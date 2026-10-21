import asyncio
import json

import httpx
from fastapi.testclient import TestClient

from app import approvals
from app.config import settings


def _events(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_chat_streams_and_stores_reply(client, mock_http):
    chunks = [{"choices": [{"delta": {"content": part}}]} for part in ("Hel", "lo")]
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    seen = mock_http(lambda request: httpx.Response(200, text=body, headers={"content-type": "text/event-stream"}))

    events = _events(client.post("/api/assistant/chat", json={"message": "hi"}).text)

    assert [e for e, _ in events] == ["session", "delta", "delta", "done"]
    sent = json.loads(seen[0].content)
    assert seen[0].headers["authorization"] == "Bearer hermes-key"
    assert sent["stream"] is True and sent["messages"][0]["role"] == "system"
    assert "Lamp" in sent["messages"][0]["content"]  # tiles are summarized for Hermes

    session = events[0][1]["id"]
    stored = client.get(f"/api/assistant/sessions/{session}").json()
    assert [(m["role"], m["content"]) for m in stored] == [("user", "hi"), ("assistant", "Hello")]


def test_chat_explains_unreachable_hermes(client, mock_http):
    def refuse(request):
        raise httpx.ConnectError("refused")

    mock_http(refuse)
    events = _events(client.post("/api/assistant/chat", json={"message": "hi"}).text)
    assert events[1][0] == "error"
    assert "isn't reachable" in events[1][1]["message"]


def test_approval_gate(client):
    async def scenario():
        waiting = asyncio.create_task(approvals.request("github_create_issue", {"title": "x"}, timeout=5))
        await asyncio.sleep(0)
        [pending] = approvals.list_pending()
        approvals.decide(pending["id"], approvals.Decision(approve=True))
        return await waiting

    assert asyncio.run(scenario()) is True


def test_approval_times_out_as_denied(client):
    assert asyncio.run(approvals.request("github_create_issue", {}, timeout=0.01)) is False
    assert client.get("/api/activity").json()[0]["status"] == "expired"


MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json",
               "mcp-protocol-version": "2025-06-18"}


def _rpc(client, method, params=None, token="mcp-token"):
    headers = {**MCP_HEADERS, **({"authorization": f"Bearer {token}"} if token else {})}
    return client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": method,
                                                       "params": params or {}})


def _result(response) -> dict:
    text = response.text
    if "data:" in text:
        text = next(line[5:] for line in text.splitlines() if line.startswith("data:"))
    return json.loads(text)["result"]


def test_mcp_requires_token(client):
    assert _rpc(client, "tools/list", token=None).status_code == 401
    assert _rpc(client, "tools/list", token="wrong").status_code == 401


def test_mcp_is_off_without_a_token(client, monkeypatch):
    from app.main import create_app

    monkeypatch.setenv("LAUNCHPAD_MCP_TOKEN", "")
    settings.cache_clear()
    with TestClient(create_app()) as c:
        assert _rpc(c, "tools/list", token=None).status_code == 503


def test_unknown_host_is_rejected(client):
    assert client.get("/api/health", headers={"host": "evil.example"}).status_code == 400
    assert client.get("/api/health", headers={"host": "launchpad.tail1234.ts.net"}).status_code == 200


def test_mcp_lists_and_calls_tools(client, mock_http):
    init = _rpc(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                       "clientInfo": {"name": "test", "version": "1"}})
    assert init.status_code == 200, init.text

    names = {t["name"] for t in _result(_rpc(client, "tools/list"))["tools"]}
    assert {"github_repo_status", "github_create_issue"} <= names
    assert not any(n.startswith("google_") for n in names)  # not configured, so not offered

    mock_http(lambda request: httpx.Response(200, json=[
        {"full_name": "me/proj", "description": None, "private": False, "pushed_at": "2026-10-01"}]))
    result = _result(_rpc(client, "tools/call", {"name": "github_list_repos", "arguments": {}}))
    assert "me/proj" in json.dumps(result)


def test_cross_site_writes_are_refused(client):
    assert client.post("/api/layout/import", content="groups: []",
                       headers={"sec-fetch-site": "cross-site", "content-type": "text/plain"}).status_code == 403
    assert client.post("/api/google/disconnect", headers={"origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/google/disconnect", headers={"sec-fetch-site": "same-origin"}).status_code == 200


def test_tool_errors_update_their_own_activity_row(client, mock_http):
    mock_http(lambda request: httpx.Response(404, json={"message": "Not Found"}))
    _rpc(client, "tools/call", {"name": "github_repo_status", "arguments": {"repo": "me/gone"}})

    rows = client.get("/api/activity").json()
    assert [r["status"] for r in rows if r["name"] == "github_repo_status"] == ["error"]


def test_unknown_api_paths_are_404_not_the_page(client):
    from app.main import create_app

    (settings().static_dir).mkdir(parents=True, exist_ok=True)
    (settings().static_dir / "index.html").write_text("<!doctype html>")
    with TestClient(create_app()) as c:
        assert c.get("/some/page").status_code == 200
        assert c.get("/api/nope").status_code == 404


def test_non_ascii_mcp_token_is_401_not_500(client):
    headers = {**MCP_HEADERS, "authorization": "Bearer \xff".encode("latin-1")}
    assert client.post("/mcp", headers=headers, json={}).status_code == 401
