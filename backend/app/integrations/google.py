"""Google Calendar and Google Tasks, sharing one OAuth connection.

Connect at /api/google/connect. The refresh token is stored in SQLite (data/, never in git).
"""

import asyncio
import re
import secrets
import time
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from .. import db
from ..config import env, settings
from .base import Field, Integration, IntegrationError, Item, Option, TileData, action, http, tool

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"
TASKS_API = "https://tasks.googleapis.com/tasks/v1"
SCOPES = " ".join([
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/tasks",
])
TOKEN_KEY = "google.refresh_token"
STATE_KEY = "google.oauth_state"
REQUIRES = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "PUBLIC_URL")

DATE_ONLY = re.compile(r"\d{4}-\d{2}-\d{2}")

_access: dict[str, Any] = {"token": None, "expires": 0.0}


class NotConnected(IntegrationError):
    pass


def _redirect_uri() -> str:
    return f"{settings().public_url}/api/google/callback"


def _tz() -> ZoneInfo:
    """The user's time zone (TZ in .env), so "today" means their today, not UTC's."""
    try:
        return ZoneInfo(env("TZ") or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _seg(value: str) -> str:
    """One URL path segment. Calendar ids can contain '#', e.g. en.usa#holiday@group.v.calendar.google.com."""
    return quote(value, safe="@")


def _client() -> dict[str, str]:
    return {"client_id": env("GOOGLE_CLIENT_ID"), "client_secret": env("GOOGLE_CLIENT_SECRET")}


async def _token_request(data: dict[str, str]) -> dict:
    try:
        async with http() as client:
            response = await client.post(TOKEN_URL, data={**_client(), **data})
    except httpx.HTTPError:
        raise IntegrationError("Couldn't reach Google.")
    if response.is_error:
        try:
            reason = response.json().get("error", response.status_code)
        except ValueError:
            reason = response.status_code
        raise IntegrationError(f"Google refused the sign-in: {reason}")
    return response.json()


async def _access_token() -> str:
    if _access["token"] and _access["expires"] > time.time() + 60:
        return _access["token"]
    refresh = db.kv_get(TOKEN_KEY)
    if not refresh:
        raise NotConnected("Connect your Google account.")
    try:
        grant = await _token_request({"grant_type": "refresh_token", "refresh_token": refresh})
    except IntegrationError as e:
        if "invalid_grant" in str(e):
            db.kv_delete(TOKEN_KEY)
            raise NotConnected("Google signed Launchpad out. Connect your account again.")
        raise
    _access.update(token=grant["access_token"], expires=time.time() + grant.get("expires_in", 3600))
    return _access["token"]


async def gapi(method: str, url: str, **kwargs: Any) -> Any:
    headers = {"Authorization": f"Bearer {await _access_token()}"}
    try:
        async with http(headers=headers) as client:
            response = await client.request(method, url, **kwargs)
    except httpx.HTTPError:
        raise IntegrationError("Couldn't reach Google.")
    if response.status_code == 404:
        raise IntegrationError("Google can't find that calendar, list or item.")
    if response.is_error:
        raise IntegrationError(f"Google returned HTTP {response.status_code}.")
    return response.json() if response.content else None


router = APIRouter(prefix="/api/google", tags=["google"])


@router.get("/connect")
async def connect(request: Request):
    # A GET, so the same-origin guard doesn't cover it: refuse other sites resetting a sign-in in progress.
    if request.headers.get("sec-fetch-site", "none") not in ("same-origin", "none"):
        return _sign_in_failed("start Google sign-in from Launchpad itself")
    if not settings().public_url or not all(_client().values()):
        raise HTTPException(400, "Set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and PUBLIC_URL in .env first.")
    state = secrets.token_urlsafe(24)
    db.kv_set(STATE_KEY, state)
    query = urlencode({"client_id": _client()["client_id"], "redirect_uri": _redirect_uri(), "response_type": "code",
                       "scope": SCOPES, "access_type": "offline", "prompt": "consent", "state": state})
    return RedirectResponse(f"{AUTH_URL}?{query}")


@router.get("/callback")
async def callback(code: str = "", state: str = "", error: str = ""):
    if error or not code:
        return _sign_in_failed(error or "no code")
    if not state or state != db.kv_get(STATE_KEY):
        return _sign_in_failed("this sign-in link has expired")
    db.kv_delete(STATE_KEY)
    try:
        grant = await _token_request({"grant_type": "authorization_code", "code": code,
                                      "redirect_uri": _redirect_uri()})
    except IntegrationError as e:
        return _sign_in_failed(str(e))
    if "refresh_token" not in grant:
        return _sign_in_failed("Google didn't return a refresh token. Remove Launchpad's access in your "
                               "Google account settings, then connect again")
    db.kv_set(TOKEN_KEY, grant["refresh_token"])
    _access.update(token=grant["access_token"], expires=time.time() + grant.get("expires_in", 3600))
    return RedirectResponse("/?google=connected")


def _sign_in_failed(reason: str) -> RedirectResponse:
    return RedirectResponse(f"/?{urlencode({'google': 'error', 'reason': reason})}")


@router.get("/status")
async def status():
    return {"connected": bool(db.kv_get(TOKEN_KEY))}


@router.post("/disconnect")
async def disconnect():
    db.kv_delete(TOKEN_KEY)
    _access.update(token=None, expires=0.0)
    return {"connected": False}


def _not_connected(e: NotConnected) -> TileData:
    return TileData(status="warn", value="Not connected", detail=str(e), url="/api/google/connect")


def _when(event: dict, today: date) -> str:
    start = event["start"]
    if "date" in start:
        day = date.fromisoformat(start["date"])
        return "All day" if day == today else f"{day:%a} all day"
    moment = datetime.fromisoformat(start["dateTime"]).astimezone(_tz())
    clock = moment.strftime("%-I:%M %p")
    return clock if moment.date() == today else f"{moment:%a} {clock}"


def _start(event: dict) -> datetime:
    """When an event starts, comparable across all-day and timed events."""
    start = event["start"]
    if "dateTime" in start:
        return datetime.fromisoformat(start["dateTime"])
    return datetime.combine(date.fromisoformat(start["date"]), datetime.min.time(), _tz())


class GoogleCalendar(Integration):
    id = "google_calendar"
    name = "Google Calendar"
    description = "Upcoming events from one or more of your calendars."
    icon = "si:googlecalendar"
    requires = REQUIRES
    refresh = 300
    default_size = "2x2"
    router = router
    fields = [
        Field(key="calendar", label="Calendars", type="multipicker", default=["primary"]),
        Field(key="days", label="Show", type="select", default="1",
              options=[{"value": "1", "label": "Today"}, {"value": "3", "label": "Next 3 days"},
                       {"value": "7", "label": "Next 7 days"}]),
    ]

    async def _events(self, calendar: str, days: int) -> list[dict]:
        return (await self._calendar(calendar, days))["items"]

    async def _calendar(self, calendar: str, days: int) -> dict:
        """One calendar's name ("summary") and upcoming events ("items")."""
        now = datetime.now(_tz())
        end = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=max(days, 1))
        data = await gapi("GET", f"{CALENDAR_API}/calendars/{_seg(calendar)}/events", params={
            "timeMin": now.isoformat(), "timeMax": end.isoformat(), "singleEvents": "true",
            "orderBy": "startTime", "maxResults": 25})
        return {"summary": data.get("summary", calendar), "items": data.get("items", [])}

    async def fetch(self, config):
        chosen = config.get("calendar") or ["primary"]
        chosen = [chosen] if isinstance(chosen, str) else chosen  # tiles saved before multi-select
        days = int(config.get("days") or 1)
        try:
            calendars = await asyncio.gather(*(self._calendar(c, days) for c in chosen))
        except NotConnected as e:
            return _not_connected(e)
        # Merge every calendar's events by start time; name the calendar when there are several.
        events = sorted(((e, c["summary"]) for c in calendars for e in c["items"]), key=lambda pair: _start(pair[0]))
        today = datetime.now(_tz()).date()
        items = [Item(label=e.get("summary", "(No title)"),
                      detail=_when(e, today) + (f", {name}" if len(chosen) > 1 else ""), url=e.get("htmlLink"))
                 for e, name in events[:25]]
        if not events:
            return TileData(status="idle", value="Clear", detail="Nothing scheduled",
                            url="https://calendar.google.com/")
        first = items[0]
        return TileData(status="ok", value=f"{len(events)} event{'s' if len(events) != 1 else ''}",
                        detail=f"Next: {first.label}, {first.detail}", items=items,
                        url="https://calendar.google.com/")

    async def options(self, field, query):
        data = await gapi("GET", f"{CALENDAR_API}/users/me/calendarList")
        # The main calendar is offered as "primary", the id saved tiles use by default.
        return [Option(value="primary" if c.get("primary") else c["id"], label=c.get("summaryOverride") or c["summary"])
                for c in data.get("items", []) if query.lower() in c["summary"].lower()]

    @tool()
    async def events(self, days: int = 1, calendar: str = "primary") -> list[dict]:
        """List upcoming events from now through the next `days` days."""
        return [{"id": e["id"], "summary": e.get("summary"), "start": e["start"], "end": e["end"],
                 "location": e.get("location")} for e in await self._events(calendar, days)]

    @tool(write=True)
    async def create_event(self, summary: str, start: str, end: str, description: str = "",
                           calendar: str = "primary") -> dict:
        """Create a calendar event. start and end are ISO 8601 date-times with offsets
        (e.g. 2026-10-22T15:00:00-04:00), or YYYY-MM-DD dates for an all-day event, where
        end is the last day of the event (2026-10-22 to 2026-10-24 is three days)."""
        all_day = [bool(DATE_ONLY.fullmatch(v)) for v in (start, end)]
        if all_day[0] != all_day[1]:
            raise IntegrationError("Give start and end in the same form: both YYYY-MM-DD dates (all day), "
                                   "or both date-times with offsets.")
        key = "date" if all_day[0] else "dateTime"
        if all_day[0]:
            first, last = date.fromisoformat(start), date.fromisoformat(end)
            if last < first:
                raise IntegrationError("The event ends before it starts.")
            end = (last + timedelta(days=1)).isoformat()  # Google's all-day end date is exclusive
        event = await gapi("POST", f"{CALENDAR_API}/calendars/{_seg(calendar)}/events", json={
            "summary": summary, "description": description, "start": {key: start}, "end": {key: end}})
        return {"id": event["id"], "url": event.get("htmlLink")}


class GoogleTasks(Integration):
    id = "google_tasks"
    name = "Google Tasks"
    description = "Open tasks from one of your lists, with a button to complete each."
    icon = "si:googletasks"
    requires = REQUIRES
    refresh = 120
    default_size = "1x2"
    fields = [Field(key="tasklist", label="List", type="picker", default="@default")]

    async def _tasks(self, tasklist: str) -> list[dict]:
        data = await gapi("GET", f"{TASKS_API}/lists/{_seg(tasklist)}/tasks",
                          params={"showCompleted": "false", "maxResults": 50})
        return data.get("items", [])

    async def fetch(self, config):
        tasklist = config.get("tasklist") or "@default"
        try:
            tasks = await self._tasks(tasklist)
        except NotConnected as e:
            return _not_connected(e)
        items = [Item(label=t.get("title") or "(Untitled)",
                      detail=f"Due {t['due'][:10]}" if t.get("due") else None,
                      action="complete", action_label="Complete", params={"task_id": t["id"]})
                 for t in tasks]
        return TileData(status="ok" if tasks else "idle", value=f"{len(tasks)} open",
                        items=items, url="https://tasks.google.com/")

    @action
    async def complete(self, config, params):
        await self._complete(params.get("task_id", ""), config.get("tasklist") or "@default")
        return await self.fetch(config)

    async def _complete(self, task_id: str, tasklist: str) -> None:
        if not task_id:
            raise IntegrationError("That task is missing its ID.")
        await gapi("PATCH", f"{TASKS_API}/lists/{_seg(tasklist)}/tasks/{_seg(task_id)}", json={"status": "completed"})

    async def options(self, field, query):
        data = await gapi("GET", f"{TASKS_API}/users/@me/lists")
        return [Option(value=t["id"], label=t["title"])
                for t in data.get("items", []) if query.lower() in t["title"].lower()]

    @tool()
    async def list_tasks(self, tasklist: str = "@default") -> list[dict]:
        """List open tasks."""
        return [{"id": t["id"], "title": t.get("title"), "notes": t.get("notes"), "due": t.get("due")}
                for t in await self._tasks(tasklist)]

    @tool(write=True)
    async def add(self, title: str, notes: str = "", due: str = "", tasklist: str = "@default") -> dict:
        """Add a task. due is an optional YYYY-MM-DD date."""
        body: dict[str, str] = {"title": title, "notes": notes}
        if due:
            body["due"] = f"{due[:10]}T00:00:00.000Z"
        task = await gapi("POST", f"{TASKS_API}/lists/{_seg(tasklist)}/tasks", json=body)
        return {"id": task["id"], "title": task["title"]}

    @tool(write=True)
    async def complete_task(self, task_id: str, tasklist: str = "@default") -> str:
        """Mark a task complete."""
        await self._complete(task_id, tasklist)
        return "Done."


integrations = [GoogleCalendar(), GoogleTasks()]
