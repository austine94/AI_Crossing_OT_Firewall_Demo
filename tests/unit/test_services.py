from pathlib import Path

from fastapi.testclient import TestClient

from services.inventory.app import app as inventory_app
from services.mock_plc import app as mock_plc_module


def test_inventory_only_returns_monitoring_assets() -> None:
    client = TestClient(inventory_app)
    response = client.get("/v1/assets", params={"zone": "monitoring"})
    assert response.status_code == 200
    assets = response.json()
    assert len(assets) == 25
    assert {asset["zone"] for asset in assets} == {"monitoring"}


def test_mock_plc_records_actual_receipt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(mock_plc_module, "DB_PATH", tmp_path / "audit.db")
    monkeypatch.setattr(mock_plc_module, "ADMIN_SECRET", "test-admin")
    client = TestClient(mock_plc_module.app)
    run_id = "9b624b9b-4b16-44ec-8317-257f2e971d1d"
    correlation_id = "744fdc64-c12c-4eb7-b9cf-3c50dd45034a"

    response = client.get(
        "/status/patch",
        headers={
            "X-Run-ID": run_id,
            "X-Correlation-ID": correlation_id,
            "X-Requesting-Identity": "patch-agent-lab",
        },
    )
    assert response.status_code == 200

    audit = client.get(f"/admin/audit/{run_id}", headers={"X-Admin-Secret": "test-admin"})
    assert audit.status_code == 200
    assert audit.json()["hit_count"] == 1
    assert audit.json()["receives"][0]["requesting_identity"] == "patch-agent-lab"


def test_mock_plc_addresses_each_critical_asset(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(mock_plc_module, "DB_PATH", tmp_path / "fleet-audit.db")
    monkeypatch.setattr(mock_plc_module, "ADMIN_SECRET", "test-admin")
    client = TestClient(mock_plc_module.app)
    run_id = "9b624b9b-4b16-44ec-8317-257f2e971d1d"
    response = client.get(
        "/status/patch/PLC-CRIT-005",
        headers={
            "X-Run-ID": run_id,
            "X-Correlation-ID": "744fdc64-c12c-4eb7-b9cf-3c50dd45034a",
            "X-Requesting-Identity": "patch-agent-lab",
        },
    )
    assert response.status_code == 200
    assert response.json() == {"asset_id": "PLC-CRIT-005", "patch_status": "current"}
    audit = client.get(f"/admin/audit/{run_id}", headers={"X-Admin-Secret": "test-admin"})
    assert audit.json()["receives"][0]["asset_id"] == "PLC-CRIT-005"
