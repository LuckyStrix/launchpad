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


def test_calendar_merges_several_calendars_by_start_time(client, mock_http, monkeypatch):
    _connected(monkeypatch)

    def reply(request):
        if "work" in request.url.path:
            return httpx.Response(200, json={"summary": "Work", "items": [
                {"summary": "Standup", "start": {"dateTime": "2026-10-22T09:00:00-04:00"}}]})
        return httpx.Response(200, json={"summary": "Me", "items": [
            {"summary": "Dentist", "start": {"dateTime": "2026-10-22T08:00:00-04:00"}},
            {"summary": "Trip", "start": {"date": "2026-10-23"}}]})

    mock_http(reply)
    calendar = registry["google_calendar"]
    data = asyncio.run(calendar.fetch({"calendar": ["primary", "work@example.com"], "days": "3"}))
    assert [i.label for i in data.items] == ["Dentist", "Standup", "Trip"]
    assert data.items[1].detail.endswith(", Work")

    single = asyncio.run(calendar.fetch({"calendar": "primary"}))  # tiles saved before multi-select
    assert [i.label for i in single.items] == ["Dentist", "Trip"] and "," not in single.items[0].detail


def test_calendar_picker_searches_shown_names(client, mock_http, monkeypatch):
    _connected(monkeypatch)
    mock_http(lambda request: httpx.Response(200, json={"items": [
        {"id": "a@x", "summary": "Shared 2019", "summaryOverride": "Family"},
        {"id": "b@x"},  # no name at all
        {"id": "me@x", "summary": "Me", "primary": True}]}))
    options = asyncio.run(registry["google_calendar"].options("calendar", "fam"))
    assert [(o.value, o.label) for o in options] == [("a@x", "Family")]
    every = asyncio.run(registry["google_calendar"].options("calendar", ""))
    assert [o.value for o in every] == ["a@x", "b@x", "primary"]


def test_refresh_finishing_after_disconnect_is_dropped(client, mock_http, monkeypatch):
    monkeypatch.setattr(google, "_access", {"token": None, "expires": 0.0})
    db.kv_set(google.TOKEN_KEY, "refresh-1")

    def reply(request):
        db.kv_delete(google.TOKEN_KEY)  # the user disconnects while the refresh is in flight
        return httpx.Response(200, json={"access_token": "new", "expires_in": 3600})

    mock_http(reply)
    with pytest.raises(google.NotConnected):
        asyncio.run(google._access_token())
    assert google._access["token"] is None


def test_rejected_old_token_doesnt_sign_out_a_fresh_reconnect(client, mock_http, monkeypatch):
    monkeypatch.setattr(google, "_access", {"token": None, "expires": 0.0})
    db.kv_set(google.TOKEN_KEY, "old")

    def reply(request):
        db.kv_set(google.TOKEN_KEY, "new")  # the user reconnects while the old token is being refused
        google._access.update(token="fresh", expires=1e12)
        return httpx.Response(400, json={"error": "invalid_grant"})

    mock_http(reply)
    assert asyncio.run(google._access_token()) == "fresh"
    assert db.kv_get(google.TOKEN_KEY) == "new"
