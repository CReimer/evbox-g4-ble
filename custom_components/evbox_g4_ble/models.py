"""Typed configuration entry shared by the integration platforms."""

from homeassistant.config_entries import ConfigEntry

from .coordinator import EVBoxCoordinator


type EVBoxConfigEntry = ConfigEntry[EVBoxCoordinator]
