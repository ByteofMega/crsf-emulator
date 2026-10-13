"""
main.py - точка входа. Вкладки: Каналы, Карта, Настройки FC, MSP, Запись полёта.

Положение дрона НЕ берётся из GPS: оператор задаёт стартовую точку и начальный курс на карте, дальше положение
считает DeadReckoning (position_estimator.py) по стикам RC и данным Betaflight из MSP. Это оценка с накапливающейся
ошибкой.

ВАЖНО: автономная навигация ("Лететь по маршруту") перекрывает ручной ввод ROLL/PITCH/YAW, пока не нажата "Стоп" -
сначала тесты без пропеллеров / в симуляторе.

Устройство (после рефакторинга MainWindow только собирает части и раздаёт им данные):
  rc_state.py     - RcState: текущие 16 каналов;
  navigation.py   - NavigationController: оценка положения и автономный полёт (таймеры 50 и 200 мс);
  map_tab.py      - вкладка «Карта»;  wifi_panel.py - блок Wi-Fi;  settings_panel.py, msp_panel.py,
  flight_panel.py - остальные вкладки;  common.py - общие функции;  config.py - константы и темы.

ФУНКЦИИ И КЛАССЫ ФАЙЛА
----------------------
class MainWindow
    Главное окно: подключение к ESP32, тема оформления, вкладки, раздача MSP-кадров и телеметрии.
  MainWindow.__init__(self)
    Создаёт клиент WebSocket, общее состояние каналов, контроллер навигации и все вкладки; восстанавливает
    IP, порт и тему из QSettings.
  MainWindow._apply_theme(self) / _toggle_theme(self)
    Применяет тёмную или светлую тему и запоминает выбор.
  MainWindow._save_connection_settings(self, ip, port) / _toggle_connection(self)
    Сохранение IP и порта; кнопка «Подключиться/Отключиться».
  MainWindow._on_connected / _on_disconnected / _on_error
    Реакции на события WebSocket (при разрыве навигация останавливается).
  MainWindow._on_cli_busy(self, busy)
    Пока идёт обмен с FC по CLI, запуск маршрута недоступен.
  MainWindow._on_channels_received(self, values)
    ESP32 прислала значения каналов: обновляет состояние и ползунки без повторной отправки.
  MainWindow._on_telemetry_received(self, telemetry) / _on_msp(self, cmd, data, err)
    Раздача телеметрии CRSF и MSP-кадров (курс и углы, статус арма, ток) нужным частям.
  MainWindow.closeEvent(self, event)
    Останавливает запись/воспроизведение, прерывает обмен с FC по CLI и сохраняет IP и порт.
main()
    Создаёт приложение Qt, показывает главное окно и запускает цикл событий.
"""

import sys

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from channel_widgets import ChannelPanel
from config import DEFAULT_IP, DEFAULT_PORT, DEFAULT_THEME, build_stylesheet
from flight_panel import FlightPanel
from map_tab import MapTab
from msp_codes import MSP_ANALOG, MSP_ATTITUDE, MSP_STATUS, MSP_STATUS_EX, decode
from msp_panel import MspPanel
from navigation import NavigationController
from rc_state import RcState
from settings_panel import FcSettingsPanel
from telemetry_panel import TelemetryPanel
from wifi_panel import WifiPanel
from ws_client import CrsfWsClient

SETTINGS_ORG = "CRSFEmulator"
SETTINGS_APP = "GUI"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ФОТОН - ELRS CRSF Emulator / ESP32 WebSocket GUI")
        self.resize(900, 980)

        self._settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
        saved_ip = self._settings.value("connection/ip", DEFAULT_IP, type=str)
        saved_port = self._settings.value("connection/port", DEFAULT_PORT, type=int)
        self._theme = self._settings.value("ui/theme", DEFAULT_THEME, type=str)
        if self._theme not in ("dark", "light"):
            self._theme = DEFAULT_THEME

        self.ws_client = CrsfWsClient(self)
        self.ws_client.connected.connect(self._on_connected)
        self.ws_client.disconnected.connect(self._on_disconnected)
        self.ws_client.error_occurred.connect(self._on_error)
        self.ws_client.channels_received.connect(self._on_channels_received)
        self.ws_client.telemetry_received.connect(self._on_telemetry_received)
        self.ws_client.msp_received.connect(self._on_msp)

        self.rc = RcState(self.ws_client)
        self.settings_panel = FcSettingsPanel(self.ws_client, nav_active=lambda: self.nav.is_running)
        self.settings_panel.busy_changed.connect(self._on_cli_busy)
        self.nav = NavigationController(self.ws_client, self.rc, self.settings_panel.get_limits, self)

        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)

        conn_layout = QHBoxLayout()
        conn_layout.addWidget(QLabel("IP адрес ESP32:"))
        self.ip_input = QLineEdit(saved_ip)
        conn_layout.addWidget(self.ip_input)
        conn_layout.addWidget(QLabel("Порт:"))
        self.port_input = QLineEdit(str(saved_port))
        self.port_input.setFixedWidth(60)
        conn_layout.addWidget(self.port_input)
        self.connect_btn = QPushButton("Подключиться")
        self.connect_btn.setObjectName("primary")
        self.connect_btn.clicked.connect(self._toggle_connection)
        conn_layout.addWidget(self.connect_btn)
        self.theme_btn = QPushButton()
        self.theme_btn.setObjectName("themeBtn")
        self.theme_btn.setToolTip("Переключить тёмную / светлую тему")
        self.theme_btn.clicked.connect(self._toggle_theme)
        conn_layout.addWidget(self.theme_btn)
        root_layout.addLayout(conn_layout)

        self.status_label = QLabel("Не подключено")
        self.status_label.setObjectName("statusLabel")
        root_layout.addWidget(self.status_label)

        root_layout.addWidget(WifiPanel(self.ws_client))

        tabs = QTabWidget()
        root_layout.addWidget(tabs)

        channels_tab = QWidget()
        channels_layout = QVBoxLayout(channels_tab)
        self.telemetry_panel = TelemetryPanel()
        self.ws_client.telemetry_received.connect(self.telemetry_panel.update_telemetry)
        channels_layout.addWidget(self.telemetry_panel)
        self.channel_panel = ChannelPanel()
        self.channel_panel.value_changed.connect(self.rc.set)
        channels_layout.addWidget(self.channel_panel)
        tabs.addTab(channels_tab, "Каналы")

        self.flight_panel = FlightPanel(
            self.ws_client, lambda: self.rc.values, self.rc.set, lambda: self.nav.is_running,
            self.channel_panel.set_all_silent)
        self.map_tab = MapTab(self.nav, self.settings_panel.get_limits, self.flight_panel.is_active, self.ws_client)
        tabs.addTab(self.map_tab, "Карта")
        tabs.addTab(self.settings_panel, "Настройки FC")
        self.msp_panel = MspPanel(self.ws_client)
        tabs.addTab(self.msp_panel, "MSP")
        tabs.addTab(self.flight_panel, "Запись полёта")

        self._apply_theme()

    # ---------- тема оформления ----------
    def _apply_theme(self):
        self.setStyleSheet(build_stylesheet(self._theme))
        self.theme_btn.setText("☀  Светлая тема" if self._theme == "dark" else "☾  Тёмная тема")
        self.map_tab.set_theme(self._theme == "dark")

    def _toggle_theme(self):
        self._theme = "light" if self._theme == "dark" else "dark"
        self._settings.setValue("ui/theme", self._theme)
        self._apply_theme()

    # ---------- подключение ----------
    def _save_connection_settings(self, ip: str, port: int):
        self._settings.setValue("connection/ip", ip)
        self._settings.setValue("connection/port", port)

    def _toggle_connection(self):
        if self.ws_client.is_connected():
            self.ws_client.disconnect_from_host()
            return
        ip = self.ip_input.text().strip()
        port_text = self.port_input.text().strip()
        if not ip or not port_text.isdigit():
            self.status_label.setText("Проверьте IP и порт")
            return
        port = int(port_text)
        self._save_connection_settings(ip, port)
        self.status_label.setText(f"Подключение к {ip}:{port} ...")
        self.ws_client.connect_to(ip, port)

    def _on_connected(self):
        self.status_label.setText("Подключено")
        self.connect_btn.setText("Отключиться")

    def _on_disconnected(self):
        self.status_label.setText("Отключено")
        self.connect_btn.setText("Подключиться")
        self.nav.stop()

    def _on_error(self, message: str):
        self.status_label.setText(f"Ошибка: {message}")

    # ---------- обмен с FC по CLI: блокируется только запуск маршрута ----------
    def _on_cli_busy(self, busy: bool):
        self.map_tab.set_cli_busy(busy)

    # ---------- каналы ----------
    def _on_channels_received(self, values: list):
        self.rc.replace(values)
        self.channel_panel.set_all_silent(values)

    # ---------- телеметрия CRSF и MSP ----------
    def _on_telemetry_received(self, telemetry: dict):
        battery = telemetry.get("battery")
        if battery:
            self.map_tab.on_battery(battery)

    def _on_msp(self, cmd: int, data: bytes, err: bool):
        if err:
            return
        d = decode(cmd, data)
        if not d:
            return
        if cmd == MSP_ATTITUDE:
            self.nav.set_msp_yaw(d["yaw_deg"])
            self.flight_panel.on_attitude(d["roll_deg"], d["pitch_deg"], d["yaw_deg"])
        elif cmd in (MSP_STATUS, MSP_STATUS_EX):
            self.nav.set_armed(d["armed"])
        elif cmd == MSP_ANALOG:
            self.map_tab.on_analog(d)

    def closeEvent(self, event):
        self.flight_panel.stop_all()
        self.settings_panel.abort()     # прервать возможный обмен с FC, чтобы процесс не завис при выходе
        ip = self.ip_input.text().strip()
        port_text = self.port_input.text().strip()
        if ip and port_text.isdigit():
            self._save_connection_settings(ip, int(port_text))
        event.accept()


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
