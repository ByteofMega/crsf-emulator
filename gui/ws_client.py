"""
ws_client.py — вся сетевая логика (WebSocket) отделена от GUI.

Обновлено: ESP32 теперь присылает два разных вида сообщений:
  {"channels": [16 значений]}      -> сигнал channels_received
  {"telemetry": {...}}             -> сигнал telemetry_received

Оба разбираются в одном месте (_on_message), GUI просто подписывается
на нужный сигнал и не заботится о формате JSON.
"""

import json

from PyQt6.QtCore import QObject, QUrl, pyqtSignal
from PyQt6.QtNetwork import QAbstractSocket
from PyQt6.QtWebSockets import QWebSocket

from config import NUM_CHANNELS


class CrsfWsClient(QObject):
    """Обертка над QWebSocket со специализированными сигналами для CRSF-протокола."""

    connected = pyqtSignal()
    disconnected = pyqtSignal()
    error_occurred = pyqtSignal(str)
    channels_received = pyqtSignal(list)   # список из NUM_CHANNELS значений (от FC-эмулятора)
    telemetry_received = pyqtSignal(dict)  # словарь с полями battery/attitude/flight_mode/link

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ws = QWebSocket()
        self._ws.connected.connect(self.connected.emit)
        self._ws.disconnected.connect(self.disconnected.emit)
        self._ws.textMessageReceived.connect(self._on_message)
        self._ws.errorOccurred.connect(self._on_error)

    # ---------- публичное API ----------

    def is_connected(self) -> bool:
        return self._ws.state() == QAbstractSocket.SocketState.ConnectedState

    def connect_to(self, ip: str, port: int):
        url = QUrl(f"ws://{ip}:{port}/")
        self._ws.open(url)

    def disconnect_from_host(self):
        self._ws.close()

    def send_channel(self, channel_index: int, value: int):
        """channel_index — 0-based индекс канала (0..15)."""
        if not self.is_connected():
            return
        payload = json.dumps({"channel": channel_index + 1, "value": value})
        self._ws.sendTextMessage(payload)

    # ---------- внутренние обработчики ----------

    def _on_message(self, message: str):
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            return

        channels = data.get("channels")
        if channels and len(channels) == NUM_CHANNELS:
            self.channels_received.emit([int(v) for v in channels])
            return

        telemetry = data.get("telemetry")
        if telemetry is not None:
            self.telemetry_received.emit(telemetry)

    def _on_error(self, _error_code):
        self.error_occurred.emit(self._ws.errorString())
