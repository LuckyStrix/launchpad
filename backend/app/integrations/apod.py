from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx

from .base import Integration, IntegrationError, TileData, http

API = "https://api.nasa.gov/planetary/apod"
# APOD's date rolls over at midnight US Eastern; asking at 1am gives the new picture time to go up.
EASTERN = ZoneInfo("America/New_York")
NEW_PICTURE_AT = time(1)
RETRY = timedelta(hours=1)  # when today's picture isn't up yet


def _now() -> datetime:
    return datetime.now(EASTERN)


def _next_new_picture(now: datetime) -> datetime:
    today = datetime.combine(now.date(), NEW_PICTURE_AT, EASTERN)
    return today if now < today else today + timedelta(days=1)


class Apod(Integration):
    id = "apod"
    name = "NASA picture of the day"
    description = "NASA's Astronomy Picture of the Day, with its title and credit."
    icon = "🔭"
    refresh = 3600  # cheap: NASA is only asked once a day, after 1am Eastern; this just picks that up
    default_size = "6x4"

    def __init__(self):
        self._picture: TileData | None = None
        self._next_fetch = datetime.min.replace(tzinfo=EASTERN)

    async def fetch(self, config):
        now = _now()
        if self._picture and now < self._next_fetch:
            return self._picture

        # DEMO_KEY works without signing up, but is rate-limited per IP address.
        params = {"api_key": self.env("NASA_API_KEY") or "DEMO_KEY", "thumbs": "true"}
        try:
            async with http() as client:
                response = await client.get(API, params=params)
        except httpx.HTTPError:
            raise IntegrationError("Couldn't reach NASA's APOD service.")
        if response.status_code == 403:
            raise IntegrationError("NASA rejected NASA_API_KEY.")
        if response.status_code == 429:
            raise IntegrationError("NASA's rate limit is used up. Set NASA_API_KEY in .env for a higher one.")
        if response.is_error:
            raise IntegrationError(f"NASA's APOD service returned HTTP {response.status_code}.")

        apod = response.json()
        # Some days are a video; show its thumbnail, and the tile opens the APOD page to play it.
        image = apod.get("url") if apod.get("media_type") == "image" else apod.get("thumbnail_url")
        year, month, day = apod["date"].split("-")
        credit = (apod.get("copyright") or "").strip().replace("\n", " ")
        self._picture = TileData(
            status="ok",
            detail=f"{apod['title']} · © {credit}" if credit else apod["title"],
            image=image,
            image_alt=apod["title"],
            url=f"https://apod.nasa.gov/apod/ap{year[2:]}{month}{day}.html",
            error=None if image else "Today's APOD can't be shown here. Open the tile to see it.",
        )
        # Still yesterday's picture after 1am: try again in a while rather than tomorrow.
        posted_today = apod["date"] == now.date().isoformat()
        late = now.time() >= NEW_PICTURE_AT and not posted_today
        self._next_fetch = now + RETRY if late else _next_new_picture(now)
        return self._picture


integration = Apod()
