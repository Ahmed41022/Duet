"""Discover and choose a private IPv4 address for the LAN listener."""

import ipaddress
import json
import os
import socket
import subprocess
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import psutil

_PRIVATE_NETWORKS = tuple(
    ipaddress.IPv4Network(network)
    for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
_VIRTUAL_PREFIXES = (
    "br-",
    "virbr",
    "veth",
    "vethernet",
    "docker",
    "podman",
    "wsl",
    "cni",
    "flannel",
    "tun",
    "tap",
    "utun",
    "wg",
    "tailscale",
    "zerotier",
    "vmnet",
    "vbox",
)
_VIRTUAL_MARKERS = (
    "wireguard",
    "wintun",
    "vpn",
    "hyper-v",
    "virtualbox",
    "vmware",
    "loopback",
    "teredo",
)
_KIND_PRIORITY = {"hotspot": 0, "wifi": 1, "ethernet": 2, "other": 3, "virtual": 4}
_PROFILE_PRIORITY = {"private": 0, "unknown": 1, "public": 2}
NetworkKind = Literal["hotspot", "wifi", "ethernet", "other", "virtual"]
NetworkProfile = Literal["private", "public", "unknown"]


@dataclass(frozen=True)
class NetworkInterface:
    name: str
    address: str
    kind: NetworkKind = "other"
    profile: NetworkProfile = "unknown"

    @property
    def warning(self) -> str | None:
        if self.profile == "public":
            return "Windows marks this network Public. Use trusted Wi-Fi or a hotspot."
        if self.profile == "unknown":
            return "Network trust is unverified. Use Duet only on a network you trust."
        return None


def is_private_ipv4(value: str) -> bool:
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError:
        return False
    return any(address in network for network in _PRIVATE_NETWORKS)


def _assigned_addresses() -> Iterator[tuple[str, str]]:
    stats = psutil.net_if_stats()
    for name, records in psutil.net_if_addrs().items():
        if name in stats and not stats[name].isup:
            continue
        for record in records:
            if record.family == socket.AF_INET and is_private_ipv4(record.address):
                yield name, record.address


def is_address_assigned(address: str) -> bool:
    return any(current == address for _, current in _assigned_addresses())


def _query(*command: str, timeout: float = 3) -> str:
    try:
        return subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _windows_metadata() -> dict[str, dict]:
    script = """
    [Console]::OutputEncoding = [Text.Encoding]::UTF8
    $profiles = @{}
    Get-NetConnectionProfile -ErrorAction SilentlyContinue | ForEach-Object {
        $profiles[[int]$_.InterfaceIndex] = [string]$_.NetworkCategory
    }
    ConvertTo-Json -Compress -InputObject @(Get-NetAdapter -IncludeHidden |
        ForEach-Object {
            @{
                name = $_.Name
                description = $_.InterfaceDescription
                medium = [string]$_.NdisPhysicalMedium
                physical = $_.HardwareInterface
                profile = $profiles[[int]$_.ifIndex]
            }
        })
    """
    executable = (
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    output = _query(
        str(executable), "-NoProfile", "-NonInteractive", "-Command", script, timeout=8
    )
    try:
        rows = json.loads(output)
    except ValueError:
        return {}
    return {row["name"].casefold(): row for row in rows}


def _linux_wireless_modes() -> dict[str, str]:
    wireless: dict[str, str] = {}
    name = None
    for line in _query("iw", "dev").splitlines():
        key, _, value = line.strip().partition(" ")
        if key == "Interface":
            name = value
        elif key == "type" and name:
            wireless[name] = value
    return wireless


def _classify(
    name: str, metadata: dict, wireless: dict[str, str], linux: bool
) -> NetworkKind:
    label = name.casefold()
    description = (metadata.get("description") or "").casefold()
    if wireless.get(name) == "AP" or "wi-fi direct virtual adapter" in description:
        return "hotspot"
    if (
        label.startswith(_VIRTUAL_PREFIXES)
        or any(marker in f"{label} {description}" for marker in _VIRTUAL_MARKERS)
        or "wi-fi direct" in description
        or metadata.get("physical") is False
    ):
        return "virtual"
    if (
        name in wireless
        or (linux and (Path("/sys/class/net") / name / "wireless").is_dir())
        or label.startswith(("wi-fi", "wifi", "wlan", "wlp", "wlx"))
        or metadata.get("medium") in {"1", "9", "Native 802.11", "Wireless Lan"}
    ):
        return "wifi"
    if label.startswith(("eth", "en")) or metadata.get("physical") is True:
        return "ethernet"
    return "other"


def _profile(metadata: dict) -> NetworkProfile:
    category = metadata.get("profile")
    if category == "Public":
        return "public"
    if category == "Private":
        return "private"
    return "unknown"


def _priority(interface: NetworkInterface) -> tuple[bool, bool, int, int, str, str]:
    return (
        interface.kind == "virtual",
        interface.profile == "public",
        _KIND_PRIORITY[interface.kind],
        _PROFILE_PRIORITY[interface.profile],
        interface.name.casefold(),
        interface.address,
    )


def list_network_interfaces() -> tuple[NetworkInterface, ...]:
    assigned = tuple(_assigned_addresses())
    if not assigned:
        return ()
    linux = sys.platform.startswith("linux")
    windows = _windows_metadata() if sys.platform == "win32" else {}
    wireless = _linux_wireless_modes() if linux else {}
    interfaces = []
    for name, address in assigned:
        metadata = windows.get(name.casefold(), {})
        interfaces.append(
            NetworkInterface(
                name,
                address,
                _classify(name, metadata, wireless, linux),
                _profile(metadata),
            )
        )
    return tuple(sorted(interfaces, key=_priority))


def select_network(
    requested: str | None = None,
    *,
    candidates: Iterable[NetworkInterface] | None = None,
) -> NetworkInterface:
    if requested is not None and not is_private_ipv4(requested):
        raise ValueError("Bind address must be a private RFC 1918 IPv4 address")
    interfaces = list_network_interfaces() if candidates is None else candidates
    matching = [
        interface
        for interface in interfaces
        if is_private_ipv4(interface.address)
        and (requested is None or interface.address == requested)
    ]
    if matching:
        return min(matching, key=_priority)
    if requested is not None:
        raise ValueError("Bind address is not assigned to this computer")
    raise ValueError("No local private IPv4 address found")


def select_bind_address(
    requested: str | None = None, *, assigned: Iterable[str] | None = None
) -> str:
    if assigned is None:
        return select_network(requested).address
    if requested is not None and not is_private_ipv4(requested):
        raise ValueError("Bind address must be a private RFC 1918 IPv4 address")
    candidates = tuple(address for address in assigned if is_private_ipv4(address))
    if requested is not None:
        if requested not in candidates:
            raise ValueError("Bind address is not assigned to this computer")
        return requested
    if not candidates:
        raise ValueError("No local private IPv4 address found")
    return candidates[0]
