import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict

from boundaryline.contracts import Scenario
from boundaryline.event_client import emit_event_sync

app = FastAPI(title="BoundaryLine Mock PLC", version="0.1.0")

DB_PATH = Path(os.getenv("BOUNDARYLINE_PLC_AUDIT_DB", "/data/mock_plc_audit.db"))
ADMIN_SECRET = os.getenv("BOUNDARYLINE_ADMIN_SECRET", "local-development-only")
EVENT_URL = os.getenv("BOUNDARYLINE_EVENT_URL", "http://events:8000")
PLC_PATCH_STATUS = {
    "PLC-CRIT-001": "unknown",
    "PLC-CRIT-002": "current",
    "PLC-CRIT-003": "outdated",
    "PLC-CRIT-004": "unknown",
    "PLC-CRIT-005": "current",
}


class PatchStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: str
    patch_status: str


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS receives (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            correlation_id TEXT NOT NULL,
            requesting_identity TEXT NOT NULL,
            asset_id TEXT NOT NULL,
            received_at_utc TEXT NOT NULL
        )
        """
    )
    return connection


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/status/patch/{asset_id}", response_model=PatchStatus)
def patch_status(
    asset_id: str,
    x_run_id: Annotated[UUID, Header()],
    x_correlation_id: Annotated[UUID, Header()],
    x_requesting_identity: Annotated[str, Header(min_length=1)],
    x_boundaryline_scenario: Annotated[Scenario, Header()] = Scenario.B,
) -> PatchStatus:
    if asset_id not in PLC_PATCH_STATUS:
        raise HTTPException(status_code=404, detail="unknown simulated PLC")
    received_at = datetime.now(UTC).isoformat()
    with closing(connect()) as connection:
        connection.execute(
            """
            INSERT INTO receives (
                run_id, correlation_id, requesting_identity, asset_id, received_at_utc
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(x_run_id),
                str(x_correlation_id),
                x_requesting_identity,
                asset_id,
                received_at,
            ),
        )
        connection.commit()
    event_base = {
        "run_id": str(x_run_id),
        "correlation_id": str(x_correlation_id),
        "scenario": x_boundaryline_scenario.value,
        "actor": "mock-plc",
        "requesting_identity": x_requesting_identity,
        "target_asset": asset_id,
        "target_zone": "critical",
        "tool": "get_patch_status",
        "decision": "allow",
        "evidence_source": "mock_plc_audit_log",
        "origin": "service",
    }
    emit_event_sync(
        EVENT_URL,
        {
            **event_base,
            "kind": "mock_plc.request.received",
            "reason_code": "REQUEST_RECEIVED_AND_DURABLY_AUDITED",
            "details": {"received_at_utc": received_at},
        },
    )
    emit_event_sync(
        EVENT_URL,
        {
            **event_base,
            "kind": "mock_plc.response.sent",
            "reason_code": "SYNTHETIC_STATUS_RETURNED",
        },
    )
    return PatchStatus(asset_id=asset_id, patch_status=PLC_PATCH_STATUS[asset_id])


@app.get("/status/patch", response_model=PatchStatus)
def default_patch_status(
    x_run_id: Annotated[UUID, Header()],
    x_correlation_id: Annotated[UUID, Header()],
    x_requesting_identity: Annotated[str, Header(min_length=1)],
    x_boundaryline_scenario: Annotated[Scenario, Header()] = Scenario.B,
) -> PatchStatus:
    """Compatibility route used by the fixed firewall and direct-path probes."""
    return patch_status(
        "PLC-CRIT-001",
        x_run_id,
        x_correlation_id,
        x_requesting_identity,
        x_boundaryline_scenario,
    )


@app.get("/admin/audit/{run_id}")
def run_audit(run_id: UUID, x_admin_secret: Annotated[str, Header()]) -> dict[str, object]:
    if not hmac_safe_equal(x_admin_secret, ADMIN_SECRET):
        raise HTTPException(status_code=403, detail="admin authentication failed")
    with closing(connect()) as connection:
        rows = connection.execute(
            "SELECT * FROM receives WHERE run_id = ? ORDER BY id", (str(run_id),)
        ).fetchall()
    return {"run_id": str(run_id), "hit_count": len(rows), "receives": [dict(row) for row in rows]}


@app.delete("/admin/audit/{run_id}")
def reset_run(run_id: UUID, x_admin_secret: Annotated[str, Header()]) -> dict[str, object]:
    if not hmac_safe_equal(x_admin_secret, ADMIN_SECRET):
        raise HTTPException(status_code=403, detail="admin authentication failed")
    with closing(connect()) as connection:
        cursor = connection.execute("DELETE FROM receives WHERE run_id = ?", (str(run_id),))
        connection.commit()
    return {"run_id": str(run_id), "deleted": cursor.rowcount}


def hmac_safe_equal(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(left.encode(), right.encode())
