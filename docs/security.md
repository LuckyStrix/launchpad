# Security notes

Launchpad is a single-user app, and your tailnet is the access boundary. Read this before exposing it more widely.

- **Network:** the app shares the Tailscale sidecar's network namespace and publishes no host ports. It's reachable from your tailnet (HTTPS via `tailscale serve`, or plain HTTP on the node's port 8080) and from containers on the `launchpad-hermes` network. Use Tailscale ACLs to limit which devices can reach the `launchpad` node, for example by tagging it with `TS_EXTRA_ARGS=--advertise-tags=tag:launchpad`.
- **No login:** anyone who can reach the page can use every tile and the chat. Don't expose it with Funnel, or anywhere else outside the tailnet.
- **MCP:** `/mcp` requires `LAUNCHPAD_MCP_TOKEN` as a bearer token, and stays off until you set one.
- **Host check:** Launchpad only answers requests addressed to `localhost`, `launchpad`, `*.ts.net`, or hosts listed in `LAUNCHPAD_ALLOWED_HOSTS`. This stops a web page from reaching it through DNS rebinding.
- **Write actions:** anything Hermes does through Launchpad that changes state waits for an explicit approval on the page. Every tool call is recorded in the activity log (`/api/activity`). Home Assistant control through Hermes's own toolset is governed by Hermes, not Launchpad.
- **Tile actions:** the browser names a tile and an action. Launchpad then uses the tile's saved config, so a request can't point an action at an arbitrary entity or repository. A Home Assistant tile's buttons may only call the services its controls use (on/off, run, open/close, lock/unlock, play/pause, set temperature) with the fields they use (brightness, temperature), so a request can't add other targets such as `area_id` or `device_id`. To add a new kind of control, extend `ALLOWED_SERVICES` and `ALLOWED_FIELDS` in `backend/app/integrations/homeassistant.py`.
- **Secrets:** API tokens live in `.env`, which is gitignored and never sent to the browser. The Google refresh token lives in the SQLite database, in the `launchpad-data` volume. Tile config is exportable YAML, so it must never contain secrets.
- **Public repo:** `.env`, `data/`, `config/layout.yaml` and `config/plugins/*.py` are gitignored. CI and the optional pre-commit hook run gitleaks.
- **Link health checks** skip TLS verification, because self-signed certificates are common on self-hosted apps. They only report reachability.
- **Icons:** icons written as `si:` load from cdn.simpleicons.org, and favicons load from each site. Use emoji or local image URLs to avoid both.
