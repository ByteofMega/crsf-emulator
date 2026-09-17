"""
ws_client.py — вся сетевая логика (WebSocket) отделена от GUI.

ESP32 присылает два вида сообщений:
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
    """Обертка над QWebSocket со специализированными сигналами для CRSF-протокола.

    Инкапсулирует детали протокола (формат JSON-пакетов, состояние сокета)
    и предоставляет вызывающему коду (GUI) только высокоуровневые сигналы
    и методы, не завязанные на конкретную реализацию транспорта.
    """

    connected = pyqtSignal()
    """Сигнал: WebSocket-соединение с ESP32 успешно установлено."""

    disconnected = pyqtSignal()
    """Сигнал: соединение с ESP32 разорвано (по инициативе любой из сторон)."""

    error_occurred = pyqtSignal(str)
    """Сигнал: произошла сетевая ошибка.

    Аргументы:
        str: человекочитаемое описание ошибки (из QWebSocket.errorString()).
    """

    channels_received = pyqtSignal(list)
    """Сигнал: от ESP32 пришло полное состояние всех каналов.

    Аргументы:
        list[int]: список из NUM_CHANNELS значений в микросекундах (1000..2000),
            в порядке ROLL, PITCH, THROTTLE, YAW, AUX1..AUX12.
    """

    telemetry_received = pyqtSignal(dict)
    """Сигнал: от ESP32 пришёл пакет телеметрии с полётного контроллера.

    Аргументы:
        dict: словарь с необязательными ключами "battery", "attitude",
            "flight_mode", "link" — см. TelemetryPanel.update_telemetry().
    """

    def __init__(self, parent=None):
        """Создать клиент и подписаться на внутренние сигналы QWebSocket.

        Аргументы:
            parent (QObject | None): родительский Qt-объект для управления
                временем жизни (стандартный параметр PyQt), по умолчанию None.
        """
        super().__init__(parent)
        self._ws = QWebSocket()
        self._ws.connected.connect(self.connected.emit)
        self._ws.disconnected.connect(self.disconnected.emit)
        self._ws.textMessageReceived.connect(self._on_message)
        self._ws.errorOccurred.connect(self._on_error)

    # ---------- публичное API ----------

    def is_connected(self) -> bool:
        """Проверить, установлено ли сейчас соединение с ESP32.

        Возвращает:
            bool: True, если сокет в состоянии ConnectedState, иначе False.
        """
        return self._ws.state() == QAbstractSocket.SocketState.ConnectedState

    def connect_to(self, ip: str, port: int):
        """Начать асинхронное подключение к WebSocket-серверу ESP32.

        Аргументы:
            ip (str): IP-адрес ESP32 в локальной сети (например "192.168.1.42").
            port (int): TCP-порт WebSocket-сервера ESP32 (по умолчанию 81).

        Возвращает:
            None. Результат подключения приходит асинхронно через сигналы
            connected / error_occurred.
        """
        url = QUrl(f"ws://{ip}:{port}/")
        self._ws.open(url)

    def disconnect_from_host(self):
        """Закрыть текущее WebSocket-соединение с ESP32.

        Аргументы: нет.
        Возвращает: None. Разрыв соединения подтверждается сигналом disconnected.
        """
        self._ws.close()

    def send_channel(self, channel_index: int, value: int):
        """Отправить на ESP32 новое значение одного канала.

        Аргументы:
            channel_index (int): 0-based индекс канала (0..15), то есть
                0 = ROLL, 1 = PITCH, ..., 15 = AUX12.
            value (int): новое значение канала в микросекундах (1000..2000).

        Возвращает:
            None. Если соединение не установлено, вызов молча игнорируется.
        """
        if not self.is_connected():
            return
        payload = json.dumps({"channel": channel_index + 1, "value": value})
        self._ws.sendTextMessage(payload)

    # ---------- внутренние обработчики ----------

    def _on_message(self, message: str):
        """Разобрать входящее текстовое сообщение от ESP32 и вызвать нужный сигнал.

        Аргументы:
            message (str): сырое текстовое сообщение, полученное по WebSocket
                (ожидается JSON вида {"channels": [...]} или {"telemetry": {...}}).

        Возвращает:
            None. При ошибке разбора JSON сообщение молча игнорируется.
        """
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
        """Обработать сигнал ошибки от внутреннего QWebSocket.

        Аргументы:
            _error_code: код ошибки Qt (QAbstractSocket.SocketError), не
                используется напрямую — вместо него берётся текстовое
                описание через self._ws.errorString().

        Возвращает:
            None. Транслирует ошибку наружу через сигнал error_occurred.
        """
        self.error_occurred.emit(self._ws.errorString())
