import asyncio

import httpx

from app import db
from app.integrations import google, registry


def _connected(monkeypatch):
    monkeypatch.setattr(google, "_access", {"token": "access", "expires": 1e12})


def test_calendar_ids_are_escaped_and_today_uses_tz(client, mock_http, monkeypatch):
    _connected(monkeypatch)
    monkeypatch.setenv("TZ", "America/New_York")
    seen = mock_http(lambda request: httpx.Response(200, json={"items": []}))

    asyncio.run(registry["google_calendar"].fetch({"calendar": "en.usa#holiday@group.v.calendar.google.com"}))

    assert seen[0].url.raw_path.startswith(b"/calendar/v3/calendars/en.usa%23holiday@group.v.calendar.google.com/")
    assert seen[0].url.params["timeMax"].endswith(("-04:00", "-05:00"))  # midnight in New York, not UTC


def test_all_day_event_with_same_start_and_end(client, mock_http, monkeypatch):
    _connected(monkeypatch)
    seen = mock_http(lambda request: httpx.Response(200, json={"id": "e1"}))

    asyncio.run(registry["google_calendar"].create_event("Trip", "2026-10-22", "2026-10-22"))

    assert b'"end":{"date":"2026-10-23"}' in seen[0].content.replace(b" ", b"")


def test_failed_sign_in_redirects_with_reason(client, mock_http, monkeypatch):
    monkeypatch.setenv("PUBLIC_URL", "https://launchpad.test")
    db.kv_set(google.STATE_KEY, "s1")
    mock_http(lambda request: httpx.Response(400, text="not json"))

    response = client.get("/api/google/callback?code=c&state=s1", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"].startswith("/?google=error&reason=Google+refused")
