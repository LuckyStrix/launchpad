# Adding an integration

An integration is one Python class. The frontend renders tiles generically from the data you return, so adding an integration needs no frontend code.

There are two places it can live:
- **`backend/app/integrations/<name>.py`** is for integrations that belong in the repo.
- **`config/plugins/<name>.py`** is for personal ones. That folder is gitignored, apart from `*.example.py` files, and it's mounted into the container. Restart to load changes.

## The smallest version

```python
from app.integrations.base import Field, Integration, TileData, http


class Weather(Integration):
    id = "weather"                 # stable, used in saved layouts
    name = "Weather"               # shown in the Add tile picker
    description = "Current conditions for a place."
    icon = "⛅"                    # emoji, image URL, or "si:<simple-icons slug>"
    refresh = 600                  # seconds between tile refreshes
    fields = [Field(key="city", label="City", required=True)]

    async def fetch(self, config):
        async with http() as client:
            data = (await client.get(f"https://wttr.in/{config['city']}?format=j1")).json()
        now = data["current_condition"][0]
        return TileData(status="ok", value=f"{now['temp_C']}°", detail=now["weatherDesc"][0]["value"])


integration = Weather()
```

That's all it takes. **Add tile** now lists Weather, with a City field.

## What a tile can show

`fetch(config)` returns a `TileData`:

| Field | Use |
|---|---|
| `status` | `ok`, `warn`, `error` or `idle`. This sets the colour of the tile's indicator lamp. |
| `value` | The big readout. |
| `detail` | One secondary line. |
| `items` | List rows: `Item(label, detail, url, status)`. Wide and tall tiles show more of them. An `Item` with `action=` gets a button that calls that action with the item's `params`. |
| `controls` | `Control(action, label, kind="button" \| "toggle" \| "slider", value, min, max, params)`. |
| `url` | Where clicking the tile goes. `#assistant` opens the chat. |
| `error` | A message to show instead of data. |

Raise `IntegrationError("...")` for expected failures. The message is shown on the tile as-is, so write it for the user. For example: "Home Assistant rejected HASS_TOKEN."

## Editor fields

A `Field` has a `type`, which is one of `text`, `url`, `number`, `bool`, `select` (with `options=[{"value", "label"}]`), `textarea` or `picker`.

A **picker** searches as you type by calling `options(field, query)`, which returns `Option(value, label, detail)`. Picking an option also names the tile, if it doesn't have a name yet.

```python
    async def options(self, field, query):
        return [Option(value=c, label=c.title()) for c in CITIES if query in c]
```

## Buttons, toggles and sliders

Decorate a method with `@action`. The frontend calls it with the tile's saved config and the control's params. Return fresh `TileData` (or `None` to keep the old one).

```python
    @action
    async def refresh_now(self, config, params):
        return await self.fetch(config)
```

Actions only ever see the config saved in the layout, never config sent by the browser.

## Tools for Hermes

Decorate a typed async method with `@tool()`. Launchpad serves it over MCP as `<integration id>_<method name>`, and the docstring becomes the tool's description. Return anything JSON-serialisable.

```python
    @tool()
    async def forecast(self, city: str, days: int = 3) -> dict:
        """Weather forecast for a city."""

    @tool(write=True)
    async def set_home_city(self, city: str) -> str:
        """Change the default city."""
```

`write=True` tools wait for the user to click **Approve** on the page before they run. A denial, or no answer before `APPROVAL_TIMEOUT`, is reported back to Hermes as declined. Use `write=True` for anything that changes state.

Tools are only offered when the integration is configured.

## Secrets and routes

- **Secrets:** set `requires = ("MY_API_KEY",)`. Tiles tell the user which variables are missing, and `self.env("MY_API_KEY")` reads a value. Never put secrets in tile config, because tile config is exported to YAML.
- **Routes:** set `router = APIRouter(prefix="/api/<id>")` for extra HTTP routes. The Google integration uses this for its OAuth callback.

## Testing

Integrations make HTTP calls through `base.http()`, so tests can route them to a fake. See `backend/tests/test_tiles.py`:

```python
def test_weather(client, mock_http):
    mock_http(lambda request: httpx.Response(200, json={...}))
    data = client.get("/api/tiles/<tile id>/data").json()
```
