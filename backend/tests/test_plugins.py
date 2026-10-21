from app import integrations

PLUGIN = '''
from __future__ import annotations

from dataclasses import dataclass

from app.integrations.base import Integration, TileData


@dataclass
class Greeting:  # needs the module in sys.modules to build
    text: str


class Hello(Integration):
    id = "hello"
    name = "Hello"

    async def fetch(self, config):
        return TileData(value="hi")


integration = Hello()
'''


def test_loads_plugins_and_skips_examples(tmp_path):
    (tmp_path / "hello.py").write_text(PLUGIN)
    (tmp_path / "other.example.py").write_text(PLUGIN.replace('"hello"', '"other"'))
    (tmp_path / "broken.py").write_text("raise RuntimeError('boom')")

    registry = integrations.load(tmp_path)

    assert "hello" in registry
    assert "other" not in registry
    assert "link" in registry  # built-ins still load when a plugin is broken
