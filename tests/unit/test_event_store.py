import csv
import io
import json
from uuid import uuid4

from fastapi.testclient import TestClient

from boundaryline.contracts import EventDraft
from services.events import app as events_module


def event_draft(run_id, correlation_id) -> EventDraft:
    return EventDraft(
        run_id=run_id,
        correlation_id=correlation_id,
        scenario="B",
        kind="run.started",
        actor="test",
        decision="not_evaluated",
        reason_code="TEST",
        evidence_source="orchestrator",
        origin="service",
    )


def test_event_store_allocates_monotonic_sequence(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(events_module, "DB_PATH", tmp_path / "events.db")
    run_id = uuid4()
    first = events_module.persist(event_draft(run_id, uuid4()))
    second = events_module.persist(event_draft(run_id, uuid4()))
    assert first.sequence == 1
    assert second.sequence == 2


def test_sequence_is_per_run(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(events_module, "DB_PATH", tmp_path / "events.db")
    first = events_module.persist(event_draft(uuid4(), uuid4()))
    second = events_module.persist(event_draft(uuid4(), uuid4()))
    assert first.sequence == 1
    assert second.sequence == 1


def test_config_hash_is_stable_and_sensitive_to_fixture_bytes(tmp_path) -> None:
    first = tmp_path / "assets.json"
    second = tmp_path / "scenarios.json"
    first.write_text('[{"asset_id":"A"}]')
    second.write_text('[{"scenario":"B"}]')
    initial = events_module.config_hash((first, second))
    assert initial == events_module.config_hash((second, first))
    second.write_text('[{"scenario":"C"}]')
    assert events_module.config_hash((first, second)) != initial


def test_run_exports_include_metadata_config_hash_and_events(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(events_module, "DB_PATH", tmp_path / "events.db")
    fixture = tmp_path / "scenarios.json"
    fixture.write_text('[{"scenario":"B"}]')
    monkeypatch.setattr(events_module, "DEFAULT_CONFIG_FILES", (fixture,))
    run_id = uuid4()
    events_module.persist(event_draft(run_id, uuid4()))
    events_module.persist(event_draft(run_id, uuid4()))
    client = TestClient(events_module.app)

    json_response = client.get(f"/v1/runs/{run_id}/export", params={"format": "json"})
    assert json_response.status_code == 200
    exported = json_response.json()
    assert exported["metadata"]["run_id"] == str(run_id)
    assert exported["metadata"]["event_count"] == 2
    assert exported["metadata"]["config_hash"].startswith("sha256:")
    assert [event["sequence"] for event in exported["events"]] == [1, 2]

    csv_response = client.get(f"/v1/runs/{run_id}/export", params={"format": "csv"})
    assert csv_response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(csv_response.text)))
    assert len(rows) == 2
    assert rows[0]["export_run_id"] == str(run_id)
    assert rows[0]["export_config_hash"] == exported["metadata"]["config_hash"]
    assert json.loads(rows[0]["details_json"]) == {}


def test_export_returns_not_found_for_unknown_run(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(events_module, "DB_PATH", tmp_path / "events.db")
    response = TestClient(events_module.app).get(f"/v1/runs/{uuid4()}/export")
    assert response.status_code == 404
