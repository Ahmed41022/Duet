import socket
from types import SimpleNamespace

import pytest

from duet.core import _network
from duet.core._network import NetworkInterface, select_network


@pytest.mark.parametrize("kind", ["hotspot", "wifi", "ethernet"])
def test_lan_outranks_virtual(kind):
    vpn = NetworkInterface("VPN", "10.0.0.7", "virtual", "private")
    lan = NetworkInterface("LAN", "192.168.1.2", kind)
    assert select_network(candidates=[vpn, lan]) == lan
    assert select_network(vpn.address, candidates=[vpn, lan]) == vpn


def test_public_wifi_yields_to_trusted_lan():
    public = NetworkInterface("Wi-Fi", "192.168.1.7", "wifi", "public")
    private = NetworkInterface("Ethernet", "192.168.2.7", "ethernet", "private")
    assert select_network(candidates=[public, private]) == private
    assert select_network(public.address, candidates=[public, private]) == public
    assert "Public" in public.warning
    assert private.warning is None
    assert NetworkInterface("Wi-Fi", "192.168.1.7").warning


def test_public_lan_is_preferred_over_virtual_adapters():
    public = NetworkInterface("Wi-Fi", "192.168.1.7", "wifi", "public")
    virtual = NetworkInterface("VPN", "172.20.0.1", "virtual", "private")
    assert select_network(candidates=[virtual, public]) == public


@pytest.mark.parametrize("address", ["0.0.0.0", "127.0.0.1", "::", "8.8.8.8"])
def test_non_lan_addresses_cannot_be_requested(address):
    with pytest.raises(ValueError, match="RFC 1918"):
        select_network(address, candidates=[NetworkInterface("Wi-Fi", address)])


def test_windows_hotspot_profile_and_adapter_order(monkeypatch):
    monkeypatch.setattr(_network.sys, "platform", "win32")
    monkeypatch.setattr(
        _network,
        "_assigned_addresses",
        lambda: iter(
            [
                ("vEthernet (WSL)", "172.27.64.1"),
                ("Wi-Fi", "192.168.1.7"),
                ("Local Area Connection* 12", "192.168.138.1"),
            ]
        ),
    )
    monkeypatch.setattr(
        _network,
        "_query",
        lambda *args, **kwargs: (
            """
            [
                {"name":"Wi-Fi","description":"Intel Wireless","profile":"Private"},
                {"name":"Local Area Connection* 12",
                 "description":"Microsoft Wi-Fi Direct Virtual Adapter",
                 "physical":false}
            ]
        """
        ),
    )
    assert [
        (item.kind, item.profile) for item in _network.list_network_interfaces()
    ] == [
        ("hotspot", "unknown"),
        ("wifi", "private"),
        ("virtual", "unknown"),
    ]


def test_linux_hotspot_and_wifi_outrank_docker(monkeypatch):
    monkeypatch.setattr(_network.sys, "platform", "linux")
    monkeypatch.setattr(
        _network,
        "_assigned_addresses",
        lambda: iter(
            [
                ("docker0", "172.17.0.1"),
                ("wlp2s0", "192.168.0.7"),
                ("ap0", "10.42.0.1"),
            ]
        ),
    )
    monkeypatch.setattr(
        _network,
        "_query",
        lambda *args: (
            "\tInterface wlp2s0\n\t\ttype managed\n\tInterface ap0\n\t\ttype AP"
        ),
    )
    assert [
        (item.kind, item.profile) for item in _network.list_network_interfaces()
    ] == [
        ("hotspot", "unknown"),
        ("wifi", "unknown"),
        ("virtual", "unknown"),
    ]


def test_address_removal_and_link_down_are_detected(monkeypatch):
    addresses = {
        "Wi-Fi": [SimpleNamespace(family=socket.AF_INET, address="192.168.1.7")]
    }
    link = SimpleNamespace(isup=True)
    monkeypatch.setattr(_network.psutil, "net_if_addrs", lambda: addresses)
    monkeypatch.setattr(_network.psutil, "net_if_stats", lambda: {"Wi-Fi": link})
    monkeypatch.setattr(_network, "_query", lambda *args, **kwargs: "")
    assert _network.is_address_assigned("192.168.1.7")
    link.isup = False
    assert not _network.is_address_assigned("192.168.1.7")
    link.isup = True
    addresses["Wi-Fi"][0].address = "192.168.1.8"
    assert not _network.is_address_assigned("192.168.1.7")
    with pytest.raises(ValueError, match="not assigned"):
        select_network("192.168.1.7")
