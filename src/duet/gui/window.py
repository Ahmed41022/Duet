"""PySide6 desktop interface for the local Duet sharing server."""

# Long QSS declarations and UI copy are kept readable as complete visual rules.
# ruff: noqa: E501

from __future__ import annotations

import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
    QIcon,
    QImage,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

_STYLE = """
QMainWindow, QDialog { background: #f4f6fa; color: #172033; }
QWidget { font-family: 'Segoe UI', 'Inter', sans-serif; font-size: 10pt; }
QFrame#card { background: white; border: 1px solid #e2e7ef; border-radius: 14px; }
QLabel#title { font-size: 20pt; font-weight: 700; color: #16213a; }
QLabel#subtitle { color: #66738a; }
QLabel#section { font-size: 12pt; font-weight: 650; color: #24324c; }
QLabel#muted { color: #6d7890; }
QLabel#status { padding: 7px 12px; border-radius: 12px; font-weight: 650; }
QPushButton { background: #edf1f7; border: 0; border-radius: 8px; padding: 9px 14px; color: #27344d; }
QPushButton:hover { background: #e0e7f1; }
QPushButton#primary { background: #375be8; color: white; font-weight: 650; }
QPushButton#primary:hover { background: #294bd2; }
QPushButton#danger { color: #a53b46; background: #fff0f0; }
QLineEdit, QPlainTextEdit, QSpinBox, QComboBox { background: white; border: 1px solid #dce2eb; border-radius: 7px; padding: 7px; }
QTabWidget::pane { border: 1px solid #e2e7ef; background: white; border-radius: 10px; }
QTabBar::tab { padding: 10px 15px; color: #69758a; }
QTabBar::tab:selected { color: #3156df; border-bottom: 2px solid #3156df; }
QTableWidget { border: 0; gridline-color: #edf0f5; }
QHeaderView::section { border: 0; border-bottom: 1px solid #e2e7ef; padding: 7px; color: #69758a; background: white; }
QProgressBar { border: 0; border-radius: 4px; background: #edf1f7; text-align: center; height: 9px; }
QProgressBar::chunk { border-radius: 4px; background: #4e70ee; }
"""


def _card(parent: QWidget | None = None) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame(parent)
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(11)
    return frame, layout


def _label(text: str, object_name: str = "") -> QLabel:
    label = QLabel(text)
    if object_name:
        label.setObjectName(object_name)
    return label


def _format_bytes(value: Any) -> str:
    if not isinstance(value, (int, float)) or value < 0:
        return "—"
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return "—"


def _pairing_qr_payload(url: str, token: str | None) -> str:
    """Keep the pairing token out of logs, clipboard-able address and HTTP query."""
    if not token:
        return url
    parts = urllib.parse.urlsplit(url)
    fragment = urllib.parse.urlencode({"token": token})
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path, parts.query, fragment)
    )


class SettingsDialog(QDialog):
    """Small settings form; saving delegates validation and persistence to runtime."""

    def __init__(self, snapshot: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Duet settings")
        self.setMinimumWidth(430)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.directory = QLineEdit(str(snapshot.get("received_directory") or ""))
        browse = QPushButton("Choose…")
        browse.clicked.connect(self._choose_directory)
        directory_row = QHBoxLayout()
        directory_row.addWidget(self.directory, 1)
        directory_row.addWidget(browse)
        directory_wrap = QWidget()
        directory_wrap.setLayout(directory_row)
        form.addRow("Received folder", directory_wrap)
        self.timeout = QSpinBox()
        self.timeout.setRange(60, 86400)
        self.timeout.setSingleStep(60)
        self.timeout.setSuffix(" seconds")
        self.timeout.setValue(int(snapshot.get("idle_timeout_seconds") or 900))
        form.addRow("Stop after idle", self.timeout)
        self.bind = QComboBox()
        self.bind.setEditable(True)
        self.bind.addItem("Automatic (recommended)", "")
        for address in snapshot.get("network_addresses") or []:
            value = str(
                address.get("address") if isinstance(address, dict) else address
            )
            name = address.get("name") if isinstance(address, dict) else None
            self.bind.addItem(f"{name} ({value})" if name else value, value)
        current_bind = str(snapshot.get("bind") or "")
        if current_bind:
            index = self.bind.findData(current_bind)
            if index >= 0:
                self.bind.setCurrentIndex(index)
            else:
                self.bind.setCurrentText(current_bind)
        form.addRow("Network address", self.bind)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _choose_directory(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "Choose Received folder", self.directory.text()
        )
        if selected:
            self.directory.setText(selected)


class MainWindow(QMainWindow):
    """Desktop window that consumes a duck-typed backend with a snapshot API."""

    def __init__(self, backend: Any, *, poll_interval_ms: int = 1000) -> None:
        super().__init__()
        self.backend = backend
        self._closed = False
        self._last_snapshot: dict[str, Any] = {}
        self._qr_target: str | None = None
        self._shown_history: list[dict[str, Any]] | None = None
        self.setWindowTitle("Duet")
        self.setMinimumSize(960, 680)
        self.resize(1120, 780)
        self.setAcceptDrops(True)
        self.setStyleSheet(_STYLE)
        self._build_ui()
        self._build_tray()
        self.timer = QTimer(self)
        self.timer.setInterval(poll_interval_ms)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(24, 20, 24, 22)
        root.setSpacing(16)
        header = QHBoxLayout()
        brand = QVBoxLayout()
        brand.addWidget(_label("Duet", "title"))
        brand.addWidget(
            _label("Send files and text between this PC and your phone.", "subtitle")
        )
        header.addLayout(brand)
        header.addStretch(1)
        self.status_label = _label("● Stopped", "status")
        self.status_label.setStyleSheet("background:#edf0f4;color:#69758a")
        header.addWidget(self.status_label)
        self.start_button = QPushButton("Start sharing")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.toggle_server)
        header.addWidget(self.start_button)
        root.addLayout(header)

        columns = QHBoxLayout()
        columns.setSpacing(16)
        left = QVBoxLayout()
        left.setSpacing(14)
        connection, connection_layout = _card()
        connection_layout.addWidget(_label("Connect your phone", "section"))
        connection_body = QHBoxLayout()
        self.qr_label = QLabel("Start sharing to create a pairing QR code")
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setMinimumSize(190, 190)
        self.qr_label.setMaximumSize(210, 210)
        self.qr_label.setStyleSheet(
            "background:#f5f7fb;border-radius:10px;color:#78849a;padding:10px"
        )
        connection_body.addWidget(self.qr_label)
        link_col = QVBoxLayout()
        link_col.addWidget(_label("Open this address on your phone", "muted"))
        self.address_label = _label("Not running", "section")
        self.address_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.pin_label = _label("PIN  —", "section")
        self.pin_label.setStyleSheet("font-size:18pt;letter-spacing:2px;color:#3156df")
        link_col.addWidget(self.address_label)
        link_col.addWidget(self.pin_label)
        link_col.addWidget(
            _label("Only devices with this QR or PIN can connect.", "muted")
        )
        self.public_warning = _label("", "muted")
        self.public_warning.setWordWrap(True)
        self.public_warning.setStyleSheet(
            "color:#a45d13;background:#fff5e4;padding:9px;border-radius:7px"
        )
        link_col.addWidget(self.public_warning)
        guide_button = QPushButton("Network and firewall guide")
        guide_button.clicked.connect(self.show_network_guide)
        link_col.addWidget(guide_button)
        link_col.addStretch(1)
        connection_body.addLayout(link_col, 1)
        connection_layout.addLayout(connection_body)
        left.addWidget(connection)

        send_card, send_layout = _card()
        send_layout.addWidget(_label("Send to phone", "section"))
        self.drop_zone = _label("Drop files or folders here", "muted")
        self.drop_zone.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_zone.setMinimumHeight(72)
        self.drop_zone.setStyleSheet(
            "border:1px dashed #b9c5d8;border-radius:9px;background:#f8faff;padding:10px"
        )
        send_layout.addWidget(self.drop_zone)
        send_actions = QHBoxLayout()
        add_files = QPushButton("Choose files")
        add_files.clicked.connect(self.pick_files)
        add_folder = QPushButton("Choose folder")
        add_folder.clicked.connect(self.pick_folder)
        send_actions.addWidget(add_files)
        send_actions.addWidget(add_folder)
        send_layout.addLayout(send_actions)
        left.addWidget(send_card)

        text_card, text_layout = _card()
        text_layout.addWidget(_label("Share text or a link", "section"))
        self.message_edit = QPlainTextEdit()
        self.message_edit.setPlaceholderText("Write a note or paste a link…")
        self.message_edit.setMaximumHeight(84)
        text_layout.addWidget(self.message_edit)
        text_actions = QHBoxLayout()
        text_actions.addStretch(1)
        self.send_text_button = QPushButton("Send to phone")
        self.send_text_button.setObjectName("primary")
        self.send_text_button.clicked.connect(self.send_text)
        text_actions.addWidget(self.send_text_button)
        text_layout.addLayout(text_actions)
        left.addWidget(text_card)
        left.addStretch(1)
        columns.addLayout(left, 5)

        right = QVBoxLayout()
        self.tabs = QTabWidget()
        self.devices_table = self._make_table(["Device", "Connected", ""])
        self.transfers_table = self._make_table(["Transfer", "Progress", "State"])
        self.outbox_table = self._make_table(["Ready to send", "Size", "Action"])
        history = QWidget()
        history_layout = QVBoxLayout(history)
        self.history_search = QLineEdit()
        self.history_search.setPlaceholderText("Search transfer filenames")
        self.history_search.setAccessibleName("Search transfer history")
        self.history_search.setClearButtonEnabled(True)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)
        self._search_timer.timeout.connect(self._search_history)
        self.history_search.textChanged.connect(self._schedule_history_search)
        history_layout.addWidget(self.history_search)
        self.history_table = self._make_table(["Transfer", "Result", "When"])
        self.history_table.horizontalHeader().setSectionResizeMode(
            2, self.history_table.horizontalHeader().ResizeMode.ResizeToContents
        )
        self.history_table.horizontalHeader().setStretchLastSection(False)
        history_layout.addWidget(self.history_table)
        self.history_summary = _label("", "muted")
        history_layout.addWidget(self.history_summary)
        self.messages_list = QListWidget()
        self.messages_list.setWordWrap(True)
        for title, widget in (
            ("Devices", self.devices_table),
            ("Transfers", self.transfers_table),
            ("Outbox", self.outbox_table),
            ("Text", self.messages_list),
            ("History", history),
        ):
            self.tabs.addTab(widget, title)
        right.addWidget(self.tabs, 1)
        bottom = QHBoxLayout()
        self.received_label = _label("Received folder: —", "muted")
        bottom.addWidget(self.received_label, 1)
        open_button = QPushButton("Open Received")
        open_button.clicked.connect(self.open_received)
        bottom.addWidget(open_button)
        settings_button = QPushButton("Settings")
        settings_button.clicked.connect(self.open_settings)
        bottom.addWidget(settings_button)
        right.addLayout(bottom)
        columns.addLayout(right, 6)
        root.addLayout(columns, 1)
        self.setCentralWidget(central)

    @staticmethod
    def _make_table(headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.setAlternatingRowColors(True)
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setSectionResizeMode(
            0, table.horizontalHeader().ResizeMode.Stretch
        )
        table.setShowGrid(False)
        return table

    def _build_tray(self) -> None:
        self.tray: QSystemTrayIcon | None = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        tray = QSystemTrayIcon(self)
        tray.setIcon(self._tray_icon(False))
        tray.setToolTip("Duet — stopped")
        menu = QMenu(self)
        toggle = QAction("Start sharing", self)
        toggle.triggered.connect(self.toggle_server)
        menu.addAction(toggle)
        menu.addSeparator()
        quit_action = QAction("Quit Duet", self)
        quit_action.triggered.connect(self.close)
        menu.addAction(quit_action)
        tray.setContextMenu(menu)
        tray.activated.connect(
            lambda reason: (
                self.showNormal()
                if reason == QSystemTrayIcon.ActivationReason.Trigger
                else None
            )
        )
        tray.show()
        self.tray = tray
        self.tray_toggle = toggle

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        try:
            return getattr(self.backend, method)(*args, **kwargs)
        except Exception as exc:  # runtime errors are surfaced in the status area
            self._show_error(str(exc))
            return None

    def _show_error(self, message: str) -> None:
        self.status_label.setText(f"● Error: {message}")
        self.status_label.setStyleSheet("background:#fff0f0;color:#a53b46")

    @staticmethod
    def _tray_icon(running: bool) -> QIcon:
        image = QImage(64, 64, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#22a36a" if running else "#8390a4"))
        painter.drawEllipse(7, 7, 50, 50)
        painter.setBrush(QColor("white"))
        painter.drawEllipse(20, 20, 24, 24)
        painter.end()
        return QIcon(QPixmap.fromImage(image))

    def toggle_server(self) -> None:
        if self._last_snapshot.get("running"):
            self._call("stop")
        else:
            self._call("start")
        self.refresh()

    def refresh(self) -> None:
        try:
            snapshot = self.backend.snapshot()
            if not isinstance(snapshot, dict):
                raise TypeError("snapshot() must return a dict")
        except Exception as exc:
            self._show_error(str(exc))
            return
        self._last_snapshot = snapshot
        running = bool(snapshot.get("running"))
        self.send_text_button.setEnabled(running)
        if running:
            self.status_label.setText("● Sharing is on")
            self.status_label.setStyleSheet("background:#e8f7ee;color:#187448")
            self.start_button.setText("Stop sharing")
        else:
            self.status_label.setText("● Stopped")
            self.status_label.setStyleSheet("background:#edf0f4;color:#69758a")
            self.start_button.setText("Start sharing")
        button_style_name = "danger" if running else "primary"
        if self.start_button.objectName() != button_style_name:
            self.start_button.setObjectName(button_style_name)
            style = self.start_button.style()
            style.unpolish(self.start_button)
            style.polish(self.start_button)
            self.start_button.update()
        if self.tray:
            self.tray.setToolTip("Duet — sharing" if running else "Duet — stopped")
            self.tray.setIcon(self._tray_icon(running))
            self.tray_toggle.setText("Stop sharing" if running else "Start sharing")
        url = snapshot.get("url")
        token = snapshot.get("token")
        self.address_label.setText(str(url) if running and url else "Not running")
        pin = snapshot.get("pin")
        self.pin_label.setText(f"PIN  {pin}" if running and pin else "PIN  —")
        self._render_qr(
            str(url), str(token) if token else None
        ) if running and url else self._clear_qr()
        self.received_label.setText(
            f"Received folder: {snapshot.get('received_directory') or '—'}"
        )
        is_public = snapshot.get("network_is_public")
        warning = snapshot.get("network_warning")
        if warning and running:
            self.public_warning.setText(
                f"{snapshot.get('network_name') or 'Network'}: {warning}"
            )
            self.public_warning.show()
        elif is_public is True:
            network = snapshot.get("network_name") or "this network"
            self.public_warning.setText(
                f"⚠ {network} may be public or shared. Duet uses local HTTP; avoid sharing sensitive files here."
            )
            self.public_warning.show()
        else:
            self.public_warning.hide()
        self._fill_devices(snapshot.get("devices") or [])
        self._fill_transfers(snapshot.get("transfers") or [])
        self._fill_outbox(snapshot.get("outbox") or [])
        self._fill_messages(snapshot.get("messages") or [])
        self._refresh_history(snapshot)
        self._notify_received()
        if not running and snapshot.get("stop_reason"):
            self.status_label.setText(snapshot["stop_reason"])
        error = snapshot.get("error")
        if error:
            self._show_error(str(error))

    def _render_qr(self, url: str, token: str | None) -> None:
        target = _pairing_qr_payload(url, token)
        if target == self._qr_target:
            return
        try:
            import qrcode

            qr = qrcode.QRCode(border=4, box_size=6)
            qr.add_data(target)
            qr.make(fit=True)
            matrix = qr.get_matrix()
            image = QImage(len(matrix) * 6, len(matrix) * 6, QImage.Format.Format_RGB32)
            image.fill(QColor("white"))
            painter = QPainter(image)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#172033"))
            for y, line in enumerate(matrix):
                for x, dark in enumerate(line):
                    if dark:
                        painter.drawRect(x * 6, y * 6, 6, 6)
            painter.end()
            self.qr_label.setPixmap(
                QPixmap.fromImage(image).scaled(
                    190,
                    190,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            self._qr_target = target
        except Exception:
            self.qr_label.setPixmap(QPixmap())
            self.qr_label.setText("QR unavailable")
            self._qr_target = None

    def _clear_qr(self) -> None:
        self._qr_target = None
        self.qr_label.setPixmap(QPixmap())
        self.qr_label.setText("Start sharing to create a pairing QR code")

    def _fill_devices(self, rows: list[dict[str, Any]]) -> None:
        self.devices_table.setRowCount(len(rows))
        for row, device in enumerate(rows):
            self.devices_table.setItem(
                row, 0, QTableWidgetItem(str(device.get("name") or "Phone"))
            )
            self.devices_table.setItem(
                row, 1, QTableWidgetItem(str(device.get("connected_at") or "Connected"))
            )
            button = QPushButton("Revoke")
            button.setObjectName("danger")
            device_id = device.get("id")
            button.clicked.connect(lambda _=False, value=device_id: self._revoke(value))
            self.devices_table.setCellWidget(row, 2, button)

    def _revoke(self, device_id: Any) -> None:
        if device_id is not None:
            self._call("revoke", str(device_id))
            self.refresh()

    def _fill_transfers(self, rows: list[dict[str, Any]]) -> None:
        self.transfers_table.setRowCount(len(rows))
        for row, transfer in enumerate(rows):
            direction = (
                "Phone → PC" if transfer.get("direction") == "upload" else "PC → phone"
            )
            name = f"{direction} · {transfer.get('name') or 'Transfer'}"
            self.transfers_table.setItem(row, 0, QTableWidgetItem(name))
            progress = max(0, min(100, int(float(transfer.get("progress") or 0) * 100)))
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(progress)
            done, total = transfer.get("bytes_done"), transfer.get("bytes_total")
            if isinstance(done, int) and isinstance(total, int) and total > 0:
                bar.setFormat(
                    f"{progress}% · {_format_bytes(done)} / {_format_bytes(total)}"
                )
            self.transfers_table.setCellWidget(row, 1, bar)
            self.transfers_table.setItem(
                row, 2, QTableWidgetItem(str(transfer.get("status") or "Active"))
            )

    def _fill_outbox(self, rows: list[dict[str, Any]]) -> None:
        self.outbox_table.setRowCount(len(rows))
        for row, item in enumerate(rows):
            self.outbox_table.setItem(
                row, 0, QTableWidgetItem(str(item.get("name") or "Item"))
            )
            self.outbox_table.setItem(
                row, 1, QTableWidgetItem(_format_bytes(item.get("size")))
            )

            button = QPushButton("Withdraw")
            button.setObjectName("danger")
            offer_id = item.get("id")
            button.setEnabled(offer_id is not None)
            button.clicked.connect(
                lambda _=False, value=offer_id: self._withdraw_offer(value)
            )
            self.outbox_table.setCellWidget(row, 2, button)

    def _withdraw_offer(self, offer_id: str) -> None:
        self._call("remove_offer", offer_id)
        self.refresh()

    def _fill_messages(self, rows: list[dict[str, Any]]) -> None:
        self.messages_list.clear()
        for message in rows:
            direction = (
                "From phone" if message.get("direction") == "incoming" else "Sent"
            )
            timestamp = message.get("created_at") or ""
            item = QListWidgetItem(
                f"{direction}  ·  {timestamp}\n{message.get('text') or ''}"
            )
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.messages_list.addItem(item)

    def _schedule_history_search(self, _text: str) -> None:
        self._search_timer.start()
        self._refresh_history(self._last_snapshot)

    def _search_history(self) -> None:
        self._call("search_history", self.history_search.text().strip())

    def _refresh_history(self, snapshot: dict[str, Any]) -> None:
        query = self.history_search.text().strip()
        if snapshot.get("history_query", "") != query:
            self.history_summary.setText("Searching…")
            rows = []
        else:
            rows = snapshot.get("history") or []
            if rows:
                message = f"Showing {len(rows)} most recent transfers"
                if query:
                    message = f"Showing {len(rows)} most recent matches"
            else:
                message = "No matching transfers" if query else "No transfers yet"
            self.history_summary.setText(message)
        if rows != self._shown_history:
            self._fill_history(rows)
            self._shown_history = rows

    def _notify_received(self) -> None:
        names = self.backend.take_received()
        if (
            not names
            or self.tray is None
            or not QSystemTrayIcon.isSystemTrayAvailable()
            or not QSystemTrayIcon.supportsMessages()
        ):
            return
        message = names[0] if len(names) == 1 else f"{len(names)} files received"
        self.tray.showMessage("Duet · Received", message)

    def _fill_history(self, rows: list[dict[str, Any]]) -> None:
        self.history_table.setRowCount(len(rows))
        for row, item in enumerate(rows):
            direction = (
                "Phone → PC" if item.get("direction") == "upload" else "PC → phone"
            )
            transfer = f"{item.get('name') or 'Transfer'} · {direction}"
            transfer_item = QTableWidgetItem(transfer)
            transfer_item.setToolTip(transfer)
            self.history_table.setItem(row, 0, transfer_item)
            self.history_table.setItem(
                row,
                1,
                QTableWidgetItem(str(item.get("status") or "Completed").capitalize()),
            )
            timestamp = str(item.get("completed_at") or "")
            try:
                local_time = datetime.fromisoformat(timestamp).astimezone()
                timestamp = local_time.strftime("%Y-%m-%d %H:%M")
            except ValueError:
                pass
            self.history_table.setItem(row, 2, QTableWidgetItem(timestamp))

    def pick_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Choose files to send")
        for path in paths:
            self._offer(path)

    def pick_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose a folder to send")
        if path:
            self._offer(path)

    def _offer(self, path: str) -> None:
        self._call("offer", path)
        self.refresh()

    def dragEnterEvent(self, event: Any) -> None:  # noqa: N802 - Qt virtual method
        if event.mimeData().hasUrls() and any(
            url.isLocalFile() for url in event.mimeData().urls()
        ):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: Any) -> None:  # noqa: N802 - Qt virtual method
        for url in event.mimeData().urls():
            if url.isLocalFile():
                self._offer(url.toLocalFile())
        event.acceptProposedAction()

    def send_text(self) -> None:
        content = self.message_edit.toPlainText().strip()
        if not content:
            return
        if not self._last_snapshot.get("running") or len(content) > 16384:
            self._show_error("Start sharing and enter at most 16,384 characters.")
            return
        self._call("send_text", content)
        self.message_edit.clear()
        self.refresh()

    def open_received(self) -> None:
        directory = self._last_snapshot.get("received_directory")
        if not directory:
            self._show_error("Choose a Received folder in Settings.")
            return
        folder = Path(directory)
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except (OSError, ValueError) as exc:
            self._show_error(f"Could not create the Received folder. {exc}")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            self._show_error("Could not open the Received folder.")

    def open_settings(self) -> None:
        dialog = SettingsDialog(self._last_snapshot, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            bind = dialog.bind.currentText().strip()
            if bind == dialog.bind.itemText(dialog.bind.currentIndex()):
                bind = dialog.bind.currentData() or ""
            bind = bind or None
            self._call(
                "configure",
                dialog.directory.text().strip(),
                dialog.timeout.value(),
                bind=bind,
            )
            self.refresh()

    def show_network_guide(self) -> None:
        QMessageBox.information(
            self,
            "Connect over your local network",
            "Keep this PC and your phone on the same Wi-Fi, or turn on the PC's mobile hotspot and connect your phone to it. "
            "If several adapters are active, choose the matching private address in Settings; Automatic is recommended.\n\n"
            "On Windows, allow Duet on private networks when the firewall asks. Avoid public or shared Wi-Fi for sensitive transfers. "
            "Duet uses local HTTP in this release. iPhone and Android use the phone's browser; no phone app is needed.",
        )

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt virtual method
        if not self._closed:
            self._closed = True
            self.timer.stop()
            self._call("close")
        if self.tray:
            self.tray.hide()
        event.accept()
