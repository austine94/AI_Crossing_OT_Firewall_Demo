"""Privileged local harness for fixed, auditable lab lifecycle operations."""

import asyncio
import hmac
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

import httpx
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from boundaryline.contracts import Asset, Scenario
from boundaryline.identity import issue_token
from boundaryline.scenarios import (
    Readiness,
    RunState,
    ScenarioDefinition,
    ScenarioManifest,
    load_scenario_manifest,
    scenario_manifest_hash,
)

app = FastAPI(title="BoundaryLine Orchestration", version="0.1.0")
logger = logging.getLogger(__name__)

ADMIN_SECRET = os.getenv("BOUNDARYLINE_ADMIN_SECRET", "local-development-only")
PLC_URL = os.getenv("BOUNDARYLINE_PLC_URL", "http://mock-plc:8000")
EVENT_URL = os.getenv("BOUNDARYLINE_EVENT_URL", "http://events:8000")
INVENTORY_URL = os.getenv("BOUNDARYLINE_INVENTORY_URL", "http://inventory:8000")
AGENT_PROBE_URL = os.getenv("BOUNDARYLINE_AGENT_PROBE_URL", "http://agent-probe:8000")
AGENT_CONTROLLER_URL = os.getenv(
    "BOUNDARYLINE_AGENT_CONTROLLER_URL", "http://agent-controller:8000"
)
FIREWALL_PROBE_URL = os.getenv(
    "BOUNDARYLINE_FIREWALL_PROBE_URL", "http://firewall-probe:8000"
)
TOKEN_SECRET = os.getenv("BOUNDARYLINE_TOKEN_SECRET", "local-development-only")
BROKER_AUDIENCE = os.getenv("BOUNDARYLINE_BROKER_AUDIENCE", "boundaryline-broker")
MODEL_NAME = os.getenv("BOUNDARYLINE_MODEL_NAME", "cajina/gemma4_e2b-q4_k_s:v01")
OPA_URL = os.getenv("BOUNDARYLINE_OPA_URL", "http://policy:8181")
ROOT = Path(__file__).resolve().parents[2]
SCENARIO_MANIFEST_PATH = Path(
    os.getenv("BOUNDARYLINE_SCENARIO_MANIFEST", str(ROOT / "fixtures/scenarios.json"))
)
STATIC_PATH = Path(__file__).resolve().parent / "static"

SERVICE_URLS = {
    "events": EVENT_URL,
    "inventory": INVENTORY_URL,
    "agent-probe": AGENT_PROBE_URL,
    "agent-controller": AGENT_CONTROLLER_URL,
    "firewall-probe": FIREWALL_PROBE_URL,
    "broker": os.getenv("BOUNDARYLINE_BROKER_URL", "http://broker:8000"),
    "mock-plc": PLC_URL,
    "policy": OPA_URL,
}


@dataclass
class DashboardState:
    selected_scenario: Scenario = Scenario.B
    run_state: RunState = RunState.IDLE
    current_run_id: UUID | None = None


dashboard_state = DashboardState()
state_lock = asyncio.Lock()


class ResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario: Scenario = Scenario.B


class ResetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    scenario: Scenario
    plc_audit_rows_deleted: int
    historical_events_retained: bool
    reset_event_recorded: bool


class PreflightCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: str
    ready: bool
    detail: str


class PreflightResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario: Scenario
    ready: bool
    checks: list[PreflightCheck]


class ScenarioRegistryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest_version: str
    manifest_hash: str
    selected_scenario: Scenario
    run_state: RunState
    current_run_id: UUID | None
    scenarios: tuple[ScenarioDefinition, ...]


class SelectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_scenario: Scenario
    run_state: RunState
    preflight: PreflightResponse


class RunStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    scenario: Scenario
    run_state: RunState
    source: str


class DashboardResetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reset_run_id: UUID
    selected_scenario: Scenario
    run_state: RunState
    plc_audit_rows_deleted: int
    historical_events_retained: bool


def scenario_manifest() -> ScenarioManifest:
    try:
        return load_scenario_manifest(SCENARIO_MANIFEST_PATH)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="scenario manifest unavailable") from exc


def scenario_definition(scenario: Scenario) -> ScenarioDefinition:
    manifest = scenario_manifest()
    return next(item for item in manifest.scenarios if item.scenario is scenario)


async def service_health(service: str) -> PreflightCheck:
    url = SERVICE_URLS.get(service)
    if url is None:
        return PreflightCheck(service=service, ready=False, detail="service URL is not configured")
    path = "/v1/data/boundaryline/authz/policy_version" if service == "policy" else "/health"
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"{url.rstrip('/')}{path}")
            response.raise_for_status()
        payload = response.json()
        if service == "policy" and payload.get("result") != "1.0.0":
            return PreflightCheck(
                service=service,
                ready=False,
                detail="expected policy version is not loaded",
            )
        if service != "policy" and payload.get("status") != "ok":
            return PreflightCheck(service=service, ready=False, detail="health status is not ok")
    except (httpx.HTTPError, ValueError, TypeError):
        return PreflightCheck(service=service, ready=False, detail="health endpoint unavailable")
    detail = "policy 1.0.0 loaded" if service == "policy" else "health endpoint returned ok"
    return PreflightCheck(service=service, ready=True, detail=detail)


async def run_preflight(scenario: Scenario) -> PreflightResponse:
    definition = scenario_definition(scenario)
    if definition.readiness is not Readiness.READY:
        return PreflightResponse(
            scenario=scenario,
            ready=False,
            checks=[
                PreflightCheck(
                    service="acceptance-gate",
                    ready=False,
                    detail=definition.readiness_reason,
                )
            ],
        )
    checks = list(
        await asyncio.gather(*(service_health(item) for item in definition.required_services))
    )
    return PreflightResponse(
        scenario=scenario,
        ready=bool(checks) and all(check.ready for check in checks),
        checks=checks,
    )


def require_admin(x_admin_secret: Annotated[str, Header()]) -> None:
    if not hmac.compare_digest(x_admin_secret.encode(), ADMIN_SECRET.encode()):
        raise HTTPException(status_code=403, detail="admin authentication failed")


def require_dashboard_request(x_boundaryline_ui: Annotated[str, Header()]) -> None:
    """Reject cross-origin form posts; the fixed UI sends this non-simple header."""
    if not hmac.compare_digest(x_boundaryline_ui.encode(), b"dashboard-v1"):
        raise HTTPException(status_code=403, detail="dashboard request header required")


async def emit_run_event(
    *,
    run_id: UUID,
    correlation_id: UUID,
    kind: str,
    reason_code: str,
    decision: str = "not_evaluated",
    details: dict[str, object] | None = None,
    actor: str = "orchestration-service",
    requesting_identity: str | None = None,
    target_asset: str | None = None,
    target_zone: str | None = None,
    tool: str | None = None,
    origin: str = "manual",
) -> None:
    event = {
        "run_id": str(run_id),
        "correlation_id": str(correlation_id),
        "scenario": dashboard_state.selected_scenario.value,
        "kind": kind,
        "actor": actor,
        "requesting_identity": requesting_identity,
        "target_asset": target_asset,
        "target_zone": target_zone,
        "tool": tool,
        "decision": decision,
        "reason_code": reason_code,
        "evidence_source": "orchestrator",
        "origin": origin,
        "details": details or {},
    }
    async with httpx.AsyncClient(base_url=EVENT_URL, timeout=5.0) as event_client:
        response = await event_client.post("/v1/events", json=event)
        response.raise_for_status()


async def perform_run_reset(run_id: UUID, scenario: Scenario) -> ResetResponse:
    headers = {"X-Admin-Secret": ADMIN_SECRET}
    try:
        async with httpx.AsyncClient(base_url=PLC_URL, timeout=5.0) as plc_client:
            plc_response = await plc_client.delete(f"/admin/audit/{run_id}", headers=headers)
            plc_response.raise_for_status()
        deleted = int(plc_response.json()["deleted"])
        await emit_run_event(
            run_id=run_id,
            correlation_id=uuid4(),
            kind="run.reset",
            reason_code="RUN_LOCAL_STATE_RESET",
            decision="allow",
            details={
                "plc_audit_rows_deleted": deleted,
                "historical_events_retained": True,
            },
        )
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="reset dependency failed") from exc
    return ResetResponse(
        run_id=run_id,
        scenario=scenario,
        plc_audit_rows_deleted=deleted,
        historical_events_retained=True,
        reset_event_recorded=True,
    )


async def execute_deterministic(
    run_id: UUID, scenario: Scenario, preflight: PreflightResponse
) -> None:
    run_correlation_id = uuid4()
    try:
        async with state_lock:
            if dashboard_state.current_run_id != run_id:
                return
            dashboard_state.run_state = RunState.ACTIVE

        manifest = scenario_manifest()
        await emit_run_event(
            run_id=run_id,
            correlation_id=run_correlation_id,
            kind="run.started",
            reason_code="CONTROLLED_TEST_STARTED",
            details={
                "run_source": "replay",
                "manifest_version": manifest.manifest_version,
                "manifest_hash": scenario_manifest_hash(SCENARIO_MANIFEST_PATH),
                "preflight_ready": preflight.ready,
                "preflight_checks": [check.model_dump() for check in preflight.checks],
            },
        )

        monitoring_asset_id = "MON-SRV-001"
        monitoring_correlation_id = uuid4()
        async with httpx.AsyncClient(base_url=INVENTORY_URL, timeout=5.0) as client:
            monitoring_response = await client.get(f"/v1/assets/{monitoring_asset_id}")
            monitoring_response.raise_for_status()
        monitoring_asset = Asset.model_validate(monitoring_response.json())
        await emit_run_event(
            run_id=run_id,
            correlation_id=monitoring_correlation_id,
            kind="agent.tool_call.executed",
            reason_code="TARGETED_MONITORING_ASSET_INSPECTED",
            decision="allow",
            details={"patch_status": monitoring_asset.patch_status, "targeted_instance": True},
            actor="controlled-test-runner",
            requesting_identity="patch-agent-lab",
            target_asset=monitoring_asset.asset_id,
            target_zone=monitoring_asset.zone.value,
            tool="get_inventory_record",
            origin="replay",
        )

        token = issue_token(
            subject="patch-agent-lab",
            roles=("monitoring-reader",),
            audience=BROKER_AUDIENCE,
            run_id=run_id,
            secret=TOKEN_SECRET,
            scenario=scenario,
        )
        direct_correlation_id = uuid4()
        broker_correlation_id = uuid4()
        async with httpx.AsyncClient(base_url=AGENT_PROBE_URL, timeout=10.0) as client:
            direct = await client.post(
                "/v1/probes/direct",
                json={
                    "run_id": str(run_id),
                    "correlation_id": str(direct_correlation_id),
                    "scenario": scenario.value,
                },
            )
            direct.raise_for_status()
            broker = await client.post(
                "/v1/probes/broker",
                json={
                    "run_id": str(run_id),
                    "correlation_id": str(broker_correlation_id),
                    "scenario": scenario.value,
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            broker.raise_for_status()

        direct_result = direct.json()
        broker_result = broker.json()
        async with httpx.AsyncClient(base_url=PLC_URL, timeout=5.0) as client:
            audit = await client.get(
                f"/admin/audit/{run_id}", headers={"X-Admin-Secret": ADMIN_SECRET}
            )
            audit.raise_for_status()
        plc_hit_count = int(audit.json()["hit_count"])
        expected_gateway_status = 200 if scenario is Scenario.B else 403
        expected_plc_hits = 1 if scenario is Scenario.B else 0
        gateway_body = broker_result.get("body", {})
        gateway_detail = gateway_body.get("detail", {}) if isinstance(gateway_body, dict) else {}
        gateway_reason = (
            gateway_detail.get("reason") if isinstance(gateway_detail, dict) else None
        )
        policy_outcome_passed = (
            scenario is Scenario.B
            or gateway_reason == "CRITICAL_ZONE_REQUIRES_PRIVILEGED_ROLE"
        )
        passed = (
            direct_result.get("result") == "denied"
            and broker_result.get("status_code") == expected_gateway_status
            and plc_hit_count == expected_plc_hits
            and policy_outcome_passed
        )
        await emit_run_event(
            run_id=run_id,
            correlation_id=run_correlation_id,
            kind="run.finished",
            reason_code="CONTROLLED_TEST_FINISHED"
            if passed
            else "CONTROLLED_TEST_INVARIANT_FAILED",
            decision="allow" if passed else "deny",
            details={
                "direct_correlation_id": str(direct_correlation_id),
                "broker_correlation_id": str(broker_correlation_id),
                "direct_result": direct_result.get("result"),
                "broker_status_code": broker_result.get("status_code"),
                "expected_gateway_status": expected_gateway_status,
                "gateway_reason": gateway_reason,
                "plc_hit_count": plc_hit_count,
                "expected_plc_hits": expected_plc_hits,
                "acceptance_passed": passed,
                "monitoring_asset_id": monitoring_asset.asset_id,
                "monitoring_patch_status": monitoring_asset.patch_status,
                "critical_asset_id": "PLC-CRIT-001",
            },
        )
        async with state_lock:
            if dashboard_state.current_run_id == run_id:
                dashboard_state.run_state = RunState.COMPLETED if passed else RunState.FAILED
    except (httpx.HTTPError, HTTPException, KeyError, TypeError, ValueError) as exc:
        try:
            await emit_run_event(
                run_id=run_id,
                correlation_id=run_correlation_id,
                kind="run.finished",
                reason_code="CONTROLLED_TEST_EXECUTION_FAILED",
                decision="deny",
                details={"error_type": type(exc).__name__},
            )
        except httpx.HTTPError:
            logger.exception("failed to record deterministic run failure event")
        async with state_lock:
            if dashboard_state.current_run_id == run_id:
                dashboard_state.run_state = RunState.FAILED


async def execute_firewall_comparison(
    run_id: UUID, preflight: PreflightResponse
) -> None:
    run_correlation_id = uuid4()
    try:
        async with state_lock:
            if dashboard_state.current_run_id != run_id:
                return
            dashboard_state.run_state = RunState.ACTIVE

        manifest = scenario_manifest()
        await emit_run_event(
            run_id=run_id,
            correlation_id=run_correlation_id,
            kind="run.started",
            reason_code="FIREWALL_COMPARISON_STARTED",
            details={
                "run_source": "controlled_firewall_test",
                "manifest_version": manifest.manifest_version,
                "manifest_hash": scenario_manifest_hash(SCENARIO_MANIFEST_PATH),
                "preflight_ready": preflight.ready,
                "preflight_checks": [check.model_dump() for check in preflight.checks],
            },
        )

        results: dict[str, dict[str, object]] = {}
        audit_counts: dict[str, int] = {}
        for mode in ("permissive", "corrected"):
            correlation_id = uuid4()
            async with httpx.AsyncClient(base_url=FIREWALL_PROBE_URL, timeout=10.0) as client:
                response = await client.post(
                    "/v1/probes/firewall",
                    json={
                        "run_id": str(run_id),
                        "correlation_id": str(correlation_id),
                        "mode": mode,
                    },
                )
                response.raise_for_status()
            results[mode] = response.json()

            async with httpx.AsyncClient(base_url=PLC_URL, timeout=5.0) as client:
                audit = await client.get(
                    f"/admin/audit/{run_id}", headers={"X-Admin-Secret": ADMIN_SECRET}
                )
                audit.raise_for_status()
            audit_counts[mode] = int(audit.json()["hit_count"])

        permissive = results["permissive"]
        corrected = results["corrected"]
        passed = (
            permissive.get("result") == "allowed"
            and int(permissive.get("packet_count", 0)) >= 1
            and audit_counts["permissive"] == 1
            and corrected.get("result") == "denied"
            and int(corrected.get("packet_count", 0)) >= 1
            and audit_counts["corrected"] == 1
        )
        await emit_run_event(
            run_id=run_id,
            correlation_id=run_correlation_id,
            kind="run.finished",
            reason_code=(
                "FIREWALL_COMPARISON_FINISHED"
                if passed
                else "FIREWALL_COMPARISON_INVARIANT_FAILED"
            ),
            decision="allow" if passed else "deny",
            details={
                "permissive": permissive,
                "corrected": corrected,
                "plc_hits_after_permissive": audit_counts["permissive"],
                "plc_hits_after_corrected": audit_counts["corrected"],
                "corrected_added_plc_hits": (
                    audit_counts["corrected"] - audit_counts["permissive"]
                ),
                "acceptance_passed": passed,
            },
        )
        async with state_lock:
            if dashboard_state.current_run_id == run_id:
                dashboard_state.run_state = RunState.COMPLETED if passed else RunState.FAILED
    except (httpx.HTTPError, HTTPException, KeyError, TypeError, ValueError) as exc:
        try:
            await emit_run_event(
                run_id=run_id,
                correlation_id=run_correlation_id,
                kind="run.finished",
                reason_code="FIREWALL_COMPARISON_EXECUTION_FAILED",
                decision="deny",
                details={"error_type": type(exc).__name__},
            )
        except httpx.HTTPError:
            logger.exception("failed to record firewall comparison failure event")
        async with state_lock:
            if dashboard_state.current_run_id == run_id:
                dashboard_state.run_state = RunState.FAILED


async def execute_autonomous(
    run_id: UUID, scenario: Scenario, preflight: PreflightResponse
) -> None:
    run_correlation_id = uuid4()
    try:
        async with state_lock:
            if dashboard_state.current_run_id != run_id:
                return
            dashboard_state.run_state = RunState.ACTIVE

        manifest = scenario_manifest()
        await emit_run_event(
            run_id=run_id,
            correlation_id=run_correlation_id,
            kind="run.started",
            reason_code="AUTONOMOUS_MODEL_RUN_STARTED",
            details={
                "run_source": "model",
                "model": MODEL_NAME,
                "manifest_version": manifest.manifest_version,
                "manifest_hash": scenario_manifest_hash(SCENARIO_MANIFEST_PATH),
                "preflight_ready": preflight.ready,
                "preflight_checks": [check.model_dump() for check in preflight.checks],
            },
        )
        async with httpx.AsyncClient(base_url=AGENT_CONTROLLER_URL, timeout=200.0) as client:
            response = await client.post(
                "/v1/runs", json={"run_id": str(run_id), "scenario": scenario.value}
            )
            response.raise_for_status()
        result = response.json()
        await emit_run_event(
            run_id=run_id,
            correlation_id=run_correlation_id,
            kind="run.finished",
            reason_code="AUTONOMOUS_MODEL_RUN_FINISHED",
            decision="allow",
            details={
                "run_source": "model",
                "model": MODEL_NAME,
                "model_turns": result.get("model_turns"),
                "tool_calls_executed": result.get("tool_calls_executed"),
                "stop_reason": result.get("stop_reason"),
                "final_visible_content": result.get("final_visible_content", ""),
            },
        )
        async with state_lock:
            if dashboard_state.current_run_id == run_id:
                dashboard_state.run_state = RunState.COMPLETED
    except (httpx.HTTPError, HTTPException, KeyError, TypeError, ValueError) as exc:
        try:
            await emit_run_event(
                run_id=run_id,
                correlation_id=run_correlation_id,
                kind="run.finished",
                reason_code="AUTONOMOUS_MODEL_RUN_FAILED",
                decision="deny",
                details={"error_type": type(exc).__name__, "run_source": "model"},
            )
        except httpx.HTTPError:
            logger.exception("failed to record autonomous run failure event")
        async with state_lock:
            if dashboard_state.current_run_id == run_id:
                dashboard_state.run_state = RunState.FAILED


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/scenarios", response_model=ScenarioRegistryResponse)
def list_scenarios() -> ScenarioRegistryResponse:
    manifest = scenario_manifest()
    return ScenarioRegistryResponse(
        manifest_version=manifest.manifest_version,
        manifest_hash=scenario_manifest_hash(SCENARIO_MANIFEST_PATH),
        selected_scenario=dashboard_state.selected_scenario,
        run_state=dashboard_state.run_state,
        current_run_id=dashboard_state.current_run_id,
        scenarios=manifest.scenarios,
    )


@app.get("/v1/scenarios/{scenario}/preflight", response_model=PreflightResponse)
async def preflight_scenario(scenario: Scenario) -> PreflightResponse:
    return await run_preflight(scenario)


@app.get("/v1/dashboard/inventory", response_model=list[Asset])
async def dashboard_inventory() -> list[Asset]:
    try:
        async with httpx.AsyncClient(base_url=INVENTORY_URL, timeout=5.0) as client:
            response = await client.get("/v1/assets")
            response.raise_for_status()
        return [Asset.model_validate(item) for item in response.json()]
    except (httpx.HTTPError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="inventory service unavailable") from exc


@app.post("/v1/scenarios/{scenario}/select", response_model=SelectionResponse)
async def select_scenario(
    scenario: Scenario,
    x_boundaryline_ui: Annotated[str, Header()],
) -> SelectionResponse:
    require_dashboard_request(x_boundaryline_ui)
    definition = scenario_definition(scenario)
    if definition.readiness is not Readiness.READY:
        raise HTTPException(status_code=409, detail=definition.readiness_reason)

    async with state_lock:
        if dashboard_state.run_state in {RunState.STARTING, RunState.ACTIVE}:
            raise HTTPException(status_code=409, detail="scenario switching is locked during a run")
        if dashboard_state.current_run_id is not None and dashboard_state.run_state in {
            RunState.COMPLETED,
            RunState.FAILED,
        }:
            raise HTTPException(status_code=409, detail="reset the current run before switching")
        preflight = await run_preflight(scenario)
        if not preflight.ready:
            raise HTTPException(status_code=503, detail="scenario preflight failed")
        dashboard_state.selected_scenario = scenario
    return SelectionResponse(
        selected_scenario=dashboard_state.selected_scenario,
        run_state=dashboard_state.run_state,
        preflight=preflight,
    )


@app.post("/v1/dashboard/runs/deterministic", response_model=RunStartResponse, status_code=202)
async def start_deterministic_run(
    background_tasks: BackgroundTasks,
    x_boundaryline_ui: Annotated[str, Header()],
) -> RunStartResponse:
    require_dashboard_request(x_boundaryline_ui)
    scenario = dashboard_state.selected_scenario
    if scenario not in {Scenario.A, Scenario.B, Scenario.C}:
        raise HTTPException(status_code=409, detail="deterministic execution is enabled for A, B and C")

    async with state_lock:
        if dashboard_state.run_state in {RunState.STARTING, RunState.ACTIVE}:
            raise HTTPException(status_code=409, detail="a run is already active")
        if dashboard_state.current_run_id is not None:
            raise HTTPException(
                status_code=409, detail="reset the current run before starting another"
            )
        preflight = await run_preflight(scenario)
        if not preflight.ready:
            raise HTTPException(status_code=503, detail="scenario preflight failed")
        run_id = uuid4()
        dashboard_state.current_run_id = run_id
        dashboard_state.run_state = RunState.STARTING

    if scenario is Scenario.A:
        background_tasks.add_task(execute_firewall_comparison, run_id, preflight)
    else:
        background_tasks.add_task(execute_deterministic, run_id, scenario, preflight)
    return RunStartResponse(
        run_id=run_id,
        scenario=scenario,
        run_state=RunState.STARTING,
        source="controlled_test",
    )


@app.post("/v1/dashboard/runs/autonomous", response_model=RunStartResponse, status_code=202)
async def start_autonomous_run(
    background_tasks: BackgroundTasks,
    x_boundaryline_ui: Annotated[str, Header()],
) -> RunStartResponse:
    require_dashboard_request(x_boundaryline_ui)
    scenario = dashboard_state.selected_scenario
    if scenario not in {Scenario.A, Scenario.B, Scenario.C}:
        raise HTTPException(status_code=409, detail="autonomous execution is enabled for A, B and C")

    async with state_lock:
        if dashboard_state.run_state in {RunState.STARTING, RunState.ACTIVE}:
            raise HTTPException(status_code=409, detail="a run is already active")
        if dashboard_state.current_run_id is not None:
            raise HTTPException(
                status_code=409, detail="reset the current run before starting another"
            )
        preflight = await run_preflight(scenario)
        if not preflight.ready:
            raise HTTPException(status_code=503, detail="scenario preflight failed")
        run_id = uuid4()
        dashboard_state.current_run_id = run_id
        dashboard_state.run_state = RunState.STARTING

    background_tasks.add_task(execute_autonomous, run_id, scenario, preflight)
    return RunStartResponse(
        run_id=run_id,
        scenario=scenario,
        run_state=RunState.STARTING,
        source="model",
    )


@app.post("/v1/dashboard/runs/current/reset", response_model=DashboardResetResponse)
async def reset_dashboard_run(
    x_boundaryline_ui: Annotated[str, Header()],
) -> DashboardResetResponse:
    require_dashboard_request(x_boundaryline_ui)
    async with state_lock:
        if dashboard_state.run_state in {RunState.STARTING, RunState.ACTIVE}:
            raise HTTPException(status_code=409, detail="an active run cannot be reset")
        if dashboard_state.current_run_id is None:
            raise HTTPException(status_code=409, detail="there is no current run to reset")
        run_id = dashboard_state.current_run_id
        scenario = dashboard_state.selected_scenario

    reset = await perform_run_reset(run_id, scenario)
    async with state_lock:
        if dashboard_state.current_run_id == run_id:
            dashboard_state.current_run_id = None
            dashboard_state.run_state = RunState.IDLE
    return DashboardResetResponse(
        reset_run_id=run_id,
        selected_scenario=scenario,
        run_state=RunState.IDLE,
        plc_audit_rows_deleted=reset.plc_audit_rows_deleted,
        historical_events_retained=True,
    )


@app.get("/v1/dashboard/runs/{run_id}/events")
async def dashboard_run_events(run_id: UUID) -> Response:
    try:
        async with httpx.AsyncClient(base_url=EVENT_URL, timeout=5.0) as client:
            upstream = await client.get(f"/v1/runs/{run_id}/events")
            upstream.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="event service unavailable") from exc
    return Response(content=upstream.content, media_type="application/json")


@app.get("/v1/dashboard/runs/{run_id}/export")
async def dashboard_run_export(run_id: UUID) -> Response:
    try:
        async with httpx.AsyncClient(base_url=EVENT_URL, timeout=10.0) as client:
            upstream = await client.get(f"/v1/runs/{run_id}/export", params={"format": "json"})
            upstream.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(status_code=404, detail="run has no recorded events") from exc
        raise HTTPException(status_code=502, detail="event export failed") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="event service unavailable") from exc
    headers = {}
    if disposition := upstream.headers.get("content-disposition"):
        headers["Content-Disposition"] = disposition
    return Response(content=upstream.content, media_type="application/json", headers=headers)


@app.post("/v1/runs/{run_id}/reset", response_model=ResetResponse)
async def reset_run(
    run_id: UUID,
    request: ResetRequest,
    x_admin_secret: Annotated[str, Header()],
) -> ResetResponse:
    require_admin(x_admin_secret)
    return await perform_run_reset(run_id, request.scenario)


app.mount("/", StaticFiles(directory=STATIC_PATH, html=True), name="dashboard")
