"""Home Assistant entities as tiles. Assistant control of HA goes through Hermes's own
`hermes-homeassistant` toolset, so this integration exposes no tools."""

import re
from typing import Any

import httpx

from .base import Control, Field, Integration, IntegrationError, Option, TileData, action, http

TOGGLEABLE = {"light", "switch", "fan", "input_boolean", "automation", "siren", "humidifier"}
RUNNABLE = {"scene": ("turn_on", "Activate"), "script": ("turn_on", "Run"), "button": ("press", "Press"),
            "input_button": ("press", "Press")}
ACTIVE_STATES = {"on", "open", "opening", "home", "playing", "heat", "cool", "heat_cool", "auto"}
SERVICE_NAME = re.compile(r"^[a-z_]+$")


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
    refresh = 15
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
            service, label = RUNNABLE[domain]
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
        if not service or not SERVICE_NAME.match(service):
            raise IntegrationError("That control is missing a valid service.")

        data = dict(params.get("data") or {})
        if params.get("field") and "value" in params:
            data[params["field"]] = params["value"]
        changed = await self._request("POST", f"/services/{domain}/{service}",
                                      json={**data, "entity_id": entity_id})
        for state in changed or []:
            if state.get("entity_id") == entity_id:
                return self._render(state)
        return await self.fetch(config)

    async def options(self, field, query):
        if field != "entity_id":
            return []
        query = query.lower()
        found = []
        for state in await self._request("GET", "/states"):
            name = state.get("attributes", {}).get("friendly_name") or state["entity_id"]
            if query in state["entity_id"].lower() or query in name.lower():
                found.append(Option(value=state["entity_id"], label=name, detail=state["entity_id"]))
        return sorted(found, key=lambda o: o.label.lower())[:50]


integration = HomeAssistant()
