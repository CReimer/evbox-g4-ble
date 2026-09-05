"""State tracking for EVBox firmware proxy transfers."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass
class FirmwareUpdateState:
    """Observable state of one charger's latest firmware update."""

    in_progress: bool = True
    phase: str = "preparing"
    transferred_bytes: int = 0
    total_bytes: int = 0
    error: str | None = None
    proxy: object | None = None

    @property
    def percentage(self) -> int | None:
        """Return whole-number download progress when the size is known."""
        if not self.total_bytes:
            return None
        return min(100, self.transferred_bytes * 100 // self.total_bytes)


type FirmwareProxyRegistry = dict[str, FirmwareUpdateState]


def reserve_proxy(
    registry: FirmwareProxyRegistry, charger_ip: str
) -> FirmwareUpdateState | None:
    """Atomically reserve one firmware update slot for a charger."""
    current = registry.get(charger_ip)
    if current is not None and current.in_progress:
        return None
    state = FirmwareUpdateState()
    registry[charger_ip] = state
    return state


def activate_proxy(
    registry: FirmwareProxyRegistry,
    charger_ip: str,
    state: FirmwareUpdateState,
    proxy: object,
) -> None:
    """Attach a running proxy to its reservation."""
    if registry.get(charger_ip) is not state:
        raise RuntimeError("firmware proxy slot was not reserved")
    state.proxy = proxy
    state.phase = "waiting_for_charger"


def update_transfer(
    state: FirmwareUpdateState,
    phase: str,
    transferred_bytes: int,
    total_bytes: int,
    error: str | None = None,
) -> None:
    """Update transfer progress or its terminal error state."""
    state.phase = phase
    state.transferred_bytes = transferred_bytes
    state.total_bytes = total_bytes
    state.error = error
    state.in_progress = phase not in ("error", "installed")


def release_proxy(
    registry: FirmwareProxyRegistry,
    charger_ip: str,
    state: FirmwareUpdateState,
    proxy: object | None = None,
    error: str | None = None,
) -> None:
    """Release the matching reservation while retaining its final status."""
    if registry.get(charger_ip) is not state:
        return
    if proxy is not None and state.proxy is not proxy:
        return
    state.proxy = None
    if state.in_progress:
        state.in_progress = False
        if error:
            state.phase = "error"
            state.error = error


def proxy_is_active(
    registry: FirmwareProxyRegistry, charger_ip: str
) -> bool:
    """Return whether a charger has a reserved or running update."""
    state = registry.get(charger_ip)
    return bool(state and state.in_progress)


def get_update_state(
    registry: FirmwareProxyRegistry, charger_ip: str
) -> FirmwareUpdateState | None:
    """Return the latest update status for a charger."""
    return registry.get(charger_ip)


def running_proxies(registry: FirmwareProxyRegistry) -> Iterable[object]:
    """Return all proxy instances which still need cleanup."""
    return tuple(
        state.proxy for state in registry.values() if state.proxy is not None
    )
