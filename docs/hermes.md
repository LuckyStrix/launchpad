# Connecting Hermes

Hermes is Launchpad's assistant. The two connect in both directions:

```
Launchpad chat drawer ──(OpenAI-compatible API)──> Hermes        Hermes → Launchpad: MCP tools
                                                      │
Hermes ──(MCP, http://launchpad:8080/mcp)──> Launchpad's GitHub / Google tools
Hermes ──(its own hermes-homeassistant toolset)──> Home Assistant
```

Both containers join the `launchpad-hermes` Docker network, which `make up` creates. Nothing has to be exposed on the host.

## 1. Turn on Hermes's API server

In Hermes's `~/.hermes/.env` (inside its container or volume):

```sh
API_SERVER_ENABLED=true
API_SERVER_KEY=<a long random string>   # openssl rand -hex 32
API_SERVER_HOST=0.0.0.0                  # listen beyond the container's localhost
API_SERVER_PORT=8642
```

`hermes gateway` must be running. Check it with `curl http://<hermes>:8642/health`, which should return `{"status": "ok"}`.

## 2. Put Hermes on the shared network

Do this once, with the alias `hermes`, which matches `HERMES_URL=http://hermes:8642/v1`:

```sh
docker network connect --alias hermes launchpad-hermes <hermes-container-name>
```

If Hermes is defined in a compose file, you can add the network there instead:

```yaml
services:
  hermes:
    networks:
      default:
      launchpad-hermes:
        aliases: [hermes]
networks:
  launchpad-hermes:
    external: true
```

## 3. Tell Launchpad about Hermes

In Launchpad's `.env`:

```sh
HERMES_URL=http://hermes:8642/v1
HERMES_API_KEY=<same value as API_SERVER_KEY>
```

Then run `make up`. The Hermes tile turns green, and the chat drawer works.

## 4. Give Hermes Launchpad's tools (MCP)

Set `LAUNCHPAD_MCP_TOKEN` in Launchpad's `.env`. Then add Launchpad as a streamable-HTTP MCP server in Hermes's MCP config:

- **URL:** `http://launchpad:8080/mcp`
- **Header:** `Authorization: Bearer <LAUNCHPAD_MCP_TOKEN>`

After that, run `/reload-mcp` in Hermes, or restart the gateway. Hermes now sees tools such as `github_repo_status`, `google_calendar_events` and `google_tasks_add`. Tools that change something show an approval card in Launchpad's chat drawer, and the **Hermes** button gets a badge while one is waiting.

Not yet verified against a live Hermes:
- the exact MCP config syntax
- whether Hermes keeps its own session state across `/v1/chat/completions` calls (Launchpad sends the full history every time, so it works either way)

## 5. Home Assistant in Hermes

Launchpad's Home Assistant tiles talk to HA directly. For chat control ("turn off the lights"), enable Hermes's `hermes-homeassistant` toolset with `HASS_TOKEN`, plus its HA URL setting, in Hermes's environment.

## Troubleshooting

| You see | Check |
|---|---|
| "Hermes isn't reachable at …" | Hermes is on `launchpad-hermes` with alias `hermes`, and `API_SERVER_HOST=0.0.0.0` |
| "Hermes rejected the API key" | `HERMES_API_KEY` equals `API_SERVER_KEY` |
| Hermes can't list Launchpad tools | The bearer token, and that the URL ends in `/mcp`. Unconfigured integrations offer no tools. |
| Approvals never appear | Launchpad's page must be open. Requests expire after `APPROVAL_TIMEOUT` seconds. |
