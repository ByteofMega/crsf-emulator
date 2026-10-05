"""ws_client.py - сетевая логика WebSocket (GUI <-> ESP32).

Каналы RC, настройки Wi-Fi, MSP-кадры от FC, диагностика обмена и проброс CLI идут по одному WebSocket.
last_msp_time - время (time.monotonic) последнего корректного MSP-кадра от FC: по нему CLI-клиент проверяет,
что FC жива, прежде чем входить в CLI, и ждёт её возврата после exit/save.

ФУНКЦИИ И КЛАССЫ ФАЙЛА
----------------------
class CrsfWsClient
    Клиент WebSocket к ESP32: отправляет каналы, настройки Wi-Fi, MSP-запросы и CLI-строки,
    принимает каналы, телеметрию, MSP-кадры, диагностику и вывод CLI в виде сигналов Qt.
  CrsfWsClient.__init__(self, parent=None)
    Создаёт QWebSocket и подключает его события; обнуляет флаг режима CLI и время последнего
    MSP-кадра.
  CrsfWsClient.is_connected(self)
    True, если соединение с ESP32 установлено.
  CrsfWsClient.connect_to(self, ip: str, port: int)
    Открывает WebSocket по адресу ws://ip:port/.
  CrsfWsClient.disconnect_from_host(self)
    Закрывает WebSocket-соединение.
  CrsfWsClient._send(self, obj: dict)
    Отправляет словарь как JSON-сообщение; возвращает False, если соединения нет.
  CrsfWsClient.send_channel(self, channel_index: int, value: int)
    Отправляет значение одного канала (индекс с нуля преобразуется в номер с единицы).
  CrsfWsClient.send_wifi_config(self, ssid, identity, username, password, enterprise=True)
    Отправляет настройки STA-сети Wi-Fi для ESP32 (SSID, identity, логин, пароль, тип сети).
  CrsfWsClient.request_msp(self, cmd: int)
    Просит ESP32 один раз запросить MSP-команду; допускаются только команды чтения из списка
    READ_CODES.
  CrsfWsClient.cli_enter(self)
    Просит ESP32 войти в CLI полётного контроллера и ставит флаг режима CLI.
  CrsfWsClient.cli_line(self, text: str)
    Отправляет строку в CLI (ESP32 добавит конец строки).
  CrsfWsClient.cli_leave(self)
    Просит ESP32 вернуться в режим MSP и снимает флаг CLI (команду выхода exit отправляет
    вызывающий код).
  CrsfWsClient._on_disconnected(self)
    Сбрасывает флаг CLI и сообщает об обрыве соединения сигналом.
  CrsfWsClient._on_message(self, message: str)
    Разбирает JSON от ESP32 и рассылает его сигналами: MSP-кадр (с отметкой времени),
    диагностика, вывод CLI, значения каналов, телеметрия CRSF.
  CrsfWsClient._on_error(self, _error_code)
    Передаёт текст ошибки сокета сигналом error_occurred.
"""

import json
import time

from PyQt6.QtCore import QObject, QUrl, pyqtSignal
from PyQt6.QtNetwork import QAbstractSocket
from PyQt6.QtWebSockets import QWebSocket

from config import NUM_CHANNELS
from msp_codes import READ_CODES


class CrsfWsClient(QObject):
    connected = pyqtSignal()
    disconnected = pyqtSignal()
    error_occurred = pyqtSignal(str)
    channels_received = pyqtSignal(list)
    telemetry_received = pyqtSignal(dict)
    msp_received = pyqtSignal(int, bytes, bool)  # cmd, payload, err
    msp_diag = pyqtSignal(dict)                  # счётчики обмена ESP32 <-> FC
    cli_output = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cli_mode = False
        self.last_msp_time = 0.0
        self._ws = QWebSocket()
        self._ws.connected.connect(self.connected.emit)
        self._ws.disconnected.connect(self._on_disconnected)
        self._ws.textMessageReceived.connect(self._on_message)
        self._ws.errorOccurred.connect(self._on_error)

    def is_connected(self) -> bool:
        return self._ws.state() == QAbstractSocket.SocketState.ConnectedState

    def connect_to(self, ip: str, port: int):
        self._ws.open(QUrl(f"ws://{ip}:{port}/"))

    def disconnect_from_host(self):
        self._ws.close()

    def _send(self, obj: dict) -> bool:
        if not self.is_connected():
            return False
        self._ws.sendTextMessage(json.dumps(obj))
        return True

    def send_channel(self, channel_index: int, value: int):
        self._send({"channel": channel_index + 1, "value": value})

    def send_wifi_config(self, ssid, identity, username, password, enterprise=True) -> bool:
        return self._send({"wifi": {"ssid": ssid, "identity": identity, "username": username,
                                    "password": password, "enterprise": enterprise}})

    def request_msp(self, cmd: int) -> bool:
        """Запросить одну MSP-команду (только из READ_CODES - команды записи запрещены).
        MSP по CRSF работает и во время CLI; в запасном режиме (UART3) ESP32 сама игнорирует запрос в CLI."""
        if cmd not in READ_CODES:
            return False
        return self._send({"msp_req": int(cmd)})

    def cli_enter(self) -> bool:
        self.cli_mode = True
        return self._send({"cli": "enter"})

    def cli_line(self, text: str) -> bool:
        return self._send({"cli": "line", "text": text})

    def cli_leave(self) -> bool:
        self.cli_mode = False
        return self._send({"cli": "leave"})

    def _on_disconnected(self):
        self.cli_mode = False
        self.disconnected.emit()

    def _on_message(self, message: str):
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            return
        if "msp" in data:
            m = data["msp"]
            try:
                err = bool(m.get("err", 0))
                if not err:
                    self.last_msp_time = time.monotonic()
                self.msp_received.emit(int(m["cmd"]), bytes.fromhex(m.get("data", "")), err)
            except (KeyError, ValueError):
                pass
            return
        if "msp_diag" in data:
            self.msp_diag.emit(data["msp_diag"])
            return
        if "cli_out" in data:
            self.cli_output.emit(str(data["cli_out"]))
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
