"""App factory. Run with: uvicorn app.main:create_app --factory"""

import logging
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from . import approvals, assistant, db, integrations, layout, mcp_server, tiles
from .config import settings


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    s = settings()
    db.connect(s.data_dir / "launchpad.db")
    registry = integrations.load(s.config_dir / "plugins")
    layout.seed(s.config_dir)
    mcp, mcp_routes = mcp_server.build(registry)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="Launchpad", lifespan=lifespan)
    app.add_middleware(mcp_server.McpAuth, token=s.mcp_token)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=s.allowed_hosts)
    app.add_middleware(SameOriginOnly)
    for router in (layout.router, tiles.router, approvals.router, assistant.router):
        app.include_router(router)
    for router in {id(i.router): i.router for i in registry.values() if i.router}.values():
        app.include_router(router)
    app.router.routes.extend(mcp_routes)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    _serve_frontend(app)
    return app


SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class SameOriginOnly:
    """Refuse state-changing /api requests sent by other websites (CSRF). There is no login,
    so without this any page the user visits could edit the board or press tile buttons.
    Plain ASGI rather than BaseHTTPMiddleware, so streamed chat replies pass straight through."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] not in SAFE_METHODS and scope["path"].startswith("/api/"):
            headers = Headers(scope=scope)
            site, origin = headers.get("sec-fetch-site"), headers.get("origin")
            if site is not None:
                cross = site not in ("same-origin", "none")
            else:  # older browsers: compare Origin with Host
                cross = origin is not None and urlsplit(origin).netloc != headers.get("host")
            if cross:
                response = PlainTextResponse("Requests from other websites are refused.", status_code=403)
                return await response(scope, receive, send)
        await self.app(scope, receive, send)


def _serve_frontend(app: FastAPI) -> None:
    """Serve the built Svelte app, falling back to index.html for client-side routes."""
    static = settings().static_dir
    if not (static / "index.html").is_file():
        return
    if (static / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path == "api" or path.startswith(("api/", "mcp")):
            raise HTTPException(404, "Not found")
        file = (static / path).resolve()
        if path and file.is_file() and file.is_relative_to(static.resolve()):
            return FileResponse(file)
        # Always revalidate the page, so a rebuilt app shows up on the next reload. Its scripts
        # and styles have content-hashed names, so they can still be cached.
        return FileResponse(static / "index.html", headers={"Cache-Control": "no-cache"})
