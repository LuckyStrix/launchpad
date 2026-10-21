"""Chat with Hermes. Launchpad stores the conversation and streams Hermes's OpenAI-compatible
/v1/chat/completions output to the browser as server-sent events:

    event: session  data: {"id": ...}
    event: delta    data: {"text": ...}
    event: error    data: {"message": ...}
    event: done     data: {}
"""

import json
import logging
import uuid
from collections.abc import AsyncIterator

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import db, layout
from .config import settings
from .integrations import base

HISTORY_LIMIT = 40
SYSTEM_PROMPT = """You are the assistant on the user's Launchpad homepage. Be brief and practical.
Launchpad gives you tools over MCP (named launchpad_*) for their GitHub repos, Google Calendar and Google Tasks;
Home Assistant control comes from your own Home Assistant tools. Actions that change something wait for the user
to approve them on the page. The tiles currently on their homepage:
{tiles}"""

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/assistant", tags=["assistant"])


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _tile_summary() -> str:
    lines = []
    for group in layout.current()[1].groups:
        names = ", ".join(t.title or t.integration for t in group.tiles) or "(empty)"
        lines.append(f"- {group.title}: {names}")
    return "\n".join(lines) or "- (none yet)"


def _history(session_id: str) -> list[dict]:
    rows = db.query("SELECT role, content FROM chat_messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                    (session_id, HISTORY_LIMIT))
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def _error_message(e: Exception) -> str:
    url = settings().hermes_url
    if isinstance(e, httpx.HTTPStatusError):
        if e.response.status_code == 401:
            return "Hermes rejected the API key. Check that HERMES_API_KEY matches API_SERVER_KEY in Hermes."
        return f"Hermes returned HTTP {e.response.status_code}."
    if isinstance(e, httpx.ConnectError):
        return f"Hermes isn't reachable at {url}. Check that its gateway and API server are running."
    if isinstance(e, httpx.TimeoutException):
        return "Hermes took too long to respond."
    return f"The connection to Hermes failed ({e.__class__.__name__})."


async def _stream_from_hermes(messages: list[dict]) -> AsyncIterator[str]:
    s = settings()
    headers = {"Authorization": f"Bearer {s.hermes_api_key}"} if s.hermes_api_key else {}
    body = {"model": s.hermes_model, "messages": messages, "stream": True}
    # No read timeout: Hermes may run tools for minutes between tokens.
    async with base.http(timeout=httpx.Timeout(10, read=None)) as client:
        async with client.stream("POST", f"{s.hermes_url}/chat/completions", json=body, headers=headers) as resp:
            if resp.is_error:
                await resp.aread()
                resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    return
                try:
                    choice = json.loads(payload)["choices"][0]
                except (ValueError, KeyError, IndexError):
                    continue
                text = (choice.get("delta") or {}).get("content")
                if text:
                    yield text


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


@router.post("/chat")
async def chat(body: ChatRequest):
    message = body.message.strip()
    if not message:
        raise HTTPException(422, "Type a message first.")
    session_id = body.session_id
    if not session_id or not db.query("SELECT 1 FROM chat_sessions WHERE id = ?", (session_id,)):
        session_id = uuid.uuid4().hex[:12]
        db.execute("INSERT INTO chat_sessions (id, title) VALUES (?, ?)", (session_id, message[:60]))
    db.execute("INSERT INTO chat_messages (session_id, role, content) VALUES (?, 'user', ?)", (session_id, message))
    messages = [{"role": "system", "content": SYSTEM_PROMPT.format(tiles=_tile_summary())}, *_history(session_id)]

    async def events() -> AsyncIterator[str]:
        yield _sse("session", {"id": session_id})
        reply = []
        try:
            async for text in _stream_from_hermes(messages):
                reply.append(text)
                yield _sse("delta", {"text": text})
        except httpx.HTTPError as e:
            yield _sse("error", {"message": _error_message(e)})
        except Exception as e:
            log.exception("Chat stream failed")
            yield _sse("error", {"message": f"Hermes's reply broke off ({type(e).__name__}). Try again."})
        finally:
            if reply:
                # The session may have been deleted while Hermes was replying; then there's nothing to save.
                db.execute("INSERT INTO chat_messages (session_id, role, content) SELECT ?, 'assistant', ? "
                           "WHERE EXISTS (SELECT 1 FROM chat_sessions WHERE id = ?)",
                           (session_id, "".join(reply), session_id))
            db.execute("UPDATE chat_sessions SET updated_at = datetime('now') WHERE id = ?", (session_id,))
        yield _sse("done", {})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/sessions")
def sessions():
    rows = db.query("SELECT id, title, updated_at FROM chat_sessions ORDER BY updated_at DESC LIMIT 50")
    return [dict(r) for r in rows]


@router.get("/sessions/{session_id}")
def session_messages(session_id: str):
    rows = db.query("SELECT role, content, created_at FROM chat_messages WHERE session_id = ? ORDER BY id",
                    (session_id,))
    return [dict(r) for r in rows]


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str):
    db.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
    return {"ok": True}
