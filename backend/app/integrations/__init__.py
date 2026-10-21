"""Integration registry.

Every module in this package, plus every `*.py` file in `config/plugins/` (except
`*.example.py`), is imported at startup. A module contributes integrations by defining
`integration = MyIntegration()` or `integrations = [...]`.
"""

import importlib
import importlib.util
import logging
import pkgutil
from pathlib import Path
from types import ModuleType

from .base import Integration

log = logging.getLogger(__name__)

registry: dict[str, Integration] = {}


def _collect(module: ModuleType) -> list[Integration]:
    found = getattr(module, "integrations", None) or [getattr(module, "integration", None)]
    return [i for i in found if isinstance(i, Integration)]


def _builtin_modules() -> list[ModuleType]:
    return [
        importlib.import_module(f"{__name__}.{info.name}")
        for info in pkgutil.iter_modules(__path__)
        if info.name != "base"
    ]


def _plugin_modules(plugin_dir: Path) -> list[ModuleType]:
    modules = []
    paths = sorted(plugin_dir.glob("*.py")) if plugin_dir.is_dir() else []
    for path in (p for p in paths if not p.name.endswith(".example.py")):
        try:
            spec = importlib.util.spec_from_file_location(f"launchpad_plugin_{path.stem}", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            modules.append(module)
        except Exception:
            log.exception("Skipping plugin %s: it failed to import", path.name)
    return modules


def load(plugin_dir: Path) -> dict[str, Integration]:
    registry.clear()
    for module in _builtin_modules() + _plugin_modules(plugin_dir):
        for integration in _collect(module):
            if integration.id in registry:
                log.warning("Integration id %r is defined twice; keeping the later one", integration.id)
            registry[integration.id] = integration
    return registry
