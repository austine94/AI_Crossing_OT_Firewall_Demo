import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query

from boundaryline.contracts import Asset, Zone

app = FastAPI(title="BoundaryLine Inventory", version="0.1.0")


@lru_cache
def load_assets() -> tuple[Asset, ...]:
    fixture_path = Path(os.getenv("BOUNDARYLINE_ASSET_FIXTURE", "/fixtures/assets.json"))
    if not fixture_path.exists():
        fixture_path = Path(__file__).resolve().parents[2] / "fixtures/assets.json"
    records = json.loads(fixture_path.read_text())
    return tuple(Asset.model_validate(record) for record in records)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/assets", response_model=list[Asset])
def list_assets(zone: Annotated[Zone | None, Query()] = None) -> list[Asset]:
    return [asset for asset in load_assets() if zone is None or asset.zone is zone]


@app.get("/v1/assets/{asset_id}", response_model=Asset)
def get_asset(asset_id: str) -> Asset:
    for asset in load_assets():
        if asset.asset_id == asset_id:
            return asset
    raise HTTPException(status_code=404, detail="unknown asset")
