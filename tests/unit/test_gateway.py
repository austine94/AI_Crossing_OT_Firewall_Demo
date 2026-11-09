from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from boundaryline.contracts import Scenario
from boundaryline.identity import issue_token
from services.broker import app as gateway_module


def install_dependencies(monkeypatch, *, policy_available: bool = True):
    calls = {"inventory": 0, "policy": 0, "plc": 0}
    events = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "inventory.invalid":
            calls["inventory"] += 1
            return httpx.Response(
                200,
                json={
                    "schema_version": "1.0",
                    "asset_id": "PLC-CRIT-001",
                    "display_name": "Critical Controller 1 (simulation)",
                    "zone": "critical",
                    "asset_type": "mock_plc",
                    "patch_status": "unknown",
                    "authorized_for_agent": False,
                    "criticality": "high",
                },
            )
        if request.url.host == "policy.invalid":
            calls["policy"] += 1
            if not policy_available:
                raise httpx.ConnectError("policy unavailable", request=request)
            return httpx.Response(
                200,
                json={
                    "result": {
                        "allow": False,
                        "reason": "CRITICAL_ZONE_REQUIRES_PRIVILEGED_ROLE",
                        "policy_version": "1.0.0",
                    }
                },
            )
        if request.url.host == "plc.invalid":
            calls["plc"] += 1
            asset_id = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(
                200,
                json={"asset_id": asset_id, "patch_status": "unknown"},
            )
        return httpx.Response(404)

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    async def capture_event(event_url, payload):
        events.append(payload)
        return True

    monkeypatch.setattr(gateway_module, "TOKEN_SECRET", "test-secret")
    monkeypatch.setattr(gateway_module, "INVENTORY_URL", "http://inventory.invalid")
    monkeypatch.setattr(gateway_module, "OPA_URL", "http://policy.invalid")
    monkeypatch.setattr(gateway_module, "PLC_URL", "http://plc.invalid")
    monkeypatch.setattr(gateway_module.httpx, "AsyncClient", MockAsyncClient)
    monkeypatch.setattr(gateway_module, "emit_event_async", capture_event)
    return calls, events


def gateway_request(scenario: Scenario) -> tuple[str, dict[str, object]]:
    run_id = uuid4()
    token = issue_token(
        subject="patch-agent-lab",
        roles=("monitoring-reader",),
        audience="boundaryline-broker",
        run_id=run_id,
        scenario=scenario,
        secret="test-secret",
    )
    return token, {
        "asset_id": "PLC-CRIT-001",
        "run_id": str(run_id),
        "correlation_id": str(uuid4()),
        "requesting_identity": "untrusted-body-hint",
    }


def test_b_authenticates_but_skips_resource_authorization(monkeypatch) -> None:
    calls, events = install_dependencies(monkeypatch)
    token, payload = gateway_request(Scenario.B)
    response = TestClient(gateway_module.app).post(
        "/v1/patch-status",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert calls == {"inventory": 0, "policy": 0, "plc": 1}
    assert [event["kind"] for event in events] == [
        "broker.request.received",
        "broker.asset_request.allowed",
    ]
    assert {event["scenario"] for event in events} == {"B"}


def test_b_supports_each_fixed_critical_plc(monkeypatch) -> None:
    calls, _ = install_dependencies(monkeypatch)
    token, payload = gateway_request(Scenario.B)
    payload["asset_id"] = "PLC-CRIT-005"
    response = TestClient(gateway_module.app).post(
        "/v1/patch-status",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["asset_id"] == "PLC-CRIT-005"
    assert calls["plc"] == 1


def test_c_denies_before_plc_using_trusted_inventory_and_opa(monkeypatch) -> None:
    calls, events = install_dependencies(monkeypatch)
    token, payload = gateway_request(Scenario.C)
    response = TestClient(gateway_module.app).post(
        "/v1/patch-status",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["reason"] == "CRITICAL_ZONE_REQUIRES_PRIVILEGED_ROLE"
    assert calls == {"inventory": 1, "policy": 1, "plc": 0}
    assert [event["kind"] for event in events] == [
        "broker.request.received",
        "policy.decision.deny",
        "broker.asset_request.denied",
    ]
    assert {event["scenario"] for event in events} == {"C"}


def test_c_fails_closed_when_opa_is_unavailable(monkeypatch) -> None:
    calls, events = install_dependencies(monkeypatch, policy_available=False)
    token, payload = gateway_request(Scenario.C)
    response = TestClient(gateway_module.app).post(
        "/v1/patch-status",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["reason"] == "POLICY_EVALUATION_FAILED_CLOSED"
    assert calls == {"inventory": 1, "policy": 1, "plc": 0}
    assert [event["kind"] for event in events] == [
        "broker.request.received",
        "policy.decision.deny",
        "broker.asset_request.denied",
    ]
