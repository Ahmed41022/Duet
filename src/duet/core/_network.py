"""Select a concrete, assigned RFC 1918 IPv4 address for the LAN listener."""

import ipaddress
import socket
from collections.abc import Iterable

import psutil

_PRIVATE_NETWORKS = tuple(
    ipaddress.IPv4Network(network)
    for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


def is_private_ipv4(value: str) -> bool:
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError:
        return False
    return any(address in network for network in _PRIVATE_NETWORKS)


def _assigned_addresses() -> Iterable[str]:
    for records in psutil.net_if_addrs().values():
        for record in records:
            if record.family == socket.AF_INET:
                yield record.address


def select_bind_address(
    requested: str | None = None, *, assigned: Iterable[str] | None = None
) -> str:
    if requested is not None and not is_private_ipv4(requested):
        raise ValueError("Bind address must be a private RFC 1918 IPv4 address")
    candidates = tuple(
        address
        for address in (_assigned_addresses() if assigned is None else assigned)
        if is_private_ipv4(address)
    )
    if requested is not None:
        if requested not in candidates:
            raise ValueError("Bind address is not assigned to this computer")
        return requested
    if not candidates:
        raise ValueError("No local private IPv4 address found")
    return candidates[0]
