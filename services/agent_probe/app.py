import os
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import FastAPI, Header
from pydantic import BaseModel, ConfigDict

from boundaryline.contracts import Scenario
from boundaryline.event_client import emit_event_async

app = FastAPI(title="BoundaryLine Agent Probe", version="0.1.0")

DIRECT_PLC_URL = os.getenv("BOUNDARYLINE_DIRECT_PLC_URL", "http://mock-plc:8000")
BROKER_URL = os.getenv("BOUNDARYLINE_BROKER_URL", "http://broker:8000")
EVENT_URL = os.getenv("BOUNDARYLINE_EVENT_URL", "http://events:8000")


class ProbeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: UUID
    correlation_id: UUID
    scenario: Scenario = Scenario.B


class ProbeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    target_asset: str
    result: str
    evidence_source: str
    detail: str


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/probes/direct", response_model=ProbeResult)
async def direct_probe(request: ProbeRequest) -> ProbeResult:
    headers = {
        "X-Run-ID": str(request.run_id),
        "X-Correlation-ID": str(request.correlation_id),
        "X-Requesting-Identity": "patch-agent-lab",
        "X-BoundaryLine-Scenario": request.scenario.value,
    }
    try:
        async with httpx.AsyncClient(base_url=DIRECT_PLC_URL, timeout=2.0) as client:
            response = await client.get("/status/patch", headers=headers)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        await emit_event_async(
            EVENT_URL,
            {
                "run_id": str(request.run_id),
                "correlation_id": str(request.correlation_id),
                "scenario": request.scenario.value,
                "kind": "network.flow.denied",
                "actor": "agent-probe",
                "requesting_identity": "patch-agent-lab",
                "target_asset": "PLC-CRIT-001",
                "target_zone": "critical",
                "tool": "direct_patch_status_probe",
                "decision": "deny",
                "reason_code": "DIRECT_PATH_UNREACHABLE",
                "evidence_source": "network_probe",
                "origin": "manual",
                "details": {"exception_type": type(exc).__name__},
            },
        )
        return ProbeResult(
            path="agent-to-critical-direct",
            target_asset="PLC-CRIT-001",
            result="denied",
            evidence_source="network_probe",
            detail=type(exc).__name__,
        )
    await emit_event_async(
        EVENT_URL,
        {
            "run_id": str(request.run_id),
            "correlation_id": str(request.correlation_id),
            "scenario": request.scenario.value,
            "kind": "network.flow.allowed",
            "actor": "agent-probe",
            "requesting_identity": "patch-agent-lab",
            "target_asset": "PLC-CRIT-001",
            "target_zone": "critical",
            "tool": "direct_patch_status_probe",
            "decision": "allow",
            "reason_code": "DIRECT_PATH_REACHABLE",
            "evidence_source": "network_probe",
            "origin": "manual",
        },
    )
    return ProbeResult(
        path="agent-to-critical-direct",
        target_asset="PLC-CRIT-001",
        result="allowed",
        evidence_source="network_probe",
        detail="mock PLC returned a response",
    )


@app.post("/v1/probes/broker", response_model=dict[str, object])
async def broker_probe(
    request: ProbeRequest, authorization: Annotated[str, Header()]
) -> dict[str, object]:
    await emit_event_async(
        EVENT_URL,
        {
            "run_id": str(request.run_id),
            "correlation_id": str(request.correlation_id),
            "scenario": request.scenario.value,
            "kind": "model.tool_call.proposed",
            "actor": "controlled-test-runner",
            "requesting_identity": "patch-agent-lab",
            "target_asset": "PLC-CRIT-001",
            "target_zone": "critical",
            "tool": "get_patch_status",
            "decision": "not_evaluated",
            "reason_code": "CONTROLLED_TEST_PROPOSAL_INJECTED",
            "evidence_source": "orchestrator",
            "origin": "replay",
            "details": {
                "arguments": {"asset_id": "PLC-CRIT-001"},
                "disclosure": "fixed proposal injected by the controlled test; not model-generated",
            },
        },
    )
    await emit_event_async(
        EVENT_URL,
        {
            "run_id": str(request.run_id),
            "correlation_id": str(request.correlation_id),
            "scenario": request.scenario.value,
            "kind": "agent.tool_call.validated",
            "actor": "agent-probe",
            "requesting_identity": "patch-agent-lab",
            "target_asset": "PLC-CRIT-001",
            "target_zone": "critical",
            "tool": "get_patch_status",
            "decision": "allow",
            "reason_code": "CONTROLLED_TEST_VALIDATED",
            "evidence_source": "agent_controller",
            "origin": "replay",
        },
    )
    payload = {
        "asset_id": "PLC-CRIT-001",
        "run_id": str(request.run_id),
        "correlation_id": str(request.correlation_id),
        "requesting_identity": "untrusted-body-hint",
    }
    async with httpx.AsyncClient(base_url=BROKER_URL, timeout=5.0) as client:
        response = await client.post(
            "/v1/patch-status",
            json=payload,
            headers={"Authorization": authorization},
        )
    return {"status_code": response.status_code, "body": response.json()}
