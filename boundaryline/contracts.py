"""Versioned, shared data contracts for every BoundaryLine service."""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "1.0"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Zone(StrEnum):
    MANAGEMENT = "management"
    MONITORING = "monitoring"
    CRITICAL = "critical"


class Scenario(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"


class Decision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    NOT_EVALUATED = "not_evaluated"


class EventKind(StrEnum):
    RUN_STARTED = "run.started"
    RUN_FINISHED = "run.finished"
    RUN_RESET = "run.reset"
    MODEL_TOOL_CALL_PROPOSED = "model.tool_call.proposed"
    AGENT_TOOL_CALL_VALIDATED = "agent.tool_call.validated"
    AGENT_TOOL_CALL_REJECTED = "agent.tool_call.rejected"
    AGENT_TOOL_CALL_EXECUTED = "agent.tool_call.executed"
    NETWORK_FLOW_ALLOWED = "network.flow.allowed"
    NETWORK_FLOW_DENIED = "network.flow.denied"
    BROKER_REQUEST_RECEIVED = "broker.request.received"
    BROKER_ASSET_REQUEST_ALLOWED = "broker.asset_request.allowed"
    BROKER_ASSET_REQUEST_DENIED = "broker.asset_request.denied"
    POLICY_DECISION_ALLOW = "policy.decision.allow"
    POLICY_DECISION_DENY = "policy.decision.deny"
    MOCK_PLC_REQUEST_RECEIVED = "mock_plc.request.received"
    MOCK_PLC_RESPONSE_SENT = "mock_plc.response.sent"


class EvidenceSource(StrEnum):
    MODEL_RESPONSE = "model_response"
    AGENT_CONTROLLER = "agent_controller"
    NETWORK_PROBE = "network_probe"
    BROKER_ACCESS_LOG = "broker_access_log"
    POLICY_DECISION_LOG = "policy_decision_log"
    MOCK_PLC_AUDIT_LOG = "mock_plc_audit_log"
    ORCHESTRATOR = "orchestrator"


class Asset(StrictModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    asset_id: str = Field(pattern=r"^[A-Z][A-Z0-9-]{2,63}$")
    display_name: str = Field(min_length=1, max_length=120)
    zone: Zone
    asset_type: Literal["workstation", "server", "hmi", "mock_plc"]
    patch_status: Literal["current", "outdated", "unknown"]
    authorized_for_agent: bool
    criticality: Literal["low", "medium", "high"]

    @model_validator(mode="after")
    def critical_assets_are_not_agent_authorized(self) -> Self:
        if self.zone is Zone.CRITICAL and self.authorized_for_agent:
            raise ValueError("critical assets cannot be authorized for the lab agent fixture")
        return self


class Identity(StrictModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    identity_id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,63}$")
    roles: tuple[str, ...] = Field(min_length=1)
    permitted_zones: tuple[Zone, ...]
    token_audience: str = Field(min_length=1)


class EventDraft(StrictModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    run_id: UUID
    correlation_id: UUID
    scenario: Scenario
    kind: EventKind
    actor: str = Field(min_length=1, max_length=100)
    requesting_identity: str | None = None
    target_asset: str | None = None
    target_zone: Zone | None = None
    tool: str | None = None
    decision: Decision = Decision.NOT_EVALUATED
    reason_code: str = Field(min_length=1, max_length=100)
    evidence_source: EvidenceSource
    origin: Literal["model", "manual", "replay", "service"]
    synthetic: Literal[True] = True
    details: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def network_decisions_require_network_evidence(self) -> Self:
        if (
            self.kind
            in {
                EventKind.NETWORK_FLOW_ALLOWED,
                EventKind.NETWORK_FLOW_DENIED,
            }
            and self.evidence_source is not EvidenceSource.NETWORK_PROBE
        ):
            raise ValueError("network decisions require network_probe evidence")
        return self


class Event(EventDraft):
    event_id: UUID
    sequence: int = Field(ge=1)
    timestamp_utc: datetime
