"""Config + options flow for Daily Podcast Queue (fully UI-managed)."""

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
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
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
    CONF_PODCASTS,
    CONF_RECORD_ONLY,
    CONF_TIMEZONE,
    DEFAULT_AT,
    DEFAULT_ENABLED,
    DEFAULT_RECORD_ONLY,
    DOMAIN,
    STEP_ADD,
    STEP_MOVE_DOWN,
    STEP_MOVE_UP,
    STEP_REMOVE,
    STEP_SETTINGS,
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
                CONF_RECORD_ONLY: user_input.get(
                    CONF_RECORD_ONLY, DEFAULT_RECORD_ONLY
                ),
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
                vol.Optional(
                    CONF_RECORD_ONLY, default=DEFAULT_RECORD_ONLY
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
            {CONF_NAME: p.get(CONF_NAME), CONF_FEED_URL: p.get(CONF_FEED_URL)}
            for p in (import_data.get(CONF_PODCASTS) or [])
            if p.get(CONF_FEED_URL)
        ]
        options = {
            CONF_PLAYER: import_data.get(CONF_PLAYER),
            CONF_AT: DEFAULT_AT,
            CONF_ENABLED: DEFAULT_ENABLED,
            CONF_RECORD_ONLY: import_data.get(
                CONF_RECORD_ONLY, DEFAULT_RECORD_ONLY
            ),
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
    """Manage everything from the UI: settings + the podcast list."""

    # --- Menu ------------------------------------------------------------
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        podcasts = self.config_entry.options.get(CONF_PODCASTS, [])
        count = len(podcasts)
        menu = [STEP_SETTINGS, STEP_ADD]
        if count:
            menu.append(STEP_REMOVE)
        if count > 1:
            menu += [STEP_MOVE_UP, STEP_MOVE_DOWN]
        return self.async_show_menu(step_id="init", menu_options=menu)

    # --- Settings --------------------------------------------------------
    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        opts = dict(self.config_entry.options)
        if user_input is not None:
            opts[CONF_PLAYER] = user_input[CONF_PLAYER]
            opts[CONF_AT] = _normalise_time(user_input.get(CONF_AT, DEFAULT_AT))
            opts[CONF_ENABLED] = user_input.get(CONF_ENABLED, DEFAULT_ENABLED)
            opts[CONF_RECORD_ONLY] = user_input.get(
                CONF_RECORD_ONLY, DEFAULT_RECORD_ONLY
            )
            tz = (user_input.get(CONF_TIMEZONE) or "").strip()
            if tz:
                opts[CONF_TIMEZONE] = tz
            else:
                opts.pop(CONF_TIMEZONE, None)
            return self._save(opts)

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
                    CONF_RECORD_ONLY,
                    default=opts.get(CONF_RECORD_ONLY, DEFAULT_RECORD_ONLY),
                ): BooleanSelector(),
                vol.Optional(
                    CONF_TIMEZONE, default=opts.get(CONF_TIMEZONE, "")
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
            }
        )
        return self.async_show_form(step_id=STEP_SETTINGS, data_schema=schema)

    # --- Add a podcast ---------------------------------------------------
    async def async_step_add_podcast(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            name = user_input[CONF_NAME].strip()
            url = user_input[CONF_FEED_URL].strip()
            try:
                cv.url(url)
            except vol.Invalid:
                errors[CONF_FEED_URL] = "invalid_url"
            if not name:
                errors[CONF_NAME] = "name_required"
            if not errors:
                opts = dict(self.config_entry.options)
                podcasts = list(opts.get(CONF_PODCASTS, []))
                podcasts.append({CONF_NAME: name, CONF_FEED_URL: url})
                opts[CONF_PODCASTS] = podcasts
                return self._save(opts)

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT)
                ),
                vol.Required(CONF_FEED_URL): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.URL)
                ),
            }
        )
        return self.async_show_form(
            step_id=STEP_ADD, data_schema=schema, errors=errors
        )

    # --- Remove a podcast ------------------------------------------------
    async def async_step_remove_podcast(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        podcasts = list(self.config_entry.options.get(CONF_PODCASTS, []))
        if user_input is not None:
            idx = int(user_input["podcast"])
            if 0 <= idx < len(podcasts):
                podcasts.pop(idx)
            opts = dict(self.config_entry.options)
            opts[CONF_PODCASTS] = podcasts
            return self._save(opts)
        return self.async_show_form(
            step_id=STEP_REMOVE,
            data_schema=vol.Schema(
                {vol.Required("podcast"): self._podcast_selector(podcasts)}
            ),
        )

    # --- Reorder: move up ------------------------------------------------
    async def async_step_move_up(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._move(user_input, STEP_MOVE_UP, direction=-1)

    # --- Reorder: move down ----------------------------------------------
    async def async_step_move_down(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._move(user_input, STEP_MOVE_DOWN, direction=1)

    async def _move(
        self, user_input, step_id: str, direction: int
    ) -> ConfigFlowResult:
        podcasts = list(self.config_entry.options.get(CONF_PODCASTS, []))
        if user_input is not None:
            idx = int(user_input["podcast"])
            new_idx = idx + direction
            if 0 <= idx < len(podcasts) and 0 <= new_idx < len(podcasts):
                podcasts[idx], podcasts[new_idx] = podcasts[new_idx], podcasts[idx]
            opts = dict(self.config_entry.options)
            opts[CONF_PODCASTS] = podcasts
            return self._save(opts)
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(
                {vol.Required("podcast"): self._podcast_selector(podcasts)}
            ),
        )

    # --- Helpers ---------------------------------------------------------
    def _podcast_selector(self, podcasts: list[dict]) -> SelectSelector:
        options = [
            {"value": str(i), "label": f"{i + 1}. {p.get(CONF_NAME, '?')}"}
            for i, p in enumerate(podcasts)
        ]
        return SelectSelector(
            SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST)
        )

    def _save(self, options: dict[str, Any]) -> ConfigFlowResult:
        """Persist options. OptionsFlowWithReload reloads the entry for us."""
        return self.async_create_entry(title="", data=options)
