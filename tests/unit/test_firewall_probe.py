import json
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from services.firewall_probe import app as firewall_module


def install_fake_firewall(monkeypatch):
    active_mode = {"value": firewall_module.FirewallMode.PERMISSIVE}
    events = []

    def install_policy(mode, target_ip):
        assert target_ip == "172.30.0.9"
        active_mode["value"] = mode
        return firewall_module.RULES[mode][0]

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            def handler(request: httpx.Request) -> httpx.Response:
                if active_mode["value"] is firewall_module.FirewallMode.CORRECTED:
                    raise httpx.ConnectError("rejected by fixed rule", request=request)
                return httpx.Response(
                    200,
                    json={"asset_id": "PLC-CRIT-001", "patch_status": "unknown"},
                )

            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    async def capture_event(event_url, payload):
        events.append(payload)
        return True

    monkeypatch.setattr(firewall_module, "resolve_fixed_target", lambda: "172.30.0.9")
    monkeypatch.setattr(firewall_module, "install_policy", install_policy)
    monkeypatch.setattr(firewall_module, "read_rule_counter", lambda rule_id: (1, 60))
    monkeypatch.setattr(firewall_module, "remove_policy", lambda: None)
    monkeypatch.setattr(firewall_module.httpx, "AsyncClient", MockAsyncClient)
    monkeypatch.setattr(firewall_module, "emit_event_async", capture_event)
    return events


def request_payload(mode: str) -> dict[str, str]:
    return {
        "run_id": str(uuid4()),
        "correlation_id": str(uuid4()),
        "mode": mode,
    }


def test_permissive_rule_records_kernel_counter_and_allow(monkeypatch) -> None:
    events = install_fake_firewall(monkeypatch)
    response = TestClient(firewall_module.app).post(
        "/v1/probes/firewall", json=request_payload("permissive")
    )

    assert response.status_code == 200
    assert response.json() == {
        "mode": "permissive",
        "rule_id": "BL-A-PERMISSIVE-001",
        "result": "allowed",
        "target_asset": "PLC-CRIT-001",
        "packet_count": 1,
        "byte_count": 60,
        "response_status": 200,
    }
    assert events[0]["kind"] == "network.flow.allowed"
    assert events[0]["details"]["rule_id"] == "BL-A-PERMISSIVE-001"


def test_corrected_rule_records_kernel_counter_and_deny(monkeypatch) -> None:
    events = install_fake_firewall(monkeypatch)
    response = TestClient(firewall_module.app).post(
        "/v1/probes/firewall", json=request_payload("corrected")
    )

    assert response.status_code == 200
    assert response.json()["result"] == "denied"
    assert response.json()["response_status"] is None
    assert events[0]["kind"] == "network.flow.denied"
    assert events[0]["reason_code"] == "CORRECTED_FIREWALL_RULE_DENIED"


def test_counter_parser_reads_the_named_rule(monkeypatch) -> None:
    payload = {
        "nftables": [
            {
                "rule": {
                    "comment": "BL-A-CORRECTED-001",
                    "expr": [{"counter": {"packets": 3, "bytes": 180}}],
                }
            }
        ]
    }
    monkeypatch.setattr(firewall_module, "run_nft", lambda arguments: json.dumps(payload))
    assert firewall_module.read_rule_counter("BL-A-CORRECTED-001") == (3, 180)
