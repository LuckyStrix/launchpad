import asyncio
import json

import httpx
import pytest

from app import tiles


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
