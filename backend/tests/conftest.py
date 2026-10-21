from collections.abc import Callable

import httpx
import pytest
from fastapi.testclient import TestClient

from app import tiles
from app.config import settings
from app.integrations import base

LAYOUT = """
groups:
  - id: g1
    title: Main
    tiles:
      - {id: site, integration: link, title: Site, config: {url: "https://site.test"}}
      - {id: lamp, integration: homeassistant, title: Lamp, config: {entity_id: light.lamp}}
      - {id: repo, integration: github, title: Repo, config: {repo: me/proj, workflow: ci.yml}}
"""


@pytest.fixture
def mock_http(monkeypatch) -> Callable[[Callable[[httpx.Request], httpx.Response]], list[httpx.Request]]:
    """Route every outgoing integration request to a handler; returns the list of requests seen."""

    def install(handler):
        seen: list[httpx.Request] = []

        def record(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return handler(request)

        monkeypatch.setattr(base, "transport", httpx.MockTransport(record))
        return seen

    return install


@pytest.fixture
def client(tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.mkdir()
    (config / "layout.yaml").write_text(LAYOUT)
    for name, value in {
        "LAUNCHPAD_DATA_DIR": str(tmp_path / "data"),
        "LAUNCHPAD_CONFIG_DIR": str(config),
        "LAUNCHPAD_STATIC_DIR": str(tmp_path / "static"),
        "HASS_URL": "http://ha.test:8123",
        "HASS_TOKEN": "ha-token",
        "GITHUB_TOKEN": "gh-token",
        "HERMES_URL": "http://hermes.test:8642/v1",
        "HERMES_API_KEY": "hermes-key",
        "LAUNCHPAD_MCP_TOKEN": "mcp-token",
    }.items():
        monkeypatch.setenv(name, value)
    settings.cache_clear()
    tiles._cache.clear()

    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c
    settings.cache_clear()
