"""Track active firmware proxies without yielding between reservations."""

from __future__ import annotations

from collections.abc import Iterable

_RESERVATION = object()

type FirmwareProxyRegistry = dict[str, object]


def reserve_proxy(registry: FirmwareProxyRegistry, charger_ip: str) -> bool:
    """Atomically reserve one firmware update slot for a charger."""
    if charger_ip in registry:
        return False
    registry[charger_ip] = _RESERVATION
    return True


def activate_proxy(
    registry: FirmwareProxyRegistry, charger_ip: str, proxy: object
) -> None:
    """Replace a reservation with its running proxy."""
    if registry.get(charger_ip) is not _RESERVATION:
        raise RuntimeError("firmware proxy slot was not reserved")
    registry[charger_ip] = proxy


def release_proxy(
    registry: FirmwareProxyRegistry,
    charger_ip: str,
    proxy: object | None = None,
) -> None:
    """Release a reservation or the matching running proxy."""
    expected = _RESERVATION if proxy is None else proxy
    if registry.get(charger_ip) is expected:
        registry.pop(charger_ip)


def proxy_is_active(
    registry: FirmwareProxyRegistry, charger_ip: str
) -> bool:
    """Return whether a charger has a reserved or running proxy."""
    return charger_ip in registry


def running_proxies(registry: FirmwareProxyRegistry) -> Iterable[object]:
    """Return running proxies, excluding incomplete reservations."""
    return tuple(
        proxy for proxy in registry.values() if proxy is not _RESERVATION
    )
