"""The integration contract. See docs/adding-an-integration.md for a walkthrough.

An integration is a subclass of `Integration` that can:
  - render tiles:      `fetch(config) -> TileData`
  - offer tile buttons: methods decorated with `@action`
  - give Hermes tools:  methods decorated with `@tool` (served over MCP)
  - fill editor pickers: `options(field, query)`
  - add HTTP routes:    set `router` (e.g. an OAuth callback)
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any, Literal

import httpx
from fastapi import APIRouter
from pydantic import BaseModel

from ..config import env

Status = Literal["ok", "warn", "error", "idle"]


class IntegrationError(Exception):
    """An expected failure; the message is shown to the user as-is."""


class Item(BaseModel):
    """A row in a tile's list. With `action` set, the row gets a control that calls it: a round
    check button (`kind="check"`, e.g. complete a task), an on/off switch showing `on`
    (`kind="switch"`), or a small button labelled `action_label` (`kind="button"`)."""

    label: str
    detail: str | None = None
    url: str | None = None
    status: Status | None = None
    action: str | None = None
    action_label: str | None = None
    params: dict[str, Any] = {}
    kind: Literal["check", "switch", "button"] = "check"
    on: bool | None = None
    icon: str | None = None  # shown on cards in a "grid" tile


class Control(BaseModel):
    """A button, toggle or slider on a tile. Using it calls the named action with `params`
    (sliders and toggles add their new value as `params["value"]`)."""

    action: str
    label: str
    kind: Literal["button", "toggle", "slider"] = "button"
    value: bool | float | None = None
    min: float = 0
    max: float = 100
    step: float = 1
    params: dict[str, Any] = {}


class TileData(BaseModel):
    """Everything a tile shows. The frontend renders this generically, so a new
    integration needs no frontend code."""

    status: Status = "idle"
    value: str | None = None  # the big readout
    detail: str | None = None  # one secondary line
    items: list[Item] = []
    layout: Literal["list", "grid"] = "list"  # grid: items as tappable cards, like a phone widget
    controls: list[Control] = []
    url: str | None = None  # where clicking the tile goes
    error: str | None = None


class Field(BaseModel):
    """One input in the tile editor. `picker` and `multipicker` fields search `Integration.options()`;
    a multipicker's value is a list."""

    key: str
    label: str
    type: Literal["text", "url", "number", "bool", "select", "picker", "multipicker", "textarea"] = "text"
    required: bool = False
    default: Any = None
    options: list[dict[str, str]] = []  # for type="select": [{"value", "label"}]
    placeholder: str = ""
    help: str = ""


class Option(BaseModel):
    value: str
    label: str
    detail: str | None = None


def action(fn: Callable) -> Callable:
    """Mark a method as a tile action: `async def name(self, config, params) -> TileData | None`."""
    fn._launchpad_action = True
    return fn


def tool(*, write: bool = False) -> Callable[[Callable], Callable]:
    """Mark a typed async method as a tool Hermes can call. Its docstring is the description.
    `write=True` tools wait for the user to approve them in the UI before running."""

    def mark(fn: Callable) -> Callable:
        fn._launchpad_tool = {"write": write}
        return fn

    return mark


# Tests swap this for an httpx.MockTransport.
transport: httpx.AsyncBaseTransport | None = None


def http(**kwargs: Any) -> httpx.AsyncClient:
    kwargs.setdefault("timeout", 10)
    return httpx.AsyncClient(transport=transport, **kwargs)


class Integration:
    id: str = ""
    name: str = ""
    description: str = ""
    icon: str = ""
    requires: tuple[str, ...] = ()  # env vars that must be set
    fields: list[Field] = []
    refresh: int = 60  # seconds between tile refreshes
    default_size: str = "1x1"
    router: APIRouter | None = None

    def env(self, name: str, default: str = "") -> str:
        return env(name, default)

    @property
    def missing(self) -> list[str]:
        return [name for name in self.requires if not self.env(name)]

    @property
    def configured(self) -> bool:
        return not self.missing

    async def fetch(self, config: dict[str, Any]) -> TileData:
        return TileData()

    async def options(self, field: str, query: str) -> list[Option]:
        return []

    def actions(self) -> dict[str, Callable]:
        return {name: fn for name, fn in self._members() if getattr(fn, "_launchpad_action", False)}

    def tools(self) -> list[Callable]:
        return [fn for _, fn in self._members() if hasattr(fn, "_launchpad_tool")]

    def _members(self) -> list[tuple[str, Callable]]:
        return inspect.getmembers(self, predicate=inspect.ismethod)

    def schema(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "icon": self.icon,
            "fields": [f.model_dump() for f in self.fields],
            "refresh": self.refresh,
            "default_size": self.default_size,
            "configured": self.configured,
            "missing": self.missing,
            "tools": [fn.__name__ for fn in self.tools()],
        }
