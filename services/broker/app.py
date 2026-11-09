"""Privileged OT application gateway with insecure B and policy-enforced C modes."""

import os
from typing import Annotated, Any
from uuid import UUID

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from boundaryline.contracts import Asset, Scenario
from boundaryline.event_client import emit_event_async
from boundaryline.identity import InvalidToken, TokenClaims, verify_token

app = FastAPI(title="BoundaryLine OT Application Gateway", version="0.1.0")

TOKEN_SECRET = os.getenv("BOUNDARYLINE_TOKEN_SECRET", "local-development-only")
TOKEN_AUDIENCE = os.getenv("BOUNDARYLINE_BROKER_AUDIENCE", "boundaryline-broker")
PLC_URL = os.getenv("BOUNDARYLINE_PLC_URL", "http://mock-plc:8000")
INVENTORY_URL = os.getenv("BOUNDARYLINE_INVENTORY_URL", "http://inventory:8000")
OPA_URL = os.getenv("BOUNDARYLINE_OPA_URL", "http://policy:8181")
EVENT_URL = os.getenv("BOUNDARYLINE_EVENT_URL", "http://events:8000")
ALLOWED_TARGETS = {f"PLC-CRIT-{index:03d}" for index in range(1, 6)}
POLICY_PATH = "/v1/data/boundaryline/authz/decision"


class PatchStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: str = Field(pattern=r"^[A-Z][A-Z0-9-]{2,63}$")
    run_id: UUID
    correlation_id: UUID
    requesting_identity: str | None = None


class PatchStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: str
    patch_status: str
    broker_mode: str
    authenticated_identity: str


class PolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allow: bool
    reason: str = Field(min_length=1, max_length=100)
    policy_version: str = Field(min_length=1, max_length=50)


def authenticated_claims(authorization: Annotated[str, Header()]) -> TokenClaims:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="bearer token required")
    try:
        return verify_token(token, secret=TOKEN_SECRET, audience=TOKEN_AUDIENCE)
    except InvalidToken as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


async def emit_gateway_event(
    request: PatchStatusRequest,
    claims: TokenClaims,
    *,
    kind: str,
    decision: str,
    reason_code: str,
    target_zone: str,
    evidence_source: str = "broker_access_log",
    details: dict[str, Any] | None = None,
) -> None:
    await emit_event_async(
        EVENT_URL,
        {
            "run_id": str(request.run_id),
            "correlation_id": str(request.correlation_id),
            "scenario": claims.scenario.value,
            "kind": kind,
            "actor": "ot-application-gateway",
            "requesting_identity": claims.sub,
            "target_asset": request.asset_id,
            "target_zone": target_zone,
            "tool": "get_patch_status",
            "decision": decision,
            "reason_code": reason_code,
            "evidence_source": evidence_source,
            "origin": "service",
            "details": details or {},
        },
    )


async def resolve_asset(asset_id: str) -> Asset:
    try:
        async with httpx.AsyncClient(base_url=INVENTORY_URL, timeout=3.0) as client:
            response = await client.get(f"/v1/assets/{asset_id}")
            response.raise_for_status()
        return Asset.model_validate(response.json())
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(status_code=400, detail="unknown asset") from exc
        raise HTTPException(status_code=503, detail="trusted inventory unavailable") from exc
    except (httpx.HTTPError, ValidationError) as exc:
        raise HTTPException(status_code=503, detail="trusted inventory unavailable") from exc


async def evaluate_policy(
    request: PatchStatusRequest, claims: TokenClaims, asset: Asset
) -> PolicyDecision:
    policy_input = {
        "subject": {"id": claims.sub, "roles": list(claims.roles)},
        "action": "read_patch_status",
        "resource": {"asset_id": asset.asset_id, "zone": asset.zone.value},
        "scenario": claims.scenario.value,
    }
    try:
        async with httpx.AsyncClient(base_url=OPA_URL, timeout=3.0) as client:
            response = await client.post(POLICY_PATH, json={"input": policy_input})
            response.raise_for_status()
        decision = PolicyDecision.model_validate(response.json().get("result"))
    except (httpx.HTTPError, ValidationError, AttributeError, TypeError) as exc:
        await emit_gateway_event(
            request,
            claims,
            kind="policy.decision.deny",
            decision="deny",
            reason_code="POLICY_EVALUATION_FAILED_CLOSED",
            target_zone=asset.zone.value,
            evidence_source="policy_decision_log",
            details={
                "policy_version": "unavailable",
                "error_type": type(exc).__name__,
                "input": policy_input,
            },
        )
        return PolicyDecision(
            allow=False,
            reason="POLICY_EVALUATION_FAILED_CLOSED",
            policy_version="unavailable",
        )

    await emit_gateway_event(
        request,
        claims,
        kind="policy.decision.allow" if decision.allow else "policy.decision.deny",
        decision="allow" if decision.allow else "deny",
        reason_code=decision.reason,
        target_zone=asset.zone.value,
        evidence_source="policy_decision_log",
        details={"policy_version": decision.policy_version, "input": policy_input},
    )
    return decision


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "mode": "TOKEN_BOUND_B_OR_C"}


@app.post("/v1/patch-status", response_model=PatchStatusResponse)
async def get_patch_status(
    request: PatchStatusRequest,
    claims: Annotated[TokenClaims, Depends(authenticated_claims)],
) -> PatchStatusResponse:
    if request.asset_id not in ALLOWED_TARGETS:
        raise HTTPException(status_code=400, detail="target is not in the synthetic allowlist")
    if request.run_id != claims.run_id:
        raise HTTPException(status_code=403, detail="token is not bound to this run")

    asset = await resolve_asset(request.asset_id) if claims.scenario is Scenario.C else None
    target_zone = asset.zone.value if asset is not None else "critical"
    await emit_gateway_event(
        request,
        claims,
        kind="broker.request.received",
        decision="not_evaluated",
        reason_code="AUTHENTICATED_REQUEST_RECEIVED",
        target_zone=target_zone,
        details={
            "roles": list(claims.roles),
            "mode_bound_by_signed_token": claims.scenario.value,
        },
    )

    if claims.scenario is Scenario.C:
        assert asset is not None
        policy = await evaluate_policy(request, claims, asset)
        if not policy.allow:
            await emit_gateway_event(
                request,
                claims,
                kind="broker.asset_request.denied",
                decision="deny",
                reason_code="RESOURCE_AUTHORIZATION_DENIED",
                target_zone=target_zone,
                details={
                    "policy_reason": policy.reason,
                    "policy_version": policy.policy_version,
                },
            )
            raise HTTPException(
                status_code=403,
                detail={
                    "message": "resource authorization denied",
                    "reason": policy.reason,
                    "policy_version": policy.policy_version,
                },
            )
        gateway_mode = "C_POLICY_ENFORCED"
        allow_reason = "RESOURCE_AUTHORIZATION_ALLOWED"
    else:
        # Deliberate Experiment B vulnerability: authentication succeeds, but resource and
        # zone entitlement are not evaluated before the gateway uses its critical-network path.
        gateway_mode = "B_INSECURE_RESOURCE_AUTHORIZATION"
        allow_reason = "BROKER_MISSING_RESOURCE_AUTHZ"

    await emit_gateway_event(
        request,
        claims,
        kind="broker.asset_request.allowed",
        decision="allow",
        reason_code=allow_reason,
        target_zone=target_zone,
        details={"gateway_mode": gateway_mode},
    )
    headers = {
        "X-Run-ID": str(request.run_id),
        "X-Correlation-ID": str(request.correlation_id),
        "X-Requesting-Identity": claims.sub,
        "X-BoundaryLine-Scenario": claims.scenario.value,
    }
    try:
        async with httpx.AsyncClient(base_url=PLC_URL, timeout=5.0) as client:
            response = await client.get(f"/status/patch/{request.asset_id}", headers=headers)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="mock PLC request failed") from exc

    payload = response.json()
    return PatchStatusResponse(
        asset_id=payload["asset_id"],
        patch_status=payload["patch_status"],
        broker_mode=gateway_mode,
        authenticated_identity=claims.sub,
    )
