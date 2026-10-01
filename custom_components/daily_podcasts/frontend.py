"""Register the Daily Podcasts sidebar panel."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from .const import DOMAIN, INTEGRATION_VERSION

_LOGGER = logging.getLogger(__name__)

PANEL_FILENAME = "daily-podcasts-panel.js"
PANEL_URL = f"/{DOMAIN}/{PANEL_FILENAME}"
PANEL_PATH = Path(__file__).parent / "www" / PANEL_FILENAME
PANEL_URL_PATH = "daily-podcasts"
PANEL_TITLE = "Daily Podcasts"
PANEL_ICON = "mdi:podcast"
PANEL_COMPONENT = "daily-podcasts-panel"
PANEL_REGISTERED = "panel_registered"


async def async_register_panel(hass: HomeAssistant) -> None:
    """Serve and register the Daily Podcasts sidebar panel idempotently."""
    data = hass.data.setdefault(DOMAIN, {})
    if data.get(PANEL_REGISTERED):
        return
    if not PANEL_PATH.is_file():
        _LOGGER.error("Sidebar panel asset not found: %s", PANEL_PATH)
        return

    try:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(PANEL_URL, str(PANEL_PATH), cache_headers=False)]
        )
    except RuntimeError:
        # The path can already be registered after an integration reload.
        pass
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Could not serve Daily Podcasts sidebar panel: %s", err)
        return

    try:
        # panel_custom is built into Home Assistant but may not yet have been
        # initialized when a custom integration starts.
        if not await async_setup_component(hass, "panel_custom", {}):
            _LOGGER.warning("panel_custom could not be initialized")
            return
        from homeassistant.components import panel_custom

        await panel_custom.async_register_panel(
            hass,
            frontend_url_path=PANEL_URL_PATH,
            webcomponent_name=PANEL_COMPONENT,
            sidebar_title=PANEL_TITLE,
            sidebar_icon=PANEL_ICON,
            module_url=f"{PANEL_URL}?v={INTEGRATION_VERSION}",
            embed_iframe=False,
            require_admin=False,
        )
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Could not register Daily Podcasts sidebar panel: %s", err)
        return

    data[PANEL_REGISTERED] = True
    _LOGGER.info("Registered Daily Podcasts sidebar panel at /%s", PANEL_URL_PATH)


def async_remove_panel(hass: HomeAssistant) -> None:
    """Remove the sidebar panel when the integration is unloaded."""
    data = hass.data.get(DOMAIN, {})
    if not data.pop(PANEL_REGISTERED, False):
        return
    try:
        from homeassistant.components import frontend

        frontend.async_remove_panel(hass, PANEL_URL_PATH)
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("Could not remove Daily Podcasts sidebar panel: %s", err)
