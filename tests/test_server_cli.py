from duet.cli import serve
from duet.core.pairing import PairingManager


def test_terminal_offer_uses_fragment_and_does_not_print_raw_token(monkeypatch, capsys):
    calls = []

    class FakeQR:
        def __init__(self, *, border):
            assert border == 2

        def add_data(self, data):
            calls.append(data)

        def print_ascii(self, *, tty, invert):
            assert not tty
            assert invert

    monkeypatch.setattr(serve.qrcode, "QRCode", FakeQR)
    offer = PairingManager().issue_offer()
    serve._print_offer("http://192.168.0.1:8765", offer)
    assert calls == [f"http://192.168.0.1:8765/#token={offer.token}"]
    stdout = capsys.readouterr().out
    assert offer.pin in stdout
    assert offer.token not in stdout


def test_cli_rejects_non_private_binding_without_starting_server(monkeypatch, capsys):
    def unexpected_start(*args, **kwargs):
        raise AssertionError("Unexpected bind attempt")

    monkeypatch.setattr(serve, "_listen", unexpected_start)
    assert serve.run_serve(bind="0.0.0.0") == 1
    assert "private RFC 1918 IPv4" in capsys.readouterr().err
