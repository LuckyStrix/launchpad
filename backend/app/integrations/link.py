import time

import httpx

from .base import Field, Integration, IntegrationError, TileData, http


class Link(Integration):
    id = "link"
    name = "Link"
    description = "A website or self-hosted app, with an optional check that it's up."
    icon = "🔗"
    refresh = 60
    fields = [
        Field(key="url", label="URL", type="url", required=True, placeholder="https://"),
        Field(key="check", label="Show whether it's up", type="bool", default=True),
    ]

    async def fetch(self, config):
        url = (config.get("url") or "").strip()
        if not url:
            raise IntegrationError("Add a URL in the tile settings.")
        if not config.get("check", True):
            return TileData(url=url)

        started = time.monotonic()
        try:
            # Self-hosted apps often use self-signed certificates; this only checks reachability.
            async with http(timeout=5, verify=False, follow_redirects=True) as client:
                response = await client.get(url)
        except httpx.HTTPError:
            return TileData(url=url, status="error", detail="Not responding")

        if response.status_code >= 500:
            return TileData(url=url, status="error", detail=f"HTTP {response.status_code}")
        # 4xx still means the server is up (e.g. a login wall).
        return TileData(url=url, status="ok", detail=f"{round((time.monotonic() - started) * 1000)} ms")


integration = Link()
