import csv
import hashlib
import io
import json
import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Query, Response, WebSocket, WebSocketDisconnect

from boundaryline.contracts import SCHEMA_VERSION, Event, EventDraft

app = FastAPI(title="BoundaryLine Events", version="0.1.0")

DB_PATH = Path(os.getenv("BOUNDARYLINE_EVENT_DB", "/data/events.db"))
subscribers: set[WebSocket] = set()

DEFAULT_CONFIG_FILES = (
    Path("/app/fixtures/assets.json"),
    Path("/app/fixtures/identities.json"),
    Path("/app/fixtures/scenarios.json"),
)


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            event_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            timestamp_utc TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            UNIQUE(run_id, sequence)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_run_sequence ON events(run_id, sequence)"
    )
    return connection


def persist(draft: EventDraft) -> Event:
    with closing(connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM events WHERE run_id = ?",
            (str(draft.run_id),),
        ).fetchone()
        event = Event(
            **draft.model_dump(),
            event_id=uuid4(),
            sequence=int(row["next_sequence"]),
            timestamp_utc=datetime.now(UTC),
        )
        payload = event.model_dump_json()
        connection.execute(
            """
            INSERT INTO events (event_id, run_id, sequence, timestamp_utc, payload_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(event.event_id),
                str(event.run_id),
                event.sequence,
                event.timestamp_utc.isoformat(),
                payload,
            ),
        )
        connection.commit()
    return event


def load_run_events(run_id: UUID, after_sequence: int = 0) -> list[Event]:
    with closing(connect()) as connection:
        rows = connection.execute(
            """
            SELECT payload_json FROM events
            WHERE run_id = ? AND sequence > ?
            ORDER BY sequence
            """,
            (str(run_id), after_sequence),
        ).fetchall()
    return [Event.model_validate(json.loads(row["payload_json"])) for row in rows]


def config_files() -> tuple[Path, ...]:
    configured = os.getenv("BOUNDARYLINE_CONFIG_FILES")
    if configured:
        return tuple(Path(value.strip()) for value in configured.split(",") if value.strip())
    return DEFAULT_CONFIG_FILES


def config_hash(paths: tuple[Path, ...] | None = None) -> str:
    """Hash the exact local fixture bytes used to define the synthetic lab."""
    digest = hashlib.sha256()
    selected = paths or config_files()
    for path in sorted(selected, key=lambda item: item.name):
        if not path.is_file():
            raise FileNotFoundError(path)
        digest.update(path.name.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def export_metadata(run_id: UUID, events: list[Event]) -> dict[str, object]:
    scenarios = sorted({event.scenario.value for event in events})
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": str(run_id),
        "synthetic": True,
        "event_count": len(events),
        "first_sequence": events[0].sequence,
        "last_sequence": events[-1].sequence,
        "started_at_utc": events[0].timestamp_utc.isoformat(),
        "finished_at_utc": events[-1].timestamp_utc.isoformat(),
        "scenarios": scenarios,
        "config_hash": config_hash(),
        "exported_at_utc": datetime.now(UTC).isoformat(),
    }


def csv_export(metadata: dict[str, object], events: list[Event]) -> str:
    output = io.StringIO(newline="")
    fieldnames = [
        "export_run_id",
        "export_config_hash",
        "export_event_count",
        "schema_version",
        "event_id",
        "run_id",
        "sequence",
        "timestamp_utc",
        "correlation_id",
        "scenario",
        "kind",
        "actor",
        "requesting_identity",
        "target_asset",
        "target_zone",
        "tool",
        "decision",
        "reason_code",
        "evidence_source",
        "origin",
        "synthetic",
        "details_json",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for event in events:
        record = event.model_dump(mode="json")
        writer.writerow(
            {
                "export_run_id": metadata["run_id"],
                "export_config_hash": metadata["config_hash"],
                "export_event_count": metadata["event_count"],
                **{key: record.get(key) for key in fieldnames if key in record},
                "details_json": json.dumps(
                    record["details"], sort_keys=True, separators=(",", ":")
                ),
            }
        )
    return output.getvalue()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/events", response_model=Event, status_code=201)
async def create_event(draft: EventDraft) -> Event:
    event = persist(draft)
    disconnected: list[WebSocket] = []
    for websocket in subscribers:
        try:
            await websocket.send_json(event.model_dump(mode="json"))
        except (WebSocketDisconnect, RuntimeError, OSError):
            disconnected.append(websocket)
    for websocket in disconnected:
        subscribers.discard(websocket)
    return event


@app.get("/v1/runs/{run_id}/events", response_model=list[Event])
def list_run_events(run_id: UUID, after_sequence: int = Query(default=0, ge=0)) -> list[Event]:
    return load_run_events(run_id, after_sequence)


@app.get("/v1/runs/{run_id}/export")
def export_run(
    run_id: UUID, format: str = Query(default="json", pattern="^(json|csv)$")
) -> Response:
    events = load_run_events(run_id)
    if not events:
        raise HTTPException(status_code=404, detail="run has no recorded events")
    try:
        metadata = export_metadata(run_id, events)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=503, detail=f"configuration file unavailable: {exc}"
        ) from exc

    filename = f"boundaryline-{run_id}.{format}"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if format == "csv":
        return Response(
            content=csv_export(metadata, events),
            media_type="text/csv; charset=utf-8",
            headers=headers,
        )
    content = json.dumps(
        {
            "metadata": metadata,
            "events": [event.model_dump(mode="json") for event in events],
        },
        indent=2,
        sort_keys=True,
    )
    return Response(content=content, media_type="application/json", headers=headers)


@app.websocket("/v1/events/ws")
async def event_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    subscribers.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        subscribers.discard(websocket)
