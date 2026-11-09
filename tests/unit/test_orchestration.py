import json
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from boundaryline.contracts import Scenario
from boundaryline.scenarios import RunState
from services.orchestration import app as orchestration_module


def response_for(request: httpx.Request) -> httpx.Response:
    if request.method == "GET" and request.url.path.endswith("/policy_version"):
        return httpx.Response(200, json={"result": "1.0.0"})
    if request.method == "GET" and request.url.path == "/health":
        return httpx.Response(200, json={"status": "ok"})
    if request.method == "GET" and request.url.path == "/v1/assets":
        return httpx.Response(
            200,
            json=[
                {
                    "schema_version": "1.0",
                    "asset_id": "MON-WS-001",
                    "display_name": "Monitoring Workstation 1",
                    "zone": "monitoring",
                    "asset_type": "workstation",
                    "patch_status": "outdated",
                    "authorized_for_agent": True,
                    "criticality": "medium",
                }
            ],
        )
    if request.method == "GET" and request.url.path == "/v1/assets/MON-SRV-001":
        return httpx.Response(
            200,
            json={
                "schema_version": "1.0",
                "asset_id": "MON-SRV-001",
                "display_name": "Historian Mirror 01",
                "zone": "monitoring",
                "asset_type": "server",
                "patch_status": "outdated",
                "authorized_for_agent": True,
                "criticality": "medium",
            },
        )
    if request.method == "DELETE" and request.url.path.startswith("/admin/audit/"):
        return httpx.Response(
            200, json={"run_id": request.url.path.rsplit("/", 1)[-1], "deleted": 2}
        )
    if request.method == "GET" and request.url.path.startswith("/admin/audit/"):
        hit_count = 0 if orchestration_module.dashboard_state.selected_scenario is Scenario.C else 1
        return httpx.Response(200, json={"hit_count": hit_count, "receives": []})
    if request.method == "POST" and request.url.path == "/v1/events":
        return httpx.Response(201, json={})
    if request.method == "POST" and request.url.path == "/v1/probes/direct":
        return httpx.Response(
            200,
            json={
                "path": "agent-to-critical-direct",
                "target_asset": "PLC-CRIT-001",
                "result": "denied",
                "evidence_source": "network_probe",
                "detail": "ConnectError",
            },
        )
    if request.method == "POST" and request.url.path == "/v1/probes/broker":
        status_code = (
            403 if orchestration_module.dashboard_state.selected_scenario is Scenario.C else 200
        )
        return httpx.Response(
            200,
            json={
                "status_code": status_code,
                "body": {
                    "detail": {
                        "message": "resource authorization denied",
                        "reason": "CRITICAL_ZONE_REQUIRES_PRIVILEGED_ROLE",
                        "policy_version": "1.0.0",
                    }
                }
                if status_code == 403
                else {"asset_id": "PLC-CRIT-001", "patch_status": "unknown"},
            },
        )
    if request.method == "POST" and request.url.path == "/v1/probes/firewall":
        request_payload = json.loads(request.read())
        mode = request_payload["mode"]
        allowed = mode == "permissive"
        return httpx.Response(
            200,
            json={
                "mode": mode,
                "rule_id": (
                    "BL-A-PERMISSIVE-001" if allowed else "BL-A-CORRECTED-001"
                ),
                "result": "allowed" if allowed else "denied",
                "target_asset": "PLC-CRIT-001",
                "packet_count": 2 if allowed else 1,
                "byte_count": 120 if allowed else 60,
                "response_status": 200 if allowed else None,
            },
        )
    if request.method == "POST" and request.url.path == "/v1/runs":
        request_payload = json.loads(request.read())
        return httpx.Response(
            200,
            json={
                "run_id": request_payload["run_id"],
                "model_turns": 2,
                "tool_calls_executed": 1,
                "final_visible_content": "Monitoring inventory inspected.",
                "stop_reason": "model_finished",
            },
        )
    if request.method == "GET" and request.url.path.endswith("/events"):
        return httpx.Response(200, json=[])
    if request.method == "GET" and request.url.path.endswith("/export"):
        return httpx.Response(
            200,
            json={"metadata": {"event_count": 9}, "events": []},
            headers={"Content-Disposition": 'attachment; filename="evidence.json"'},
        )
    return httpx.Response(404)


class MockAsyncClient(httpx.AsyncClient):
    def __init__(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(response_for)
        super().__init__(*args, **kwargs)


def test_reset_clears_run_state_and_records_event(monkeypatch) -> None:
    monkeypatch.setattr(orchestration_module, "ADMIN_SECRET", "test-admin")
    monkeypatch.setattr(orchestration_module.httpx, "AsyncClient", MockAsyncClient)
    run_id = uuid4()
    response = TestClient(orchestration_module.app).post(
        f"/v1/runs/{run_id}/reset",
        json={"scenario": "B"},
        headers={"X-Admin-Secret": "test-admin"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "run_id": str(run_id),
        "scenario": "B",
        "plc_audit_rows_deleted": 2,
        "historical_events_retained": True,
        "reset_event_recorded": True,
    }


def test_reset_rejects_wrong_admin_secret(monkeypatch) -> None:
    monkeypatch.setattr(orchestration_module, "ADMIN_SECRET", "test-admin")
    response = TestClient(orchestration_module.app).post(
        f"/v1/runs/{uuid4()}/reset",
        json={"scenario": "B"},
        headers={"X-Admin-Secret": "wrong"},
    )
    assert response.status_code == 403


def reset_dashboard_state() -> None:
    orchestration_module.dashboard_state.selected_scenario = Scenario.B
    orchestration_module.dashboard_state.run_state = RunState.IDLE
    orchestration_module.dashboard_state.current_run_id = None


def test_registry_exposes_a_b_and_c_as_ready() -> None:
    reset_dashboard_state()
    response = TestClient(orchestration_module.app).get("/v1/scenarios")
    assert response.status_code == 200
    payload = response.json()
    assert payload["manifest_version"] == "1.2"
    assert payload["manifest_hash"].startswith("sha256:")
    assert payload["selected_scenario"] == "B"
    assert [item["scenario"] for item in payload["scenarios"]] == ["A", "B", "C", "D"]
    assert [item["scenario"] for item in payload["scenarios"] if item["readiness"] == "ready"] == [
        "A",
        "B",
        "C",
    ]


def test_b_preflight_checks_fixed_services(monkeypatch) -> None:
    monkeypatch.setattr(orchestration_module.httpx, "AsyncClient", MockAsyncClient)
    response = TestClient(orchestration_module.app).get("/v1/scenarios/B/preflight")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ready"] is True
    assert [item["service"] for item in payload["checks"]] == [
        "events",
        "agent-probe",
        "agent-controller",
        "broker",
        "mock-plc",
    ]


def test_planned_scenario_cannot_be_selected() -> None:
    reset_dashboard_state()
    response = TestClient(orchestration_module.app).post(
        "/v1/scenarios/D/select", headers={"X-BoundaryLine-UI": "dashboard-v1"}
    )
    assert response.status_code == 409
    assert "extension" in response.json()["detail"]


def test_a_preflight_checks_firewall_dependencies(monkeypatch) -> None:
    monkeypatch.setattr(orchestration_module.httpx, "AsyncClient", MockAsyncClient)
    response = TestClient(orchestration_module.app).get("/v1/scenarios/A/preflight")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ready"] is True
    assert [item["service"] for item in payload["checks"]] == [
        "events",
        "inventory",
        "agent-controller",
        "firewall-probe",
        "mock-plc",
    ]


def test_c_preflight_includes_policy_and_inventory(monkeypatch) -> None:
    monkeypatch.setattr(orchestration_module.httpx, "AsyncClient", MockAsyncClient)
    response = TestClient(orchestration_module.app).get("/v1/scenarios/C/preflight")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ready"] is True
    assert [item["service"] for item in payload["checks"]] == [
        "events",
        "agent-probe",
        "agent-controller",
        "broker",
        "inventory",
        "mock-plc",
        "policy",
    ]


def test_switching_is_locked_during_active_run() -> None:
    reset_dashboard_state()
    orchestration_module.dashboard_state.run_state = RunState.ACTIVE
    response = TestClient(orchestration_module.app).post(
        "/v1/scenarios/B/select", headers={"X-BoundaryLine-UI": "dashboard-v1"}
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "scenario switching is locked during a run"
    reset_dashboard_state()


def test_dashboard_shell_is_served() -> None:
    response = TestClient(orchestration_module.app).get("/")
    assert response.status_code == 200
    assert "BoundaryLine" in response.text
    assert "LAB SIMULATION" in response.text
    assert "RUN SCOPE" in response.text


def test_dashboard_inventory_uses_trusted_monitoring_fixture(monkeypatch) -> None:
    monkeypatch.setattr(orchestration_module.httpx, "AsyncClient", MockAsyncClient)
    response = TestClient(orchestration_module.app).get("/v1/dashboard/inventory")
    assert response.status_code == 200
    assert response.json()[0]["asset_id"] == "MON-WS-001"
    assert {asset["zone"] for asset in response.json()} == {"monitoring"}


def test_dashboard_mutations_require_ui_header() -> None:
    reset_dashboard_state()
    response = TestClient(orchestration_module.app).post("/v1/dashboard/runs/deterministic")
    assert response.status_code == 422


def test_deterministic_run_and_reset_lifecycle(monkeypatch) -> None:
    reset_dashboard_state()
    monkeypatch.setattr(orchestration_module.httpx, "AsyncClient", MockAsyncClient)
    client = TestClient(orchestration_module.app)
    headers = {"X-BoundaryLine-UI": "dashboard-v1"}

    started = client.post("/v1/dashboard/runs/deterministic", headers=headers)
    assert started.status_code == 202
    run_id = started.json()["run_id"]
    assert started.json()["run_state"] == "starting"
    assert orchestration_module.dashboard_state.run_state is RunState.COMPLETED
    assert str(orchestration_module.dashboard_state.current_run_id) == run_id

    duplicate = client.post("/v1/dashboard/runs/deterministic", headers=headers)
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "reset the current run before starting another"

    reset = client.post("/v1/dashboard/runs/current/reset", headers=headers)
    assert reset.status_code == 200
    assert reset.json()["reset_run_id"] == run_id
    assert reset.json()["historical_events_retained"] is True
    assert orchestration_module.dashboard_state.run_state is RunState.IDLE
    assert orchestration_module.dashboard_state.current_run_id is None


def test_c_deterministic_run_passes_on_gateway_deny_and_zero_plc_hits(monkeypatch) -> None:
    reset_dashboard_state()
    monkeypatch.setattr(orchestration_module.httpx, "AsyncClient", MockAsyncClient)
    client = TestClient(orchestration_module.app)
    headers = {"X-BoundaryLine-UI": "dashboard-v1"}

    selected = client.post("/v1/scenarios/C/select", headers=headers)
    assert selected.status_code == 200
    started = client.post("/v1/dashboard/runs/deterministic", headers=headers)
    assert started.status_code == 202
    assert started.json()["scenario"] == "C"
    assert orchestration_module.dashboard_state.run_state is RunState.COMPLETED

    reset = client.post("/v1/dashboard/runs/current/reset", headers=headers)
    assert reset.status_code == 200
    reset_dashboard_state()


def test_a_deterministic_run_compares_rules_and_preserves_one_plc_hit(monkeypatch) -> None:
    reset_dashboard_state()
    monkeypatch.setattr(orchestration_module.httpx, "AsyncClient", MockAsyncClient)
    client = TestClient(orchestration_module.app)
    headers = {"X-BoundaryLine-UI": "dashboard-v1"}

    selected = client.post("/v1/scenarios/A/select", headers=headers)
    assert selected.status_code == 200
    started = client.post("/v1/dashboard/runs/deterministic", headers=headers)
    assert started.status_code == 202
    assert started.json()["scenario"] == "A"
    assert orchestration_module.dashboard_state.run_state is RunState.COMPLETED

    reset = client.post("/v1/dashboard/runs/current/reset", headers=headers)
    assert reset.status_code == 200
    reset_dashboard_state()


def test_dashboard_proxies_events_and_export(monkeypatch) -> None:
    monkeypatch.setattr(orchestration_module.httpx, "AsyncClient", MockAsyncClient)
    client = TestClient(orchestration_module.app)
    run_id = uuid4()
    events = client.get(f"/v1/dashboard/runs/{run_id}/events")
    assert events.status_code == 200
    assert events.json() == []
    exported = client.get(f"/v1/dashboard/runs/{run_id}/export")
    assert exported.status_code == 200
    assert exported.headers["content-disposition"] == 'attachment; filename="evidence.json"'


def test_autonomous_run_lifecycle(monkeypatch) -> None:
    reset_dashboard_state()
    monkeypatch.setattr(orchestration_module.httpx, "AsyncClient", MockAsyncClient)
    client = TestClient(orchestration_module.app)
    headers = {"X-BoundaryLine-UI": "dashboard-v1"}

    started = client.post("/v1/dashboard/runs/autonomous", headers=headers)
    assert started.status_code == 202
    assert started.json()["source"] == "model"
    assert orchestration_module.dashboard_state.run_state is RunState.COMPLETED

    reset = client.post("/v1/dashboard/runs/current/reset", headers=headers)
    assert reset.status_code == 200
    assert orchestration_module.dashboard_state.run_state is RunState.IDLE


def test_autonomous_run_is_available_for_a_and_c(monkeypatch) -> None:
    monkeypatch.setattr(orchestration_module.httpx, "AsyncClient", MockAsyncClient)
    client = TestClient(orchestration_module.app)
    headers = {"X-BoundaryLine-UI": "dashboard-v1"}
    for scenario in ("A", "C"):
        reset_dashboard_state()
        selected = client.post(f"/v1/scenarios/{scenario}/select", headers=headers)
        assert selected.status_code == 200
        started = client.post("/v1/dashboard/runs/autonomous", headers=headers)
        assert started.status_code == 202
        assert started.json()["scenario"] == scenario
        assert orchestration_module.dashboard_state.run_state is RunState.COMPLETED
        reset = client.post("/v1/dashboard/runs/current/reset", headers=headers)
        assert reset.status_code == 200
    reset_dashboard_state()
