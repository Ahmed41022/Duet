import pytest

from duet.core._network import select_bind_address
from duet.core.pairing import PairingError, PairingManager


def test_pairing_consumes_offer_and_exposes_only_public_identity():
    pairing = PairingManager()
    offer = pairing.issue_offer()
    secret = pairing.pair(address="192.168.1.3", token=offer.token)
    (session,) = pairing.devices()
    assert session.id != secret
    assert session.address == "192.168.1.3"
    assert pairing.authenticate(secret) is session
    assert pairing.authenticate(session.id) is None
    for credentials in ({"token": offer.token}, {"pin": offer.pin}):
        with pytest.raises(PairingError):
            pairing.pair(address="192.168.1.4", **credentials)
    another_offer = pairing.issue_offer()
    another_secret = pairing.pair(address="192.168.1.4", pin=another_offer.pin)
    assert pairing.revoke(session.id)
    assert not pairing.revoke(session.id)
    assert pairing.authenticate(secret) is None
    assert pairing.authenticate(another_secret)


def test_pairing_and_sessions_expire_at_the_deadline():
    now = 0.0
    pairing = PairingManager(offer_ttl=10, session_ttl=20, clock=lambda: now)
    offer = pairing.issue_offer()
    now = 10.0
    with pytest.raises(PairingError):
        pairing.pair(address="192.168.1.3", token=offer.token)
    offer = pairing.issue_offer()
    secret = pairing.pair(address="192.168.1.3", token=offer.token)
    now = 29.9
    assert pairing.authenticate(secret)
    now = 30.0
    assert pairing.authenticate(secret) is None
    assert pairing.devices() == ()


def test_lockout_counts_wrong_pins_across_peers_and_offer_rotations():
    now = 100.0
    pairing = PairingManager(clock=lambda: now)
    offer = pairing.issue_offer()
    for index in range(5):
        with pytest.raises(PairingError) as rejected:
            pairing.pair(address=f"192.168.1.{index}", pin="invalid")
        assert rejected.value.retry_after == (60 if index == 4 else None)

    offer = pairing.issue_offer()
    now = 159.5
    with pytest.raises(PairingError) as locked:
        pairing.pair(address="192.168.1.9", pin=offer.pin)
    assert locked.value.retry_after == 1
    now = 160
    assert pairing.pair(address="192.168.1.9", pin=offer.pin)


def test_qr_works_even_while_pin_is_locked():
    pairing = PairingManager()
    offer = pairing.issue_offer()
    for _ in range(5):
        with pytest.raises(PairingError):
            pairing.pair(address="192.168.1.3", pin="invalid")
    assert pairing.pair(address="192.168.1.3", token=offer.token)


@pytest.mark.parametrize(
    "address",
    [
        "8.8.8.8",
        "127.0.0.1",
        "0.0.0.0",
        "169.254.0.1",
        "172.32.0.1",
        "::1",
        "localhost",
    ],
)
def test_bind_rejects_addresses_outside_rfc1918(address):
    with pytest.raises(ValueError, match="RFC 1918"):
        select_bind_address(address, assigned=[address])


def test_bind_requires_an_assigned_private_address():
    assigned = ["127.0.0.1", "8.8.8.8", "172.31.0.2", "192.168.0.3"]
    assert select_bind_address(assigned=assigned) == "172.31.0.2"
    assert select_bind_address("192.168.0.3", assigned=assigned) == "192.168.0.3"
    with pytest.raises(ValueError, match="not assigned"):
        select_bind_address("10.0.0.5", assigned=assigned)
    with pytest.raises(ValueError, match="No local"):
        select_bind_address(assigned=["127.0.0.1"])
