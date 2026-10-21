import asyncio

import httpx
import pytest

from app import db
from app.config import settings
from app.integrations import google, registry
from app.integrations.base import IntegrationError


def _connected(monkeypatch):
    monkeypatch.setattr(google, "_access", {"token": "access", "expires": 1e12})


def test_calendar_ids_are_escaped_and_today_uses_tz(client, mock_http, monkeypatch):
    _connected(monkeypatch)
    monkeypatch.setenv("TZ", "America/New_York")
    seen = mock_http(lambda request: httpx.Response(200, json={"items": []}))

    asyncio.run(registry["google_calendar"].fetch({"calendar": "en.usa#holiday@group.v.calendar.google.com"}))

    assert seen[0].url.raw_path.startswith(b"/calendar/v3/calendars/en.usa%23holiday@group.v.calendar.google.com/")
    assert seen[0].url.params["timeMax"].endswith(("-04:00", "-05:00"))  # midnight in New York, not UTC


def test_all_day_end_date_is_inclusive(client, mock_http, monkeypatch):
    _connected(monkeypatch)
    seen = mock_http(lambda request: httpx.Response(200, json={"id": "e1"}))
    calendar = registry["google_calendar"]

    asyncio.run(calendar.create_event("Trip", "2026-10-22", "2026-10-22"))
    asyncio.run(calendar.create_event("Trip", "2026-10-22", "2026-10-24"))

    assert b'"end":{"date":"2026-10-23"}' in seen[0].content.replace(b" ", b"")
    assert b'"end":{"date":"2026-10-25"}' in seen[1].content.replace(b" ", b"")
    with pytest.raises(IntegrationError):
        asyncio.run(calendar.create_event("Trip", "2026-10-22", "2026-10-22T17:00:00-04:00"))
    with pytest.raises(IntegrationError):
        asyncio.run(calendar.create_event("Trip", "2026-10-22T15:00:00-04:00", "2026-10-22"))


def test_expired_sign_in_redirects_and_connect_refuses_other_sites(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_URL", "https://launchpad.test")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    settings.cache_clear()

    expired = client.get("/api/google/callback?code=c&state=old", follow_redirects=False)
    assert expired.headers["location"].startswith("/?google=error&reason=this+sign-in+link+has+expired")
    refused = client.get("/api/google/connect", headers={"sec-fetch-site": "cross-site"}, follow_redirects=False)
    assert refused.headers["location"].startswith("/?google=error")
    assert client.get("/api/google/connect", follow_redirects=False).status_code == 307


def test_failed_sign_in_redirects_with_reason(client, mock_http, monkeypatch):
    monkeypatch.setenv("PUBLIC_URL", "https://launchpad.test")
    db.kv_set(google.STATE_KEY, "s1")
    mock_http(lambda request: httpx.Response(400, text="not json"))

    response = client.get("/api/google/callback?code=c&state=s1", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"].startswith("/?google=error&reason=Google+refused")
