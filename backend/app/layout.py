"""The tile layout: groups of tiles, stored as revisioned JSON documents."""

import json
import threading
import uuid
from pathlib import Path
from typing import Any, Literal

import yaml
from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field, ValidationError, model_validator

from . import db

KEEP_REVISIONS = 50


def _new_id() -> str:
    return uuid.uuid4().hex[:10]


class Tile(BaseModel):
    id: str = Field(default_factory=_new_id)
    integration: str
    title: str = ""
    icon: str = ""  # emoji, image URL, "si:<simple-icons slug>", or blank for the default
    size: Literal["1x1", "2x1", "1x2", "2x2"] = "1x1"
    config: dict[str, Any] = {}


class Group(BaseModel):
    id: str = Field(default_factory=_new_id)
    title: str
    tiles: list[Tile] = []


class Layout(BaseModel):
    groups: list[Group] = []

    @model_validator(mode="after")
    def unique_ids(self) -> "Layout":
        ids = [g.id for g in self.groups] + [t.id for g in self.groups for t in g.tiles]
        if len(ids) != len(set(ids)):
            raise ValueError("Every group and tile needs a unique id.")
        return self


def current() -> tuple[int, Layout]:
    rows = db.query("SELECT id, doc FROM layout_revisions ORDER BY id DESC LIMIT 1")
    if not rows:
        return 0, Layout()
    return rows[0]["id"], Layout.model_validate_json(rows[0]["doc"])


_save_lock = threading.Lock()


def save(layout: Layout, base_revision: int | None = None) -> int:
    """Store a new revision. With `base_revision`, refuse (409) if someone saved since then."""
    with _save_lock:  # check-then-insert must be atomic across every write path
        if base_revision is not None and base_revision != current()[0]:
            raise HTTPException(409, "The layout was changed somewhere else. Reload to get the latest version.")
        return _insert(layout)


def _insert(layout: Layout) -> int:
    revision = db.execute("INSERT INTO layout_revisions (doc) VALUES (?)", (layout.model_dump_json(),))
    db.execute("DELETE FROM layout_revisions WHERE id <= ?", (revision - KEEP_REVISIONS,))
    return revision


def find_tile(tile_id: str) -> Tile | None:
    for group in current()[1].groups:
        for tile in group.tiles:
            if tile.id == tile_id:
                return tile
    return None


def parse_yaml(text: str) -> Layout:
    try:
        return Layout.model_validate(yaml.safe_load(text) or {})
    except (yaml.YAMLError, ValidationError) as e:
        raise HTTPException(422, f"That layout file isn't valid: {e}")


def seed(config_dir: Path) -> None:
    """On first run, load config/layout.yaml (or the committed example)."""
    if current()[0]:
        return
    for name in ("layout.yaml", "layout.example.yaml"):
        path = config_dir / name
        if path.is_file():
            save(parse_yaml(path.read_text()))
            return


router = APIRouter(prefix="/api/layout", tags=["layout"])


class SaveRequest(BaseModel):
    layout: Layout
    base_revision: int | None = None


@router.get("")
def get_layout():
    revision, layout = current()
    return {"revision": revision, "layout": layout}


@router.put("")
def put_layout(body: SaveRequest):
    return {"revision": save(body.layout, body.base_revision)}


@router.get("/revisions")
def list_revisions():
    rows = db.query("SELECT id, created_at, doc FROM layout_revisions ORDER BY id DESC")
    return [{"revision": r["id"], "created_at": r["created_at"],
             "tiles": sum(len(g["tiles"]) for g in json.loads(r["doc"])["groups"])} for r in rows]


@router.post("/revisions/{revision}/restore")
def restore_revision(revision: int):
    rows = db.query("SELECT doc FROM layout_revisions WHERE id = ?", (revision,))
    if not rows:
        raise HTTPException(404, "That revision no longer exists.")
    return {"revision": save(Layout.model_validate_json(rows[0]["doc"]))}


@router.get("/export", response_class=PlainTextResponse)
def export_yaml():
    data = current()[1].model_dump(mode="json")
    return PlainTextResponse(yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
                             media_type="application/yaml",
                             headers={"Content-Disposition": 'attachment; filename="layout.yaml"'})


@router.post("/import")
def import_yaml(text: str = Body(..., media_type="application/yaml")):
    return {"revision": save(parse_yaml(text))}
