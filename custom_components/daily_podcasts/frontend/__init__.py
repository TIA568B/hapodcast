"""Serve and auto-register the Daily Podcasts Lovelace card.

Serves the card JS from the integration over HTTP and (in Lovelace storage
mode) registers it as a dashboard resource automatically, so the user doesn't
have to add the resource by hand. Based on the community pattern for embedding
a Lovelace card in an integration.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_call_later

from ..const import JSMODULES, URL_BASE

_LOGGER = logging.getLogger(__name__)


class JSModuleRegistration:
    """Registers the card's JS module(s) with Home Assistant."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.lovelace = self.hass.data.get("lovelace")

    async def async_register(self) -> None:
        await self._async_register_path()
        if self.lovelace and getattr(self.lovelace, "mode", None) == "storage":
            await self._async_wait_for_lovelace_resources()

    async def _async_register_path(self) -> None:
        try:
            await self.hass.http.async_register_static_paths(
                [StaticPathConfig(URL_BASE, str(Path(__file__).parent), False)]
            )
            _LOGGER.debug("Registered static path %s", URL_BASE)
        except RuntimeError:
            # Already registered (e.g. after a reload).
            _LOGGER.debug("Static path %s already registered", URL_BASE)

    async def _async_wait_for_lovelace_resources(self) -> None:
        async def _check_loaded(_now: Any) -> None:
            if self.lovelace.resources.loaded:
                await self._async_register_modules()
            else:
                async_call_later(self.hass, 5, _check_loaded)

        await _check_loaded(0)

    async def _async_register_modules(self) -> None:
        existing = [
            r
            for r in self.lovelace.resources.async_items()
            if r["url"].startswith(URL_BASE)
        ]
        for module in JSMODULES:
            url = f"{URL_BASE}/{module['filename']}"
            registered = False
            for resource in existing:
                if resource["url"].split("?")[0] == url:
                    registered = True
                    if self._version_of(resource["url"]) != module["version"]:
                        await self.lovelace.resources.async_update_item(
                            resource["id"],
                            {
                                "res_type": "module",
                                "url": f"{url}?v={module['version']}",
                            },
                        )
                    break
            if not registered:
                _LOGGER.info("Registering Lovelace resource %s", url)
                await self.lovelace.resources.async_create_item(
                    {"res_type": "module", "url": f"{url}?v={module['version']}"}
                )

    @staticmethod
    def _version_of(url: str) -> str:
        parts = url.split("?")
        if len(parts) > 1 and parts[1].startswith("v="):
            return parts[1][2:]
        return "0"
