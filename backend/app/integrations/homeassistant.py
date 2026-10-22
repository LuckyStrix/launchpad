"""Home Assistant entities as tiles. Assistant control of HA goes through Hermes's own
`hermes-homeassistant` toolset, so this integration exposes no tools."""

import re
from typing import Any

import httpx

from .base import Control, Field, Integration, IntegrationError, Item, Option, TileData, action, http

TOGGLEABLE = {"light", "switch", "fan", "input_boolean", "automation", "siren", "humidifier"}
RUNNABLE = {"scene": ("turn_on", "Activate"), "script": ("turn_on", "Run"), "button": ("press", "Press"),
            "input_button": ("press", "Press")}
ACTIVE_STATES = {"on", "open", "opening", "home", "playing", "heat", "cool", "heat_cool", "auto"}
SERVICE_NAME = re.compile(r"^[a-z_]+$")
# What a tile's controls may send, and nothing else: a button press can't add targets such as
# area_id or device_id (which would reach other devices) or call unrelated services.
ALLOWED_SERVICES = {"turn_on", "turn_off", "toggle", "press", "open_cover", "close_cover", "lock", "unlock",
                    "media_play_pause", "set_temperature"}
ALLOWED_FIELDS = {"brightness_pct", "temperature"}
DOMAIN_ICONS = {"light": "💡", "switch": "🔌", "fan": "🌀", "climate": "🌡️", "cover": "🪟", "lock": "🔒",
                "media_player": "🔈", "scene": "🎬", "script": "📜", "automation": "⚙️", "button": "🔘",
                "input_button": "🔘", "input_boolean": "🔘", "binary_sensor": "◉", "person": "👤",
                "humidifier": "💧", "siren": "🚨", "vacuum": "🧹"}
SENSOR_ICONS = {"temperature": "🌡️", "humidity": "💧", "power": "⚡", "energy": "⚡", "battery": "🔋",
                "illuminance": "☀️"}


def _number(value: Any) -> str:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(n)) if n.is_integer() else f"{n:.1f}"


class HomeAssistant(Integration):
    id = "homeassistant"
    name = "Home Assistant"
    description = "Show and control a device, sensor, scene or script."
    icon = "si:homeassistant"
    requires = ("HASS_URL", "HASS_TOKEN")
    refresh = 10
    fields = [Field(key="entity_id", label="Entity", type="picker", required=True,
                    help="Search by name or entity ID.")]

    async def _request(self, method: str, path: str, json: dict | None = None) -> Any:
        base = self.env("HASS_URL").rstrip("/")
        headers = {"Authorization": f"Bearer {self.env('HASS_TOKEN')}"}
        try:
            async with http(base_url=base, headers=headers) as client:
                response = await client.request(method, f"/api{path}", json=json)
        except httpx.HTTPError:
            raise IntegrationError(f"Couldn't reach Home Assistant at {base}.")
        if response.status_code == 401:
            raise IntegrationError("Home Assistant rejected HASS_TOKEN.")
        if response.status_code in (400, 404):
            raise IntegrationError("Home Assistant doesn't know that entity or service.")
        if response.is_error:
            raise IntegrationError(f"Home Assistant returned HTTP {response.status_code}.")
        return response.json()

    def _entity(self, config: dict) -> str:
        entity_id = config.get("entity_id") or ""
        if "." not in entity_id:
            raise IntegrationError("Choose an entity in the tile settings.")
        return entity_id

    async def fetch(self, config):
        return self._render(await self._request("GET", f"/states/{self._entity(config)}"))

    def _render(self, state: dict) -> TileData:
        entity_id, value, attrs = state["entity_id"], state["state"], state.get("attributes", {})
        domain = entity_id.split(".")[0]
        unit = attrs.get("unit_of_measurement")

        status = "ok" if value in ACTIVE_STATES else "idle"
        if value in ("unavailable", "unknown"):
            status = "warn"
        readout = f"{_number(value)} {unit}" if unit else value.replace("_", " ")
        detail = attrs.get("friendly_name")
        controls: list[Control] = []

        if domain in TOGGLEABLE:
            controls.append(Control(action="call", label="Power", kind="toggle", value=value == "on"))
        if domain == "light" and value == "on" and attrs.get("brightness") is not None:
            controls.append(Control(action="call", label="Brightness", kind="slider", min=1, max=100,
                                    value=round(attrs["brightness"] / 255 * 100),
                                    params={"service": "turn_on", "field": "brightness_pct"}))
        if domain in RUNNABLE:
            # A scene's or script's state is just when it last ran; the name says more.
            service, label = RUNNABLE[domain]
            readout, status = None, "idle"
            controls.append(Control(action="call", label=label, params={"service": service}))
        if domain == "cover":
            controls += [Control(action="call", label="Open", params={"service": "open_cover"}),
                         Control(action="call", label="Close", params={"service": "close_cover"})]
        if domain == "lock":
            locked = value == "locked"
            controls.append(Control(action="call", label="Unlock" if locked else "Lock",
                                    params={"service": "unlock" if locked else "lock"}))
        if domain == "media_player":
            controls.append(Control(action="call", label="Play/pause", params={"service": "media_play_pause"}))
        if domain == "climate":
            readout = f"{_number(attrs.get('current_temperature'))}°"
            target = attrs.get("temperature")
            if target is not None:
                step = attrs.get("target_temp_step") or 0.5
                detail = f"Set to {_number(target)}°, {value}"
                controls += [
                    Control(action="call", label="−", params={"service": "set_temperature",
                                                              "data": {"temperature": target - step}}),
                    Control(action="call", label="+", params={"service": "set_temperature",
                                                              "data": {"temperature": target + step}}),
                ]

        public = (self.env("HASS_PUBLIC_URL") or self.env("HASS_URL")).rstrip("/")
        return TileData(status=status, value=readout, detail=detail, controls=controls,
                        url=f"{public}/history?entity_id={entity_id}")

    @action
    async def call(self, config, params):
        """Call a service on this tile's entity. Toggles send `value`; sliders send `field` + `value`."""
        entity_id = self._entity(config)
        domain = entity_id.split(".")[0]
        service = params.get("service")
        if service is None and isinstance(params.get("value"), bool):
            service = "turn_on" if params["value"] else "turn_off"
        if not service or not SERVICE_NAME.match(service) or service not in ALLOWED_SERVICES:
            raise IntegrationError("That control is missing a valid service.")

        data = dict(params.get("data") or {})
        if params.get("field") and "value" in params:
            data[params["field"]] = params["value"]
        if set(data) - ALLOWED_FIELDS or not all(isinstance(v, int | float) for v in data.values()):
            raise IntegrationError("That control sent settings this tile doesn't use.")
        changed = await self._request("POST", f"/services/{domain}/{service}",
                                      json={**data, "entity_id": entity_id})
        for state in changed or []:
            if state.get("entity_id") == entity_id:
                return self._render(state)
        return await self.fetch(config)

    async def options(self, field, query):
        if field not in ("entity_id", "entities"):
            return []
        query = query.lower()
        found = []
        for state in await self._request("GET", "/states"):
            name = state.get("attributes", {}).get("friendly_name") or state["entity_id"]
            if query in state["entity_id"].lower() or query in name.lower():
                found.append(Option(value=state["entity_id"], label=name, detail=state["entity_id"]))
        return sorted(found, key=lambda o: o.label.lower())[:50]


class HomeAssistantList(HomeAssistant):
    """Several entities in one tile: a row each with its state, and an on/off switch for lights and
    switches (or a Run button for scenes and scripts). For full controls, use one Home Assistant tile."""

    id = "homeassistant_list"
    shares_source_with = "homeassistant"
    name = "Home Assistant group"
    description = "Several devices and sensors in one tile, as tappable buttons or a list."
    default_size = "2x2"
    fields = [
        Field(key="entities", label="Entities", type="multipicker", required=True,
              help="Search by name or entity ID. Drag the chips, or use the arrow keys on one, to arrange them."),
        Field(key="style", label="Show as", type="select", default="grid",
              options=[{"value": "grid", "label": "Buttons, like a phone widget"}, {"value": "list", "label": "List"}]),
    ]

    def _chosen(self, config: dict) -> list[str]:
        chosen = config.get("entities") or []
        if not chosen:
            raise IntegrationError("Choose entities in the tile settings.")
        return [chosen] if isinstance(chosen, str) else chosen

    async def fetch(self, config):
        chosen = self._chosen(config)
        states = {s["entity_id"]: s for s in await self._request("GET", "/states") if s["entity_id"] in chosen}
        items = [self._row(entity_id, states.get(entity_id)) for entity_id in chosen]
        switches = [i for i in items if i.kind == "switch"]
        on = sum(1 for i in switches if i.on)
        public = (self.env("HASS_PUBLIC_URL") or self.env("HASS_URL")).rstrip("/")
        grid = config.get("style") == "grid"  # tiles saved before the option existed are lists
        return TileData(status="ok" if on else "idle", items=items, url=public, layout="grid" if grid else "list",
                        value=f"{on} of {len(switches)} on" if switches and not grid else None)

    def _row(self, entity_id: str, state: dict | None) -> Item:
        if state is None:
            return Item(label=entity_id, detail="Not found in Home Assistant", status="warn")
        value, attrs = state["state"], state.get("attributes", {})
        domain = entity_id.split(".")[0]
        name = attrs.get("friendly_name") or entity_id
        unit = attrs.get("unit_of_measurement")
        status = "warn" if value in ("unavailable", "unknown") else "ok" if value in ACTIVE_STATES else "idle"
        icon = DOMAIN_ICONS.get(domain) or SENSOR_ICONS.get(attrs.get("device_class"), "📟")
        if domain in TOGGLEABLE:
            return Item(label=name, status=status, kind="switch", on=value == "on", action="flip", icon=icon,
                        detail="On" if value == "on" else "Off" if value == "off" else value,
                        action_label="Turn off" if value == "on" else "Turn on", params={"entity_id": entity_id})
        if domain in RUNNABLE:
            return Item(label=name, kind="button", action="flip", action_label=RUNNABLE[domain][1], icon=icon,
                        params={"entity_id": entity_id})
        if domain == "climate":
            detail = f"{_number(attrs.get('current_temperature'))}°, {value}"
        else:
            detail = f"{_number(value)} {unit}" if unit else value.replace("_", " ")
        return Item(label=name, detail=detail, status=status, icon=icon)

    @action
    async def flip(self, config, params):
        """Toggle a switchable row, or run a scene/script row. Only entities on this tile."""
        entity_id = params.get("entity_id", "")
        if entity_id not in self._chosen(config):
            raise IntegrationError("That entity isn't on this tile.")
        domain = entity_id.split(".")[0]
        service = "toggle" if domain in TOGGLEABLE else RUNNABLE.get(domain, (None,))[0]
        if not service:
            raise IntegrationError("That entity has no on/off or run action.")
        await self._request("POST", f"/services/{domain}/{service}", json={"entity_id": entity_id})
        return await self.fetch(config)


integrations = [HomeAssistant(), HomeAssistantList()]
