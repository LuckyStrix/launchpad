"""Launchpad's MCP server: every `@tool` method of a configured integration, served at /mcp
so Hermes can call it. Tools are named `<integration id>_<method name>`."""

import functools
import hmac
import inspect
import json
from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.routing import BaseRoute
from starlette.types import ASGIApp, Receive, Scope, Send

from . import approvals, db
from .integrations.base import Integration, IntegrationError, changed

INSTRUCTIONS = (
    "Tools for the user's Launchpad homepage: their GitHub repos, Google Calendar and Google Tasks. "
    "Tools that change something wait for the user to approve them on the Launchpad page; if one comes back "
    "declined, tell the user and don't retry unless they ask."
)


def _wrap(integration: Integration, fn: Callable, name: str) -> Callable:
    write = fn._launchpad_tool["write"]

    @functools.wraps(fn)
    async def run(**kwargs: Any) -> Any:
        # One activity row per call: pending -> approved/denied/expired -> done/error (writes),
        # or running -> done/error (reads).
        args = json.dumps(kwargs)
        activity_id = db.log_activity("tool", name, "pending" if write else "running", args)
        if write and not await approvals.request(name, kwargs, activity_id=activity_id):
            return f"The user declined {name}, or didn't answer in time. Nothing was changed."
        try:
            result = await fn(**kwargs)
        except IntegrationError as e:
            db.set_activity_status(activity_id, "error", f"{args}\n{e}")
            return f"Error: {e}"
        except BaseException as e:
            db.set_activity_status(activity_id, "error", f"{args}\n{type(e).__name__}: {e}")
            raise
        db.set_activity_status(activity_id, "done")
        if write:
            changed(integration.source)  # the user's tiles for this service refresh within seconds
        return result

    return run


def build(registry: dict[str, Integration]) -> tuple[MCPServer, list[BaseRoute]]:
    server = MCPServer("launchpad", instructions=INSTRUCTIONS)
    for integration in registry.values():
        if not integration.configured:
            continue
        for fn in integration.tools():
            name = f"{integration.id}_{fn.__name__}"
            server.add_tool(_wrap(integration, fn, name), name=name, description=inspect.getdoc(fn))
    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        # Hermes reaches us by container name, not localhost; McpAuth below guards access instead.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    return server, list(app.routes)


class McpAuth:
    """Require `Authorization: Bearer <LAUNCHPAD_MCP_TOKEN>` on /mcp. Without a token, /mcp is off."""

    def __init__(self, app: ASGIApp, token: str):
        self.app, self.token = app, token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"].startswith("/mcp"):
            if not self.token:
                return await self._reject(send, 503, b"MCP is off. Set LAUNCHPAD_MCP_TOKEN to turn it on.")
            given = dict(scope["headers"]).get(b"authorization", b"")
            if not hmac.compare_digest(given, f"Bearer {self.token}".encode()):
                return await self._reject(send, 401, b"Missing or wrong MCP token.")
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(send: Send, status: int, body: bytes) -> None:
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": body})
