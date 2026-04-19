"""Config flow for Xiaomi Ab Wheel."""

import logging
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_MAC

from .const import DOMAIN, CONF_TOKEN

_LOGGER = logging.getLogger(__name__)


class AbWheelConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Xiaomi Ab Wheel."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            mac = user_input[CONF_MAC].upper().strip()
            token = user_input[CONF_TOKEN].strip()

            # Validate hex token (24 hex chars = 12 bytes)
            try:
                token_bytes = bytes.fromhex(token)
                if len(token_bytes) != 12:
                    errors["base"] = "invalid_token"
            except ValueError:
                errors["base"] = "invalid_token"

            if not errors:
                await self.async_set_unique_id(mac)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Ab Wheel ({mac[-8:]})",
                    data={CONF_MAC: mac, CONF_TOKEN: token},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_MAC): str,
                vol.Required(CONF_TOKEN): str,
            }),
            errors=errors,
        )
