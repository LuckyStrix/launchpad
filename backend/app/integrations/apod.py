import html
import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx

from .base import Integration, IntegrationError, TileData, http

# APOD now lives on science.nasa.gov as WordPress posts (api.nasa.gov's APOD API stopped following
# it and returns the site's logo for every day). The newest post in the APOD category is today's.
API = "https://science.nasa.gov/wp-json/wp/v2/image-article"
QUERY = {"categories": "22766", "per_page": "1", "_embed": "wp:featuredmedia",
         "_fields": "date,link,title,content,_links,_embedded"}
SIZE = {"w": "2048", "h": "2048", "fit": "clip"}  # assets.science.nasa.gov resizes on request
TITLE_PREFIX = re.compile(r"^APOD:\s*\d{4} \w+ \d{1,2}\s*[–-]\s*")
CREDIT = re.compile(r"Credit &amp; Copyright</th>\s*<td[^>]*>(.*?)</td>", re.S)
TAG = re.compile(r"<[^>]+>")

# APOD's date rolls over at midnight US Eastern; asking at 1am gives the new picture time to go up.
EASTERN = ZoneInfo("America/New_York")
NEW_PICTURE_AT = time(1)
RETRY = timedelta(hours=1)  # when today's picture isn't up yet


def _now() -> datetime:
    return datetime.now(EASTERN)


def _next_new_picture(now: datetime) -> datetime:
    today = datetime.combine(now.date(), NEW_PICTURE_AT, EASTERN)
    return today if now < today else today + timedelta(days=1)


def _text(fragment: str) -> str:
    return " ".join(html.unescape(TAG.sub("", fragment)).split())


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

        try:
            async with http() as client:
                response = await client.get(API, params=QUERY)
        except httpx.HTTPError:
            raise IntegrationError("Couldn't reach NASA Science.")
        if response.is_error:
            raise IntegrationError(f"NASA Science returned HTTP {response.status_code}.")
        posts = response.json()
        if not posts:
            raise IntegrationError("NASA Science didn't list any APOD posts.")

        post = posts[0]
        title = TITLE_PREFIX.sub("", _text(post["title"]["rendered"]))
        credit = CREDIT.search(post.get("content", {}).get("rendered", ""))
        media = (post.get("_embedded", {}).get("wp:featuredmedia") or [{}])[0]
        image = media.get("source_url")
        if image and "/dynamicimage/" in image:
            image = str(httpx.URL(image, params=SIZE))
        self._picture = TileData(
            status="ok",
            detail=f"{title} · © {_text(credit[1])}" if credit else title,
            image=image,
            image_alt=title,
            url=post["link"],
            error=None if image else "Today's APOD can't be shown here. Open the tile to see it.",
        )
        # Still yesterday's picture after 1am: try again in a while rather than tomorrow.
        posted_today = post["date"][:10] == now.date().isoformat()
        late = now.time() >= NEW_PICTURE_AT and not posted_today
        self._next_fetch = now + RETRY if late else _next_new_picture(now)
        return self._picture


integration = Apod()
