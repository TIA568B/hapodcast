"""Config + options flow for Daily Podcast Queue (fully UI-managed).

The podcast list (add / edit / remove / reorder / per-podcast catch-up) is
managed from the "Daily Podcasts" sidebar panel. This options flow only covers
the integration-wide settings: player, daily prepare time, and so on.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
    TimeSelector,
)

from .const import (
    CONF_AT,
    CONF_ENABLED,
    CONF_FEED_URL,
    CONF_NAME,
    CONF_PLAYER,
    CONF_CATCHUP,
    CONF_MAX_LOOKBACK_DAYS,
    CONF_PODCASTS,
    CONF_TIMEZONE,
    CONF_WEEKEND_CATCHUP,
    DEFAULT_AT,
    DEFAULT_CATCHUP,
    DEFAULT_ENABLED,
    DEFAULT_MAX_LOOKBACK_DAYS,
    DOMAIN,
    STEP_INIT,
)


def _normalise_time(value: Any) -> str:
    """Coerce a time selector value / string into HH:MM:SS."""
    s = str(value)
    parts = s.split(":")
    while len(parts) < 3:
        parts.append("00")
    try:
        h, m, sec = (int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return DEFAULT_AT
    return f"{h:02d}:{m:02d}:{sec:02d}"


class DailyPodcastsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Initial setup: pick the player and the daily run time."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        # Single instance only -- everything is managed inside one entry.
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            options = {
                CONF_PLAYER: user_input[CONF_PLAYER],
                CONF_AT: _normalise_time(user_input.get(CONF_AT, DEFAULT_AT)),
                CONF_ENABLED: user_input.get(CONF_ENABLED, DEFAULT_ENABLED),
                CONF_PODCASTS: [],
            }
            return self.async_create_entry(
                title="Daily Podcast Queue", data={}, options=options
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_PLAYER): EntitySelector(
                    EntitySelectorConfig(domain="media_player")
                ),
                vol.Optional(CONF_AT, default=DEFAULT_AT): TimeSelector(),
                vol.Optional(
                    CONF_ENABLED, default=DEFAULT_ENABLED
                ): BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_import(
        self, import_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Import an existing YAML config into a single config entry."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        podcasts = [
            {
                CONF_NAME: p.get(CONF_NAME),
                CONF_FEED_URL: p.get(CONF_FEED_URL),
                CONF_CATCHUP: bool(
                    p.get(
                        CONF_CATCHUP,
                        p.get(CONF_WEEKEND_CATCHUP, DEFAULT_CATCHUP),
                    )
                ),
            }
            for p in (import_data.get(CONF_PODCASTS) or [])
            if p.get(CONF_FEED_URL)
        ]
        options = {
            CONF_PLAYER: import_data.get(CONF_PLAYER),
            CONF_AT: DEFAULT_AT,
            CONF_ENABLED: DEFAULT_ENABLED,
            CONF_PODCASTS: podcasts,
        }
        if import_data.get(CONF_TIMEZONE):
            options[CONF_TIMEZONE] = import_data[CONF_TIMEZONE]
        return self.async_create_entry(
            title="Daily Podcast Queue (imported)", data={}, options=options
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> DailyPodcastsOptionsFlow:
        return DailyPodcastsOptionsFlow()


class DailyPodcastsOptionsFlow(OptionsFlowWithReload):
    """Integration-wide settings.

    Podcasts (add / edit / remove / reorder) are managed from the "Daily
    Podcasts" sidebar panel, so this flow is a single settings form.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the settings form directly (no menu — podcasts live in the panel)."""
        opts = dict(self.config_entry.options)
        if user_input is not None:
            opts[CONF_PLAYER] = user_input[CONF_PLAYER]
            opts[CONF_AT] = _normalise_time(user_input.get(CONF_AT, DEFAULT_AT))
            opts[CONF_ENABLED] = user_input.get(CONF_ENABLED, DEFAULT_ENABLED)
            opts[CONF_MAX_LOOKBACK_DAYS] = int(
                user_input.get(
                    CONF_MAX_LOOKBACK_DAYS, DEFAULT_MAX_LOOKBACK_DAYS
                )
            )
            tz = (user_input.get(CONF_TIMEZONE) or "").strip()
            if tz:
                opts[CONF_TIMEZONE] = tz
            else:
                opts.pop(CONF_TIMEZONE, None)
            # OptionsFlowWithReload reloads the entry for us on save.
            return self.async_create_entry(title="", data=opts)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_PLAYER, default=opts.get(CONF_PLAYER)
                ): EntitySelector(EntitySelectorConfig(domain="media_player")),
                vol.Optional(
                    CONF_AT, default=opts.get(CONF_AT, DEFAULT_AT)
                ): TimeSelector(),
                vol.Optional(
                    CONF_ENABLED, default=opts.get(CONF_ENABLED, DEFAULT_ENABLED)
                ): BooleanSelector(),
                vol.Optional(
                    CONF_MAX_LOOKBACK_DAYS,
                    default=opts.get(
                        CONF_MAX_LOOKBACK_DAYS, DEFAULT_MAX_LOOKBACK_DAYS
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1, max=90, step=1, mode=NumberSelectorMode.BOX
                    )
                ),
                vol.Optional(
                    CONF_TIMEZONE, default=opts.get(CONF_TIMEZONE, "")
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
            }
        )
        return self.async_show_form(step_id=STEP_INIT, data_schema=schema)
