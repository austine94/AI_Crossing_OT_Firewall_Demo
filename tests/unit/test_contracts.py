import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from boundaryline.contracts import (
    Asset,
    Decision,
    Event,
    EventKind,
    EvidenceSource,
    Identity,
    Scenario,
    Zone,
)
from boundaryline.scenarios import load_scenario_manifest, scenario_manifest_hash

ROOT = Path(__file__).resolve().parents[2]


def test_asset_fixture_validates() -> None:
    records = json.loads((ROOT / "fixtures/assets.json").read_text())
    assets = [Asset.model_validate(record) for record in records]
    assert len(assets) == 30
    assert sum(asset.zone.value == "monitoring" for asset in assets) == 25
    assert sum(asset.zone.value == "critical" for asset in assets) == 5
    assert sum(asset.zone is Zone.CRITICAL for asset in assets) == 5


def test_identity_fixture_validates() -> None:
    records = json.loads((ROOT / "fixtures/identities.json").read_text())
    identities = [Identity.model_validate(record) for record in records]
    assert identities[0].identity_id == "patch-agent-lab"
    assert identities[0].permitted_zones == (Zone.MONITORING,)


def test_scenario_manifest_defines_fixed_catalogue() -> None:
    path = ROOT / "fixtures/scenarios.json"
    manifest = load_scenario_manifest(path)
    assert tuple(item.scenario for item in manifest.scenarios) == (
        Scenario.A,
        Scenario.B,
        Scenario.C,
        Scenario.D,
    )
    assert tuple(item.scenario for item in manifest.scenarios if item.readiness == "ready") == (
        Scenario.A,
        Scenario.B,
        Scenario.C,
    )
    assert scenario_manifest_hash(path).startswith("sha256:")


def test_critical_asset_cannot_be_agent_authorized() -> None:
    with pytest.raises(ValidationError):
        Asset(
            asset_id="PLC-CRIT-999",
            display_name="Invalid fixture",
            zone=Zone.CRITICAL,
            asset_type="mock_plc",
            patch_status="unknown",
            authorized_for_agent=True,
            criticality="high",
        )


def test_network_decision_requires_probe_evidence() -> None:
    with pytest.raises(ValidationError):
        Event(
            event_id=uuid4(),
            run_id=uuid4(),
            sequence=1,
            correlation_id=uuid4(),
            timestamp_utc=datetime.now(UTC),
            scenario=Scenario.B,
            kind=EventKind.NETWORK_FLOW_DENIED,
            actor="agent-executor",
            decision=Decision.DENY,
            reason_code="DIRECT_PATH_UNREACHABLE",
            evidence_source=EvidenceSource.AGENT_CONTROLLER,
            origin="service",
        )


def test_valid_mock_plc_event() -> None:
    event = Event(
        event_id=uuid4(),
        run_id=uuid4(),
        sequence=1,
        correlation_id=uuid4(),
        timestamp_utc=datetime.now(UTC),
        scenario=Scenario.B,
        kind=EventKind.MOCK_PLC_REQUEST_RECEIVED,
        actor="mock-plc",
        requesting_identity="patch-agent-lab",
        target_asset="PLC-CRIT-001",
        target_zone=Zone.CRITICAL,
        tool="get_patch_status",
        decision=Decision.ALLOW,
        reason_code="REQUEST_RECEIVED",
        evidence_source=EvidenceSource.MOCK_PLC_AUDIT_LOG,
        origin="service",
    )
    assert event.synthetic is True
