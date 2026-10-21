"""Example plugin. Copy it to config/plugins/uptime.py (the loader skips *.example.py files)
and restart Launchpad. A plugin is any module that defines `integration = SomeIntegration()`;
see docs/adding-an-integration.md."""

from app.integrations.base import Field, Integration, TileData, http


class Uptime(Integration):
    id = "uptime_kuma"
    name = "Uptime Kuma badge"
    description = "Shows a monitor's status from an Uptime Kuma status page."
    icon = "📈"
    fields = [Field(key="url", label="Status page JSON URL", type="url", required=True)]

    async def fetch(self, config):
        async with http() as client:
            data = (await client.get(config["url"])).json()
        up = data.get("status") == "up"
        return TileData(status="ok" if up else "error", value="Up" if up else "Down")


integration = Uptime()
