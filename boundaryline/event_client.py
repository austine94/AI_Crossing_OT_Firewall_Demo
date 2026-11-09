"""Small event-client helpers shared by synthetic services."""

from typing import Any

import httpx


async def emit_event_async(event_url: str, payload: dict[str, Any]) -> bool:
    try:
        async with httpx.AsyncClient(base_url=event_url, timeout=2.0) as client:
            response = await client.post("/v1/events", json=payload)
            response.raise_for_status()
        return True
    except httpx.HTTPError:
        return False


def emit_event_sync(event_url: str, payload: dict[str, Any]) -> bool:
    try:
        with httpx.Client(base_url=event_url, timeout=2.0) as client:
            response = client.post("/v1/events", json=payload)
            response.raise_for_status()
        return True
    except httpx.HTTPError:
        return False
