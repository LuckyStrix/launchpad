import asyncio
import json
from datetime import datetime

import httpx
import pytest

from app import tiles
from app.integrations import apod


def test_link_up_and_down(client, mock_http):
    mock_http(lambda request: httpx.Response(401))
    assert client.get("/api/tiles/site/data").json()["status"] == "ok"  # a login wall still means "up"

    def down(request):
        raise httpx.ConnectError("refused")

    mock_http(down)
    tiles._cache.clear()
    data = client.get("/api/tiles/site/data").json()
    assert data == {**data, "status": "error", "detail": "Not responding"}


def test_unknown_tile(client):
    assert client.get("/api/tiles/nope/data").status_code == 404


def test_homeassistant_light_renders_controls(client, mock_http):
    mock_http(lambda request: httpx.Response(200, json={
        "entity_id": "light.lamp", "state": "on",
        "attributes": {"friendly_name": "Desk lamp", "brightness": 128}}))
    data = client.get("/api/tiles/lamp/data").json()
    assert data["value"] == "on"
    assert data["detail"] == "Desk lamp"
    assert [c["kind"] for c in data["controls"]] == ["toggle", "slider"]
    assert data["controls"][1]["value"] == 50


def test_homeassistant_toggle_calls_service_on_tile_entity(client, mock_http):
    seen = mock_http(lambda request: httpx.Response(200, json=[
        {"entity_id": "light.lamp", "state": "off", "attributes": {}}]))
    data = client.post("/api/tiles/lamp/actions/call", json={"value": False}).json()

    assert seen[0].url.path == "/api/services/light/turn_off"
    assert json.loads(seen[0].content) == {"entity_id": "light.lamp"}
    assert seen[0].headers["authorization"] == "Bearer ha-token"
    assert data["value"] == "off"


def test_homeassistant_rejects_odd_service_names(client, mock_http):
    seen = mock_http(lambda request: httpx.Response(200, json=[]))
    data = client.post("/api/tiles/lamp/actions/call", json={"service": "../config"}).json()
    assert data["status"] == "error"
    assert seen == []


def test_github_tile(client, mock_http):
    def github(request):
        path = request.url.path
        if path == "/repos/me/proj":
            return httpx.Response(200, json={"html_url": "https://github.com/me/proj", "open_issues_count": 5,
                                             "default_branch": "main"})
        if path.endswith("/actions/runs"):
            return httpx.Response(200, json={"workflow_runs": [{
                "name": "CI", "display_title": "Fix bug", "head_branch": "main", "status": "completed",
                "conclusion": "failure", "html_url": "https://github.com/me/proj/actions/runs/1"}]})
        if path.endswith("/pulls"):
            return httpx.Response(200, json=[{"number": 1}, {"number": 2}])
        return httpx.Response(404)

    mock_http(github)
    data = client.get("/api/tiles/repo/data").json()
    assert (data["status"], data["value"]) == ("error", "Failing")
    assert data["detail"] == "2 open PRs, 3 issues"
    assert data["controls"][0]["action"] == "dispatch"


def test_unconfigured_integration_says_what_to_set(client, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN")
    data = client.get("/api/tiles/repo/data").json()
    assert data["status"] == "warn"
    assert "GITHUB_TOKEN" in data["error"]


def test_integration_schemas(client):
    schemas = {i["id"]: i for i in client.get("/api/integrations").json()}
    assert {"link", "homeassistant", "github", "google_calendar", "google_tasks", "hermes"} <= set(schemas)
    assert schemas["homeassistant"]["fields"][0]["type"] == "picker"
    assert "create_issue" in schemas["github"]["tools"]


def test_problem_tiles_are_not_cached(client, mock_http):
    mock_http(lambda request: httpx.Response(500))
    assert client.get("/api/tiles/site/data").json()["status"] == "error"

    mock_http(lambda request: httpx.Response(200))
    assert client.get("/api/tiles/site/data").json()["status"] == "ok"  # recovers at once


STATES = [
    {"entity_id": "light.desk", "state": "on", "attributes": {"friendly_name": "Desk lamp"}},
    {"entity_id": "switch.fan", "state": "off", "attributes": {"friendly_name": "Fan"}},
    {"entity_id": "sensor.temp", "state": "21.46",
     "attributes": {"friendly_name": "Office", "unit_of_measurement": "°C"}},
    {"entity_id": "scene.movie", "state": "2026-10-20", "attributes": {"friendly_name": "Movie night"}},
]


def test_homeassistant_list_shows_rows_with_switches(client, mock_http):
    from app.integrations import registry

    mock_http(lambda request: httpx.Response(200, json=STATES))
    config = {"entities": ["sensor.temp", "light.desk", "switch.fan", "scene.movie", "light.gone"]}
    data = asyncio.run(registry["homeassistant_list"].fetch(config))  # saved before "style": a list

    assert data.layout == "list" and data.value == "1 of 2 on"
    assert [(i.label, i.kind, i.on, i.detail, i.icon) for i in data.items] == [
        ("Office", "check", None, "21.5 °C", "📟"),
        ("Desk lamp", "switch", True, "On", "💡"),
        ("Fan", "switch", False, "Off", "🔌"),
        ("Movie night", "button", None, None, "🎬"),
        ("light.gone", "check", None, "Not found in Home Assistant", None),
    ]
    assert data.items[0].action is None  # sensors are view-only

    grid = asyncio.run(registry["homeassistant_list"].fetch({**config, "style": "grid"}))
    assert grid.layout == "grid" and grid.value is None


def test_homeassistant_list_only_flips_its_own_entities(client, mock_http):
    from app.integrations import registry
    from app.integrations.base import IntegrationError

    seen = mock_http(lambda request: httpx.Response(200, json=STATES if request.method == "GET" else []))
    ha_list = registry["homeassistant_list"]
    config = {"entities": ["switch.fan", "scene.movie"]}

    asyncio.run(ha_list.flip(config, {"entity_id": "switch.fan"}))
    asyncio.run(ha_list.flip(config, {"entity_id": "scene.movie"}))
    posts = [r.url.path for r in seen if r.method == "POST"]
    assert posts == ["/api/services/switch/toggle", "/api/services/scene/turn_on"]
    with pytest.raises(IntegrationError):
        asyncio.run(ha_list.flip(config, {"entity_id": "lock.front_door"}))


def test_changes_skip_the_cache_and_are_reported(client, mock_http):
    seen = mock_http(lambda request: httpx.Response(200, json=[
        {"entity_id": "light.lamp", "state": "on", "attributes": {}}] if request.method == "POST" else
        {"entity_id": "light.lamp", "state": "on", "attributes": {}}))
    client.get("/api/tiles/lamp/data")
    client.get("/api/tiles/lamp/data")
    assert len(seen) == 1  # second read came from the cache

    counts = lambda: client.get("/api/tiles/changes").json()["counts"]  # noqa: E731
    before = counts().get("homeassistant", 0)
    client.post("/api/tiles/lamp/actions/call", json={"value": True})
    assert counts()["homeassistant"] == before + 1
    assert client.get("/api/tiles/changes").json()["epoch"] == tiles.EPOCH  # tells the page about restarts

    client.get("/api/tiles/lamp/data")
    assert [r.method for r in seen] == ["GET", "POST", "GET"]  # the change dropped the cached data


def test_home_assistant_tiles_share_one_source():
    from app.integrations import registry

    assert registry["homeassistant"].source == registry["homeassistant_list"].source == "homeassistant"
    assert registry["google_calendar"].source == "google_calendar"


def test_slow_tile_data_hits_the_deadline(client, monkeypatch):
    from app.integrations import registry

    async def slow(config):
        await asyncio.sleep(1)

    monkeypatch.setattr(tiles, "LOAD_DEADLINE", 0.05)
    monkeypatch.setattr(registry["link"], "fetch", slow)
    data = client.get("/api/tiles/site/data").json()
    assert data["status"] == "error" and "took more than" in data["error"]


def test_homeassistant_buttons_cant_add_targets_or_services(client, mock_http):
    seen = mock_http(lambda request: httpx.Response(200, json=[]))
    for params in ({"service": "turn_on", "data": {"area_id": "house"}},  # would reach every device in the area
                   {"service": "turn_on", "field": "entity_id", "value": "light.other"},
                   {"service": "reload"}):
        data = client.post("/api/tiles/lamp/actions/call", json=params).json()
        assert data["status"] == "error", params
    assert seen == []

    client.post("/api/tiles/lamp/actions/call", json={"service": "turn_on", "field": "brightness_pct", "value": 40})
    assert json.loads(seen[0].content) == {"brightness_pct": 40, "entity_id": "light.lamp"}


def test_an_action_that_errors_still_refreshes_related_tiles(client, mock_http):
    # It may have changed something before failing (a workflow started, then the follow-up read broke).
    mock_http(lambda request: httpx.Response(500))
    before = client.get("/api/tiles/changes").json()["counts"].get("homeassistant", 0)
    client.post("/api/tiles/lamp/actions/call", json={"value": True})
    assert client.get("/api/tiles/changes").json()["counts"]["homeassistant"] == before + 1


def test_timeouts_inside_an_integration_are_not_blamed_on_the_deadline(client, monkeypatch):
    from app.integrations import registry

    async def inner_timeout(config):
        raise TimeoutError

    monkeypatch.setattr(registry["link"], "fetch", inner_timeout)
    assert client.get("/api/tiles/site/data").json()["error"] == "Link timed out."


def test_github_counts_more_than_100_open_prs(client, mock_http):
    def reply(request):
        path = request.url.path
        if path.endswith("/pulls") and request.url.params.get("per_page") == "1":
            return httpx.Response(200, json=[{}], headers={
                "link": '<https://api.github.com/repositories/1/pulls?per_page=1&page=2>; rel="next", '
                        '<https://api.github.com/repositories/1/pulls?per_page=1&page=250>; rel="last"'})
        if path.endswith("/pulls"):
            return httpx.Response(200, json=[{"number": n} for n in range(100)])
        if path.endswith("/runs"):
            return httpx.Response(200, json={"workflow_runs": []})
        return httpx.Response(200, json={"open_issues_count": 260, "html_url": "https://github.com/me/proj"})

    mock_http(reply)
    assert client.get("/api/tiles/repo/data").json()["detail"] == "250 open PRs, 10 issues"


def test_github_says_100_plus_when_the_pr_count_request_fails(client, mock_http):
    def reply(request):
        path = request.url.path
        if path.endswith("/pulls") and request.url.params.get("per_page") == "1":
            return httpx.Response(500)
        if path.endswith("/pulls"):
            return httpx.Response(200, json=[{"number": n} for n in range(100)])
        if path.endswith("/runs"):
            return httpx.Response(200, json={"workflow_runs": []})
        return httpx.Response(200, json={"open_issues_count": 260, "html_url": "https://github.com/me/proj"})

    mock_http(reply)
    assert client.get("/api/tiles/repo/data").json()["detail"] == "100+ open PRs"




def _apod_reply(date="2026-10-22", media=None, content=""):
    media = [{"source_url": "https://assets.science.nasa.gov/dynamicimage/apod/orion.jpg"}] if media is None else media
    return lambda request: httpx.Response(200, json=[{
        "date": f"{date}T00:05:00", "link": f"https://science.nasa.gov/image-article/apod-{date}-orion/",
        "title": {"rendered": "APOD: 2026 October 22 &#8211; Orion &amp; Friends"},
        "content": {"rendered": content}, "_embedded": {"wp:featuredmedia": media}}])


@pytest.fixture
def clock(monkeypatch):
    """Sets the time the APOD tile sees (US Eastern) and forgets its saved picture."""
    monkeypatch.setattr(apod.integration, "_picture", None)
    now = {"at": datetime(2026, 10, 22, 9, tzinfo=apod.EASTERN)}
    monkeypatch.setattr(apod, "_now", lambda: now["at"])

    def set_time(*args):
        now["at"] = datetime(*args, tzinfo=apod.EASTERN)
        tiles._cache.clear()

    return set_time


def test_apod_shows_the_picture_title_and_credit(client, mock_http, clock):
    credit = '<th scope="row">Credit &amp; Copyright</th>\n<td class="p-sm"> <a href="#">A. Person</a>/Obs </td>'
    seen = mock_http(_apod_reply(content=credit))
    data = client.get("/api/tiles/apod/data").json()
    assert seen[0].url.params["categories"] == "22766"
    assert data["image"] == "https://assets.science.nasa.gov/dynamicimage/apod/orion.jpg?w=2048&h=2048&fit=clip"
    assert data["detail"] == "Orion & Friends · © A. Person/Obs"
    assert data["url"] == "https://science.nasa.gov/image-article/apod-2026-10-22-orion/"


def test_apod_asks_nasa_once_a_day_after_1am_eastern(client, mock_http, clock):
    seen = mock_http(_apod_reply())
    client.get("/api/tiles/apod/data")
    clock(2026, 10, 23, 0, 59)
    client.get("/api/tiles/apod/data")
    assert len(seen) == 1

    mock_http(_apod_reply("2026-10-23"))
    clock(2026, 10, 23, 1, 0)
    assert client.get("/api/tiles/apod/data").json()["url"].endswith("apod-2026-10-23-orion/")


def test_apod_tries_again_within_the_hour_when_the_new_picture_is_late(client, mock_http, clock):
    clock(2026, 10, 23, 1, 0)
    seen = mock_http(_apod_reply("2026-10-22"))
    client.get("/api/tiles/apod/data")
    clock(2026, 10, 23, 1, 30)
    client.get("/api/tiles/apod/data")
    assert len(seen) == 1
    clock(2026, 10, 23, 2, 0)
    client.get("/api/tiles/apod/data")
    assert len(seen) == 2


def test_apod_without_a_picture_points_to_the_page(client, mock_http, clock):
    mock_http(_apod_reply(media=[]))
    data = client.get("/api/tiles/apod/data").json()
    assert data["image"] is None
    assert "Open the tile" in data["error"]
    assert data["url"].startswith("https://science.nasa.gov/")


def test_apod_reports_nasa_errors(client, mock_http, clock):
    mock_http(lambda request: httpx.Response(500))
    assert client.get("/api/tiles/apod/data").json()["error"] == "NASA Science returned HTTP 500."
