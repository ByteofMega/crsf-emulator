"""map_view.py - карта Яндекса в GUI (QWebEngineView + QWebChannel).

map.html раздаётся через локальный HTTP-сервер (нужно для Referer с ключом API Яндекс.Карт).
Новое: режим выбора СТАРТОВОЙ точки (оператор кликает по карте), сигнал start_set.
"""

import json
import os
import socket
import threading
from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

from PyQt6.QtCore import QObject, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineWidgets import QWebEngineView

GUI_DIR = os.path.dirname(os.path.abspath(__file__))


def _find_free_port(preferred: int = 8642) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]


def _start_local_server(directory: str, port: int) -> ThreadingHTTPServer:
    handler_cls = partial(SimpleHTTPRequestHandler, directory=directory)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_cls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class MapBridge(QObject):
    """Мост JS -> Python. route_updated несёт полный список точек маршрута,
    start_set - координаты стартовой точки, выбранной оператором."""

    route_updated = pyqtSignal(list)
    start_set = pyqtSignal(float, float)

    @pyqtSlot(str)
    def onRouteUpdated(self, waypoints_json: str):
        try:
            raw = json.loads(waypoints_json)
        except json.JSONDecodeError:
            return
        self.route_updated.emit([(float(lat), float(lon)) for lat, lon in raw])

    @pyqtSlot(float, float)
    def onStartSet(self, lat: float, lon: float):
        self.start_set.emit(lat, lon)


class MapView(QWebEngineView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.bridge = MapBridge()
        self._channel = QWebChannel()
        self._channel.registerObject("bridge", self.bridge)
        self.page().setWebChannel(self._channel)

        port = _find_free_port()
        self._server = _start_local_server(GUI_DIR, port)
        self.load(QUrl(f"http://127.0.0.1:{port}/map.html"))

    def update_drone_position(self, lat: float, lon: float):
        """Новая (расчётная) позиция дрона: маркер и трек."""
        self.page().runJavaScript(f"updateDronePosition({lat}, {lon});")

    def enable_start_mode(self):
        """Следующий клик по карте задаст стартовую точку, а не точку маршрута."""
        self.page().runJavaScript("enableStartMode();")

    def clear_route(self):
        self.page().runJavaScript("if (typeof resetRoute === 'function') { resetRoute(); }")

    def clear_track(self):
        self.page().runJavaScript("if (typeof clearDroneTrack === 'function') { clearDroneTrack(); }")

    def clear_start(self):
        self.page().runJavaScript("if (typeof clearStart === 'function') { clearStart(); }")
