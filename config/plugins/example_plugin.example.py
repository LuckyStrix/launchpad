"""Example plugin. Copy it to config/plugins/uptime.py (the loader skips *.example.py files)
and restart Launchpad. A plugin is any module that defines `integration = SomeIntegration()`;
see docs/adding-an-integration.md."""

from app.integrations.base import Field, Integration, IntegrationError, TileData, http


class Uptime(Integration):
    id = "uptime_kuma"
    name = "Uptime Kuma status page"
    description = "How many monitors on an Uptime Kuma status page are up."
    icon = "📈"
    fields = [
        Field(key="url", label="Uptime Kuma URL", type="url", required=True),
        Field(key="slug", label="Status page slug", required=True),
    ]

    async def fetch(self, config):
        url = f"{config['url'].rstrip('/')}/api/status-page/heartbeat/{config['slug']}"
        async with http() as client:
            response = await client.get(url)
        if response.status_code != 200:
            raise IntegrationError(f"Uptime Kuma answered {response.status_code} for status page {config['slug']!r}.")
        # heartbeatList maps each monitor id to its recent beats, oldest first; status 1 means up.
        beats = [b[-1] for b in response.json().get("heartbeatList", {}).values() if b]
        up = sum(1 for b in beats if b.get("status") == 1)
        return TileData(
            status="ok" if up == len(beats) else "error",
            value=f"{up}/{len(beats)}",
            detail="monitors up",
            url=f"{config['url'].rstrip('/')}/status/{config['slug']}",
        )


integration = Uptime()
