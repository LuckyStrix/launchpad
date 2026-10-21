import httpx

from ..config import settings
from .base import Integration, TileData, http


class Hermes(Integration):
    id = "hermes"
    name = "Hermes"
    description = "Shows whether the Hermes assistant is online. Opens the chat."
    icon = "☿"
    refresh = 30

    async def fetch(self, config):
        base = settings().hermes_url.removesuffix("/v1")
        try:
            async with http(timeout=4) as client:
                response = await client.get(f"{base}/health")
            online = response.status_code == 200
        except httpx.HTTPError:
            online = False
        if online:
            return TileData(status="ok", value="Online", detail="Open the assistant", url="#assistant")
        return TileData(status="error", value="Offline", detail="Hermes isn't responding", url="#assistant")


integration = Hermes()
