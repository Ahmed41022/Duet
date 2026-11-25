import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication  # noqa: E402

from duet.gui.window import MainWindow, SettingsDialog, _pairing_qr_payload  # noqa: E402


class FakeBackend:
    def __init__(self):
        self.calls = []
        self.data = {
            "running": False,
            "url": None,
            "pin": None,
            "token": None,
            "devices": [],
            "transfers": [],
            "history": [],
            "outbox": [],
            "messages": [],
            "received_directory": "C:/Duet/Received",
            "idle_timeout_seconds": 900,
            "bind": None,
            "error": None,
            "network_name": None,
            "network_is_public": False,
        }

    def snapshot(self):
        return self.data

    def start(self):
        self.calls.append(("start",))
        self.data.update(
            running=True,
            url="http://192.168.1.4:8765/",
            pin="123456",
            token="secret-token",
        )

    def stop(self):
        self.calls.append(("stop",))
        self.data.update(running=False, url=None, pin=None, token=None)

    def close(self):
        self.calls.append(("close",))

    def revoke(self, device_id):
        self.calls.append(("revoke", device_id))

    def offer(self, path):
        self.calls.append(("offer", path))

    def send_text(self, text):
        self.calls.append(("send_text", text))

    def configure(self, received_directory, idle_timeout_seconds, bind=None):
        self.calls.append(("configure", received_directory, idle_timeout_seconds, bind))
        self.data.update(
            received_directory=received_directory,
            idle_timeout_seconds=idle_timeout_seconds,
            bind=bind,
        )


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture
def window(app):
    backend = FakeBackend()
    widget = MainWindow(backend, poll_interval_ms=60_000)
    yield widget, backend
    widget.close()


def test_start_stop_qr_and_plain_address(window):
    widget, backend = window
    widget.toggle_server()
    assert backend.calls[0] == ("start",)
    assert widget.address_label.text() == "http://192.168.1.4:8765/"
    assert widget.pin_label.text() == "PIN  123456"
    assert not widget.qr_label.pixmap().isNull()
    assert "secret-token" not in widget.address_label.text()
    widget.toggle_server()
    assert backend.calls[1] == ("stop",)
    assert widget.address_label.text() == "Not running"


def test_qr_encodes_token_only_in_fragment():
    target = _pairing_qr_payload("http://192.168.1.4:8765/?lang=en", "sensitive value")
    assert target == "http://192.168.1.4:8765/?lang=en#token=sensitive+value"


def test_snapshot_records_render_and_device_revocation(window):
    widget, backend = window
    backend.data.update(
        running=True,
        url="http://192.168.1.4:8765/",
        pin="654321",
        token="token",
        devices=[{"id": "dev-1", "name": "Pixel", "connected_at": "Just now"}],
        transfers=[
            {
                "id": "t1",
                "name": "photo.jpg",
                "direction": "upload",
                "progress": 0.5,
                "bytes_done": 512,
                "bytes_total": 1024,
                "status": "Receiving",
            }
        ],
        history=[
            {
                "id": "h1",
                "name": "notes.txt",
                "direction": "download",
                "status": "Complete",
                "completed_at": "10:40",
                "size": 12,
            }
        ],
        outbox=[{"id": "o1", "name": "archive.zip", "size": 2048}],
        messages=[
            {
                "id": "m1",
                "direction": "incoming",
                "text": "hello",
                "created_at": "10:42",
            }
        ],
    )
    widget.refresh()
    assert widget.devices_table.rowCount() == 1
    assert widget.transfers_table.rowCount() == 1
    assert widget.outbox_table.rowCount() == 1
    assert widget.history_table.rowCount() == 1
    assert widget.messages_list.count() == 1
    assert "hello" in widget.messages_list.item(0).text()
    widget._revoke("dev-1")
    assert ("revoke", "dev-1") in backend.calls


def test_send_text_and_offers_delegate_to_backend(window):
    widget, backend = window
    widget.toggle_server()
    widget.message_edit.setPlainText("hello https://example.test")
    widget.send_text()
    widget._offer("C:/generated/photo.bin")
    assert ("send_text", "hello https://example.test") in backend.calls
    assert ("offer", "C:/generated/photo.bin") in backend.calls
    assert widget.message_edit.toPlainText() == ""


def test_settings_apply_to_runtime(window, monkeypatch):
    widget, backend = window
    backend.data["network_addresses"] = [
        {"address": "192.168.1.4", "name": "Home Wi-Fi"},
        "192.168.137.1",
    ]

    def accept(dialog):
        assert isinstance(dialog, SettingsDialog)
        dialog.directory.setText("C:/Duet/Incoming")
        dialog.timeout.setValue(1200)
        dialog.bind.setCurrentIndex(dialog.bind.findData("192.168.137.1"))
        return dialog.DialogCode.Accepted

    monkeypatch.setattr(SettingsDialog, "exec", accept)
    widget.open_settings()
    assert ("configure", "C:/Duet/Incoming", 1200, "192.168.137.1") in backend.calls
