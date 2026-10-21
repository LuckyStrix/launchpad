"""Approval gate for write tools. A tool call waits here until the user approves or denies
it in the UI, or the timeout passes (which counts as a denial)."""

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
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    future: asyncio.Future = field(default_factory=lambda: asyncio.get_running_loop().create_future())


_pending: dict[str, Pending] = {}


async def request(tool: str, args: dict[str, Any], timeout: float | None = None) -> bool:
    activity_id = db.log_activity("tool", tool, "pending", json.dumps(args))
    pending = Pending(id=uuid.uuid4().hex[:12], tool=tool, args=args, activity_id=activity_id)
    _pending[pending.id] = pending
    try:
        approved = await asyncio.wait_for(pending.future, timeout or settings().approval_timeout)
    except TimeoutError:
        approved = None
    finally:
        _pending.pop(pending.id, None)
    db.set_activity_status(activity_id, {True: "approved", False: "denied", None: "expired"}[approved])
    return bool(approved)


router = APIRouter(prefix="/api", tags=["approvals"])


class Decision(BaseModel):
    approve: bool


@router.get("/approvals")
def list_pending():
    return [{"id": p.id, "tool": p.tool, "args": p.args, "created_at": p.created_at} for p in _pending.values()]


@router.post("/approvals/{approval_id}")
def decide(approval_id: str, decision: Decision):
    pending = _pending.get(approval_id)
    if not pending or pending.future.done():
        raise HTTPException(404, "That request already finished or expired.")
    pending.future.set_result(decision.approve)
    return {"ok": True}


@router.get("/activity")
def activity(limit: int = 100):
    rows = db.query("SELECT * FROM activity ORDER BY id DESC LIMIT ?", (min(limit, 500),))
    return [dict(r) for r in rows]
