"""Approval gate for write tools, and for Hermes's own approval requests (see assistant.py).
A request waits here until the user approves or denies it in the UI, or the timeout passes
(which counts as a denial)."""

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import db
from .config import settings


@dataclass
class Pending:
    id: str
    tool: str
    args: dict[str, Any]
    activity_id: int
    title: str | None = None  # the card's heading; the UI names the tool when it's missing
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    future: asyncio.Future = field(default_factory=lambda: asyncio.get_running_loop().create_future())


_pending: dict[str, Pending] = {}


async def request(tool: str, args: dict[str, Any], timeout: float | None = None,
                  activity_id: int | None = None, title: str | None = None) -> bool:
    """Wait for the user's decision. Records it on `activity_id`, or on a new activity row."""
    if activity_id is None:
        activity_id = db.log_activity("tool", tool, "pending", json.dumps(args))
    pending = Pending(id=uuid.uuid4().hex[:12], tool=tool, args=args, activity_id=activity_id, title=title)
    _pending[pending.id] = pending
    try:
        approved = await asyncio.wait_for(pending.future, timeout or settings().approval_timeout)
    except TimeoutError:
        approved = None
    except asyncio.CancelledError:  # Hermes dropped the call
        db.set_activity_status(activity_id, "cancelled")
        raise
    finally:
        _pending.pop(pending.id, None)
    db.set_activity_status(activity_id, {True: "approved", False: "denied", None: "expired"}[approved])
    return bool(approved)


router = APIRouter(prefix="/api", tags=["approvals"])


class Decision(BaseModel):
    approve: bool


# async: these touch _pending and its futures, which belong to the event loop, not a worker thread.
@router.get("/approvals")
async def list_pending():
    return [{"id": p.id, "tool": p.tool, "title": p.title, "args": p.args, "created_at": p.created_at}
            for p in _pending.values()]


@router.post("/approvals/{approval_id}")
async def decide(approval_id: str, decision: Decision):
    pending = _pending.get(approval_id)
    if not pending or pending.future.done():
        raise HTTPException(404, "That request already finished or expired.")
    pending.future.set_result(decision.approve)
    return {"ok": True}


@router.get("/activity")
def activity(limit: int = 100):
    rows = db.query("SELECT * FROM activity ORDER BY id DESC LIMIT ?", (min(limit, 500),))
    return [dict(r) for r in rows]
