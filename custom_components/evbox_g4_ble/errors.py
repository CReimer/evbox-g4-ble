"""Translate expected device failures at Home Assistant action boundaries."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError

from .client import EVBoxAuthError, EVBoxConnectionError
from .const import DOMAIN
from .protocol import EVBoxProtocolError

if TYPE_CHECKING:
    from .coordinator import EVBoxCoordinator


@asynccontextmanager
async def async_device_errors(coordinator: EVBoxCoordinator) -> AsyncIterator[None]:
    """Preserve validation errors and cancellation, redact transport details."""
    try:
        yield
    except EVBoxAuthError as err:
        if coordinator.config_entry is not None:
            coordinator.config_entry.async_start_reauth_if_available(coordinator.hass)
        raise ConfigEntryAuthFailed(
            "invalid_auth", translation_domain=DOMAIN, translation_key="invalid_auth"
        ) from err
    except (EVBoxConnectionError, TimeoutError, OSError) as err:
        raise HomeAssistantError(
            "cannot_connect",
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
        ) from err
    except EVBoxProtocolError as err:
        raise HomeAssistantError(
            "command_failed",
            translation_domain=DOMAIN,
            translation_key="command_failed",
        ) from err


async def async_refresh_or_raise(coordinator: EVBoxCoordinator) -> None:
    """A coordinator records polling errors; an explicit action must report them."""
    await coordinator.async_request_refresh()
    if not coordinator.last_update_success:
        raise HomeAssistantError(
            "cannot_connect",
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
        )
