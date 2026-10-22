"""Tile data, actions and editor pickers. Tile config is always read from the saved layout,
never from the request, so the browser can only trigger actions a tile was set up for."""

import asyncio
import json
import logging
import time
from typing import Any

import httpx
from fastapi import APIRouter, Body, HTTPException

from . import integrations, layout
from .integrations import base
from .integrations.base import Integration, IntegrationError, TileData

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["tiles"])

_cache: dict[str, tuple[float, str, int, TileData]] = {}  # tile id -> (time, config, change count, data)


def _integration(integration_id: str) -> Integration:
    found = integrations.registry.get(integration_id)
    if not found:
        raise HTTPException(404, f"No integration called {integration_id!r} is installed.")
    return found


def _tile(tile_id: str) -> tuple[layout.Tile, Integration]:
    tile = layout.find_tile(tile_id)
    if not tile:
        raise HTTPException(404, "That tile isn't in the saved layout. Save your changes first.")
    return tile, _integration(tile.integration)


LOAD_DEADLINE = 20  # seconds; the browser waits 25 for tile data, so it always hears back


async def _guard(integration: Integration, call, deadline: float | None = None) -> TileData:
    """Turn every failure into a tile the user can read. Loading data has a `deadline`; actions
    don't, since cutting a change off halfway would leave it unclear whether it happened."""
    if not integration.configured:
        return TileData(status="warn", error=f"Set {', '.join(integration.missing)} in .env to use this tile.")
    try:
        return await asyncio.wait_for(call(), deadline) or TileData()
    except TimeoutError:
        return TileData(status="error", error=f"{integration.name} took more than {deadline} seconds to answer.")
    except IntegrationError as e:
        return TileData(status="error", error=str(e))
    except httpx.HTTPError as e:
        return TileData(status="error", error=f"Network error: {e.__class__.__name__}")
    except Exception:
        log.exception("%s failed", integration.id)
        return TileData(status="error", error=f"{integration.name} hit an unexpected error. Check the server log.")


@router.get("/integrations")
def list_integrations():
    return [i.schema() for i in integrations.registry.values()]


@router.get("/integrations/{integration_id}/options/{field}")
async def field_options(integration_id: str, field: str, q: str = ""):
    integration = _integration(integration_id)
    if not integration.configured:
        raise HTTPException(400, f"Set {', '.join(integration.missing)} in .env first.")
    try:
        return await integration.options(field, q)
    except IntegrationError as e:
        raise HTTPException(502, str(e))


@router.get("/tiles/{tile_id}/data")
async def tile_data(tile_id: str) -> TileData:
    tile, integration = _tile(tile_id)
    key = json.dumps(tile.config, sort_keys=True)
    version = base.changes.get(integration.source, 0)
    cached = _cache.get(tile_id)
    ttl = max(5, integration.refresh // 2)
    if cached and cached[1:3] == (key, version) and time.monotonic() - cached[0] < ttl:
        return cached[3]
    data = await _guard(integration, lambda: integration.fetch(tile.config), LOAD_DEADLINE)
    # Only cache healthy results: after fixing a problem (signing in, setting a token, bringing
    # a service back) the tile should recover on the next refresh, not after the cache expires.
    if data.status in ("ok", "idle") and not data.error:
        _cache[tile_id] = (time.monotonic(), key, version, data)
    else:
        _cache.pop(tile_id, None)
    return data


@router.post("/tiles/{tile_id}/actions/{name}")
async def tile_action(tile_id: str, name: str, params: dict[str, Any] = Body(default={})) -> TileData:
    tile, integration = _tile(tile_id)
    handler = integration.actions().get(name)
    if not handler:
        raise HTTPException(404, f"{integration.name} tiles have no {name!r} action.")
    result = await _guard(integration, lambda: handler(tile.config, params))
    if not result.error:
        base.changed(integration.source)  # other tiles showing the same data refresh too
    return result


@router.get("/tiles/changes")
async def tile_changes() -> dict[str, int]:
    """Change counters per integration; when one goes up, tiles of that integration refetch."""
    return base.changes
