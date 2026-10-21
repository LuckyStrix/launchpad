# Launchpad: Extensible Personal Homepage

A tile-based homepage for quick access to websites, repos, and tools, with an AI assistant (Hermes) that can act on some of them. Runs in Docker on an always-on PC and is reachable only over Tailscale. The repo is public, so no secrets or personal config are ever committed.

## Goals

- Tile dashboard: self-hosted sites, GitHub repos, Google Calendar and Tasks, Home Assistant devices, Hermes
- **Hermes is the assistant's brain.** Launchpad does not call an LLM itself
- Home Assistant device control, both as direct tiles and through the assistant
- **Tiles are editable in the UI** (add, remove, reorder, resize, configure)
- One `docker compose up`, accessible only on the tailnet
- Adding an integration is one folder, with no core changes

## Stack

| Layer | Choice | Why |
|---|---|---|
| Backend | Python + FastAPI | Good client libraries, SSE streaming, easy MCP server |
| Frontend | Svelte (Vite) | Small bundle, easy drag-and-drop tile components |
| State | SQLite (mounted volume) | Layout, tile config, tokens, chat sessions, audit log |
| Networking | Tailscale sidecar container + `tailscale serve` | HTTPS on the tailnet, no published host ports |
| Assistant | Hermes Agent API server (OpenAI-compatible `/v1`) | Hermes is the brain |
| Smart home | Home Assistant REST + WebSocket API | Direct tiles; Hermes's `hermes-homeassistant` toolset for chat control |

## Architecture

```
Browser (tailnet) --HTTPS--> tailscale sidecar --> Launchpad (FastAPI + static UI)
                                                     |-- /api/layout        tile layout CRUD
                                                     |-- /api/tiles/*       resolved tile data
                                                     |-- /api/actions/*     tile buttons
                                                     |-- /api/assistant     SSE proxy to Hermes
                                                     |-- /mcp               integration tools for Hermes
                                                     `-- SQLite volume

Launchpad --SSE--> Hermes container (/v1, bearer key)
                     |-- hermes-homeassistant toolset --> Home Assistant
                     `-- MCP client --> Launchpad /mcp (GitHub, Google, ...)
```

### Plugin model

Each integration lives in `backend/integrations/<name>/`:

- `manifest.yaml`: id, tile types, a config schema (drives the tile editor form), required secrets
- `tile.py`: `async def fetch(config) -> TileData` (status, list, or value)
- `actions.py` (optional): buttons on a tile, such as "run workflow" or "toggle light"
- `tools.py` (optional): tools exposed to Hermes through Launchpad's MCP server; each has a JSON schema and a `requires_approval` flag

The loader discovers folders at startup.

### Tile types

1. **Link**: icon, label, URL, optional health ping (green or red dot). Covers self-hosted sites.
2. **Widget**: live data from an integration (calendar agenda, tasks, CI status, HA entity state).
3. **Action**: a button that does something (dispatch workflow, activate scene).
4. **Group**: a named section that holds tiles.

## Tile editor (core feature)

- Edit-mode toggle. Drag to reorder, resize (1x1, 2x1, 2x2), and move tiles between groups.
- "Add tile" picks a type or integration, then shows a form generated from that integration's config schema.
  - HA tile: searchable entity picker backed by `/api/states`
  - GitHub tile: repo picker
  - Link tile: URL, icon, health-check toggle
- Layout and tile config persist to SQLite through `/api/layout` (Pydantic-validated). Export and import as YAML.
- Undo, and "discard changes" back to the last saved layout.
- Secrets never pass through tile config. They live in `.env` or the settings table.

## Hermes integration

- Chat drawer in the UI streams to Hermes `POST /v1/chat/completions` through a Launchpad SSE proxy, so the API key stays server-side.
- Hermes setup (done later on the host, in `~/.hermes/.env`): `API_SERVER_ENABLED=true`, `API_SERVER_KEY=<secret>`, `API_SERVER_HOST` set so Launchpad's container can reach it, then `hermes gateway`.
- Launchpad is reachable from the Hermes container over a shared Docker network (preferred) or the tailnet.
- **Giving Hermes access to Launchpad projects:** Launchpad serves an MCP server at `/mcp`, built from each enabled integration's `tools.py`. Hermes adds it to its MCP config. Integrations are written once and serve both tiles and the assistant.
  - Unverified: Hermes's MCP configuration details are thinly documented. First task in milestone 5 is to test this against the live container. Fallback: Hermes skills that call Launchpad's REST actions.
- **Approvals:** Launchpad's write-capable MCP tools return a pending result and wait for a click in the UI before executing. Hermes-native tools (including HA service calls) use Hermes's own approval settings.
- Unverified until tested against a live gateway: sessions and conversation continuity, how tool progress appears in the stream, and how the HA URL is configured on the Hermes side.

## Integrations (priority order)

1. **Static links + health checks.** MVP, no auth.
2. **Home Assistant.** Long-lived token. Live state for light, switch, climate, sensor, lock; toggle, slider, and scene/script actions. HA is a tailnet node, so no public exposure.
3. **GitHub.** Fine-grained PAT. Repo tiles, CI status, open PRs and issues, workflow-dispatch action.
4. **Google Calendar and Tasks.** OAuth; refresh token stored in SQLite. Read-only first, then writes (add task) behind approval.
5. **Hermes status.** Health tile for the gateway.
6. **Later:** Docker container status (read-only socket), arbitrary scripts.

## Docker and Tailscale

```yaml
services:
  tailscale:
    image: tailscale/tailscale
    environment: [TS_AUTHKEY, TS_HOSTNAME=home, TS_SERVE_CONFIG, TS_STATE_DIR]
    volumes: [ts-state:/var/lib/tailscale, ./serve.json:/config/serve.json]
  app:
    build: .
    network_mode: service:tailscale
    env_file: .env          # gitignored
    volumes: [./config:/app/config, data:/app/data]
```

- Own Tailscale node (`home.<tailnet>.ts.net`), because the host's `tailscale serve` already uses ports 443 and 8443.
- No host ports are published. Use Tailscale ACLs to limit who can reach the node.
- Multi-stage Dockerfile: build the frontend, then a slim Python runtime as a non-root user.

## Public-repo hygiene

- Gitignored: `.env`, `config/`, `data/`. Committed: `.env.example`, `config/tiles.example.yaml`.
- `gitleaks` pre-commit hook and CI check.
- MIT license, README with screenshots, `docs/adding-an-integration.md`.
- GitHub Actions: lint, tests, Docker build.

## Repo layout

```
launchpad/
|-- backend/        app/, integrations/, tests/
|-- frontend/
|-- config/tiles.example.yaml
|-- docs/           PLAN.md, adding-an-integration.md, security.md
|-- docker-compose.yml, Dockerfile
`-- .env.example
```

## Milestones

1. **Skeleton + Docker + Tailscale.** FastAPI, UI shell, seeded link tiles, Dockerfile, compose with Tailscale sidecar, working `https://home.<tailnet>.ts.net`.
2. **Plugin loader + SQLite layout + tile editor.** Drag and drop, add/edit form from manifest schemas, health dots, YAML export/import.
3. **Home Assistant integration.** REST + WebSocket, entity picker, toggles, scenes. *Needs HA long-lived token.*
4. **GitHub integration.** Repo and CI tiles, workflow dispatch.
5. **Hermes bridge.** Chat drawer streaming to `/v1`; verify API shape against the live container; Hermes setup notes.
6. **Launchpad MCP server for Hermes.** Expose integration tools, UI approval flow for writes.
7. **Google Calendar and Tasks.** OAuth, widgets, MCP tools.
8. **Polish.** PWA install, theming, backup of `data/`, docs, gitleaks, CI.

## Open items

- Hermes container setup on the host (deferred; to be done together).
- HA long-lived token (needed at milestone 3).
- Hermes MCP client configuration and exact `/v1` behavior (verified in milestone 5).
- Docker group membership takes effect after re-login; builds and runs wait until then (`sg docker -c ...` can work around it).
