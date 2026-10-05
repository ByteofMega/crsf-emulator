"""
main.py - точка входа. Вкладки: Каналы, Карта, Настройки FC, MSP.

Положение дрона больше НЕ берётся из GPS: оператор задаёт стартовую точку и начальный курс
на карте, дальше положение считает DeadReckoning (position_estimator.py) по стикам RC и данным
Betaflight из MSP (курс, статус арма, ток). Это оценка с накапливающейся ошибкой.

ВАЖНО: автономная навигация ("Лететь по маршруту") перекрывает ручной ввод ROLL/PITCH/YAW,
пока не нажата "Стоп" - сначала тесты без пропеллеров / в симуляторе. Газ ведёт оператор.
"""

import sys
import time

from PyQt6.QtCore import QSettings, QTimer
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QGroupBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QMessageBox, QPushButton, QTabWidget,
    QVBoxLayout, QWidget,
)

import cli_ws
from channel_widgets import ChannelPanel
from config import CHANNEL_NAMES, DEFAULT_IP, DEFAULT_PORT, DEFAULT_VALUES, STYLESHEET
from map_view import MapView
from msp_codes import decode
from msp_panel import MspPanel
from position_estimator import DeadReckoning
from route_planner import WaypointNavigator, estimate_energy_usage
from settings_panel import FcSettingsPanel
from telemetry_panel import TelemetryPanel
from ws_client import CrsfWsClient

ROLL_IDX = CHANNEL_NAMES.index("ROLL")
PITCH_IDX = CHANNEL_NAMES.index("PITCH")
THROTTLE_IDX = CHANNEL_NAMES.index("THROTTLE")
YAW_IDX = CHANNEL_NAMES.index("YAW")

NAV_TICK_MS = 200
EST_TICK_MS = 50
MAP_EVERY_N_TICKS = 4

SETTINGS_ORG = "CRSFEmulator"
SETTINGS_APP = "GUI"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ФОТОН - ELRS CRSF Emulator / ESP32 WebSocket GUI")
        self.resize(900, 980)
        self.setStyleSheet(STYLESHEET)

        self._settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
        saved_ip = self._settings.value("connection/ip", DEFAULT_IP, type=str)
        saved_port = self._settings.value("connection/port", DEFAULT_PORT, type=int)

        self.ws_client = CrsfWsClient(self)
        self.ws_client.connected.connect(self._on_connected)
        self.ws_client.disconnected.connect(self._on_disconnected)
        self.ws_client.error_occurred.connect(self._on_error)
        self.ws_client.channels_received.connect(self._on_channels_received)
        self.ws_client.telemetry_received.connect(self._on_telemetry_received)
        self.ws_client.msp_received.connect(self._on_msp)

        self._rc = list(DEFAULT_VALUES)
        self._est = None
        self._est_last = time.monotonic()
        self._est_ticks = 0
        self._msp_yaw = None
        self._armed = None
        self._analog = None
        self._last_battery = None
        self._pending_route: list = []
        self._navigator = None
        self._cli_busy = False

        self._nav_timer = QTimer(self)
        self._nav_timer.setInterval(NAV_TICK_MS)
        self._nav_timer.timeout.connect(self._navigation_tick)
        self._est_timer = QTimer(self)
        self._est_timer.setInterval(EST_TICK_MS)
        self._est_timer.timeout.connect(self._estimator_tick)
        self._est_timer.start()

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
        self.connect_btn.clicked.connect(self._toggle_connection)
        conn_layout.addWidget(self.connect_btn)
        root_layout.addLayout(conn_layout)

        self.status_label = QLabel("Не подключено")
        self.status_label.setObjectName("statusLabel")
        root_layout.addWidget(self.status_label)

        # --- Wi-Fi сеть ESP32 (STA/интернет) ---
        wifi_box = QGroupBox("Настройка Wi-Fi сети ESP32 (интернет)")
        wifi_form = QFormLayout(wifi_box)
        self.wifi_enterprise_check = QCheckBox("WPA2-Enterprise (логин/пароль)")
        self.wifi_enterprise_check.setChecked(True)
        wifi_form.addRow(self.wifi_enterprise_check)
        self.wifi_ssid_input = QLineEdit()
        self.wifi_ssid_input.setPlaceholderText("Например: KFU.NET или домашний Wi-Fi")
        wifi_form.addRow("SSID сети:", self.wifi_ssid_input)
        self.wifi_identity_input = QLineEdit()
        self.wifi_identity_input.setPlaceholderText("Можно оставить пустым (= логин)")
        wifi_form.addRow("EAP identity (только Enterprise):", self.wifi_identity_input)
        self.wifi_username_input = QLineEdit()
        wifi_form.addRow("Логин (только Enterprise):", self.wifi_username_input)
        self.wifi_password_input = QLineEdit()
        self.wifi_password_input.setEchoMode(QLineEdit.EchoMode.Password)
        wifi_form.addRow("Пароль:", self.wifi_password_input)
        self.wifi_send_btn = QPushButton("Отправить сеть на ESP32")
        self.wifi_send_btn.clicked.connect(self._send_wifi_config)
        wifi_form.addRow(self.wifi_send_btn)
        self.wifi_status_label = QLabel("")
        self.wifi_status_label.setWordWrap(True)
        wifi_form.addRow(self.wifi_status_label)
        root_layout.addWidget(wifi_box)

        tabs = QTabWidget()
        root_layout.addWidget(tabs)

        # --- Каналы ---
        channels_tab = QWidget()
        channels_layout = QVBoxLayout(channels_tab)
        self.telemetry_panel = TelemetryPanel()
        self.ws_client.telemetry_received.connect(self.telemetry_panel.update_telemetry)
        channels_layout.addWidget(self.telemetry_panel)
        self.channel_panel = ChannelPanel()
        self.channel_panel.value_changed.connect(self._on_channel_changed)
        channels_layout.addWidget(self.channel_panel)
        tabs.addTab(channels_tab, "Каналы")

        # --- Карта ---
        map_tab = QWidget()
        map_layout = QVBoxLayout(map_tab)
        self.map_view = MapView()
        self.map_view.bridge.route_updated.connect(self._on_route_updated)
        self.map_view.bridge.start_set.connect(self._on_start_set)
        map_layout.addWidget(self.map_view)

        start_layout = QHBoxLayout()
        self.set_start_btn = QPushButton("Задать старт на карте")
        self.set_start_btn.clicked.connect(self._enable_start_mode)
        start_layout.addWidget(self.set_start_btn)
        start_layout.addWidget(QLabel("Курс дрона на старте, ° от севера:"))
        self.start_heading_input = QLineEdit("0")
        self.start_heading_input.setFixedWidth(60)
        start_layout.addWidget(self.start_heading_input)
        self.reset_pos_btn = QPushButton("Сбросить позицию")
        self.reset_pos_btn.clicked.connect(self._reset_position)
        start_layout.addWidget(self.reset_pos_btn)
        self.pos_label = QLabel("Старт не задан: нажмите кнопку и кликните по карте")
        start_layout.addWidget(self.pos_label)
        map_layout.addLayout(start_layout)

        nav_controls = QHBoxLayout()
        self.route_status_label = QLabel("Кликайте по карте: каждый клик - новая точка маршрута")
        nav_controls.addWidget(self.route_status_label)
        self.start_nav_btn = QPushButton("Лететь по маршруту")
        self.start_nav_btn.setEnabled(False)
        self.start_nav_btn.clicked.connect(self._start_navigation)
        nav_controls.addWidget(self.start_nav_btn)
        self.stop_nav_btn = QPushButton("Стоп")
        self.stop_nav_btn.setEnabled(False)
        self.stop_nav_btn.clicked.connect(self._stop_navigation)
        nav_controls.addWidget(self.stop_nav_btn)
        self.clear_route_btn = QPushButton("Очистить маршрут")
        self.clear_route_btn.clicked.connect(self._clear_route)
        nav_controls.addWidget(self.clear_route_btn)
        map_layout.addLayout(nav_controls)

        energy_box = QGroupBox("Оценка маршрута")
        energy_form = QFormLayout(energy_box)
        self.route_distance_label = QLabel("-")
        self.route_time_label = QLabel("-")
        self.route_turns_label = QLabel("-")
        self.route_energy_label = QLabel("-")
        self.speed_label = QLabel("-")
        energy_form.addRow("Оценка скорости дрона:", self.speed_label)
        energy_form.addRow("Длина маршрута:", self.route_distance_label)
        energy_form.addRow("Примерное время полёта:", self.route_time_label)
        energy_form.addRow("Из них на развороты:", self.route_turns_label)
        energy_form.addRow("Примерный расход батареи:", self.route_energy_label)
        map_layout.addWidget(energy_box)
        tabs.addTab(map_tab, "Карта")

        # --- Настройки FC (CLI через ESP32) и MSP ---
        self.settings_panel = FcSettingsPanel(self.ws_client, nav_active=lambda: self._navigator is not None)
        self.settings_panel.busy_changed.connect(self._on_cli_busy)
        tabs.addTab(self.settings_panel, "Настройки FC")
        self.msp_panel = MspPanel(self.ws_client)
        tabs.addTab(self.msp_panel, "MSP")

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
        self._stop_navigation()

    def _on_error(self, message: str):
        self.status_label.setText(f"Ошибка: {message}")

    # ---------- обмен с FC по CLI: блокируется только запуск маршрута ----------
    def _on_cli_busy(self, busy: bool):
        self._cli_busy = busy
        self.start_nav_btn.setEnabled((not busy) and bool(self._pending_route) and self._navigator is None)

    # ---------- каналы ----------
    def _on_channel_changed(self, idx: int, value: int):
        self._rc[idx] = value
        self.ws_client.send_channel(idx, value)

    def _on_channels_received(self, values: list):
        self._rc = list(values)
        self.channel_panel.set_all_silent(values)

    # ---------- телеметрия CRSF и MSP ----------
    def _on_telemetry_received(self, telemetry: dict):
        battery = telemetry.get("battery")
        if battery:
            self._last_battery = battery

    def _on_msp(self, cmd: int, data: bytes, err: bool):
        if err:
            return
        d = decode(cmd, data)
        if not d:
            return
        if cmd == 108:
            self._msp_yaw = d["yaw_deg"]
            if self._est is not None:
                self._est.set_msp_yaw(self._msp_yaw)
        elif cmd in (101, 150):
            self._armed = d["armed"]
        elif cmd == 110:
            self._analog = d
            self._update_route_estimate()

    # ---------- старт и положение ----------
    def _enable_start_mode(self):
        self.pos_label.setText("Кликните по карте: там стоит дрон")
        self.map_view.enable_start_mode()

    def _on_start_set(self, lat: float, lon: float):
        try:
            heading = float(self.start_heading_input.text().replace(",", ".")) % 360.0
        except ValueError:
            QMessageBox.warning(self, "Курс", "Курс на старте должен быть числом (0 = север, 90 = восток).")
            heading = 0.0
        lim = self.settings_panel.get_limits(silent=True)
        self._est = DeadReckoning(
            lat, lon, heading, kd=lim["drag_kd"], angle_limit_deg=lim["max_tilt_deg"],
            deadband_us=lim["rc_deadband_us"], yaw_rate_dps=lim["yaw_rate_dps"], yaw_raw=self._msp_yaw)
        self.map_view.clear_track()
        self.map_view.update_drone_position(lat, lon)
        self.pos_label.setText(f"Старт: {lat:.6f}, {lon:.6f}, курс {heading:.0f}°")
        self._update_route_estimate()

    def _reset_position(self):
        self._stop_navigation()
        self._est = None
        self.map_view.clear_start()  # стирает старт, метку дрона и трек
        self.pos_label.setText("Старт не задан: нажмите кнопку и кликните по карте")
        self.speed_label.setText("-")
        self._update_route_estimate()

    def _estimator_tick(self):
        now = time.monotonic()
        dt = now - self._est_last
        self._est_last = now
        if self._est is None:
            return
        lim = self.settings_panel.get_limits(silent=True)
        self._est.set_params(lim["drag_kd"], lim["max_tilt_deg"], lim["rc_deadband_us"], lim["yaw_rate_dps"])
        armed = self._armed if self._armed is not None else True
        airborne = armed and self._rc[THROTTLE_IDX] > lim["liftoff_throttle_us"]
        lat, lon, speed = self._est.update(self._rc[PITCH_IDX], self._rc[ROLL_IDX], self._rc[YAW_IDX], dt, airborne)
        self._est_ticks += 1
        if self._est_ticks % MAP_EVERY_N_TICKS == 0:
            self.map_view.update_drone_position(lat, lon)
            state = "в воздухе" if airborne else "на земле"
            self.speed_label.setText(f"{speed:.1f} м/с, курс {self._est.psi:.0f}° ({state})")

    # ---------- маршрут ----------
    def _on_route_updated(self, waypoints: list):
        self._pending_route = waypoints
        self.start_nav_btn.setEnabled(len(waypoints) > 0 and not self._cli_busy)
        if waypoints:
            self.route_status_label.setText(f"Точек в маршруте: {len(waypoints)} (финиш - точка №{len(waypoints)})")
        else:
            self.route_status_label.setText("Кликайте по карте: каждый клик - новая точка маршрута")
        self._update_route_estimate()

    def _update_route_estimate(self):
        if not self._pending_route:
            for lbl in (self.route_distance_label, self.route_time_label,
                        self.route_turns_label, self.route_energy_label):
                lbl.setText("-")
            return

        path = list(self._pending_route)
        if self._est is not None:
            path = [(self._est.lat, self._est.lon)] + path

        limits = self.settings_panel.get_limits(silent=True)
        max_speed = limits.get("max_speed_mps", 5.0)
        yaw_rate_dps = limits.get("yaw_rate_dps", 180.0)
        capacity_mah = limits.get("battery_capacity_mah", 0.0)

        if self._analog is not None:
            current_a = self._analog.get("current_a", 0.0)
        elif self._last_battery is not None:
            current_a = self._last_battery.get("current_a", 0.0)
        else:
            current_a = 0.0

        result = estimate_energy_usage(path, max_speed, current_a, capacity_mah, yaw_rate_dps)
        if result is None:
            self.route_distance_label.setText("-")
            self.route_time_label.setText("Ошибка: макс. скорость должна быть > 0")
            self.route_turns_label.setText("-")
            self.route_energy_label.setText("-")
            return

        self.route_distance_label.setText(f"{result['distance_m']:.0f} м")
        self.route_time_label.setText(f"{result['time_s'] / 60.0:.1f} мин (при скорости {max_speed:.1f} м/с)")
        if result["turn_count"] > 0:
            self.route_turns_label.setText(
                f"~{result['turn_time_s'] / 60.0:.1f} мин в {result['turn_count']} точках "
                f"(при скорости разворота {yaw_rate_dps:.0f} °/с)")
        else:
            self.route_turns_label.setText("Поворотов нет")

        if current_a <= 0.0:
            self.route_energy_label.setText("Нет данных о токе - оценка недоступна, пока дрон не в полёте")
        elif result["percent_of_capacity"] is not None:
            self.route_energy_label.setText(
                f"~{result['consumed_mah']:.0f} мАч (~{result['percent_of_capacity']:.1f}% ёмкости, "
                f"по текущему току {current_a:.1f} А)")
        else:
            self.route_energy_label.setText(f"~{result['consumed_mah']:.0f} мАч (ёмкость батареи не задана)")

    def _clear_route(self):
        """Сбросить маршрут на карте и в состоянии Python (через _on_route_updated)."""
        if self._navigator is not None:
            self._stop_navigation()
        self.map_view.clear_route()

    def _send_wifi_config(self):
        if not self.ws_client.is_connected():
            QMessageBox.warning(self, "Нет соединения",
                                "Сначала подключитесь к точке доступа ESP32 и нажмите «Подключиться» выше.")
            return
        ssid = self.wifi_ssid_input.text().strip()
        if not ssid:
            QMessageBox.warning(self, "Нет SSID", "Введите имя сети (SSID).")
            return
        confirm = QMessageBox.question(
            self, "Подтверждение",
            f"Отправить на ESP32 новые данные сети \"{ssid}\"? "
            "ESP32 попробует переподключиться, точка доступа для GUI не отключится.")
        if confirm != QMessageBox.StandardButton.Yes:
            return
        sent = self.ws_client.send_wifi_config(
            ssid, self.wifi_identity_input.text().strip(), self.wifi_username_input.text().strip(),
            self.wifi_password_input.text(), self.wifi_enterprise_check.isChecked())
        if sent:
            self.wifi_status_label.setText(f"Отправлено на ESP32: SSID=\"{ssid}\".")
        else:
            self.wifi_status_label.setText("Не удалось отправить - нет соединения с ESP32.")

    # ---------- навигация ----------
    def _start_navigation(self):
        if self._cli_busy:
            QMessageBox.warning(self, "FC занята", "Идёт обмен с FC по CLI (она перезагружается). Дождитесь окончания.")
            return
        if not self.ws_client.is_connected():
            QMessageBox.warning(self, "Нет соединения", "Сначала подключитесь к ESP32.")
            return
        if self._est is None:
            QMessageBox.warning(self, "Нет старта", "Сначала задайте стартовую точку дрона на карте.")
            return
        if not self._pending_route:
            QMessageBox.warning(self, "Нет маршрута", "Сначала выберите точки на карте.")
            return
        confirm = QMessageBox.question(
            self, "Подтверждение",
            f"Маршрут из {len(self._pending_route)} точек. Автономный полёт перекроет ручное управление "
            "ROLL/PITCH/YAW до нажатия «Стоп». Положение дрона - ОЦЕНКА без GPS, ошибка растёт со временем. "
            "Дрон в ANGLE-режиме, газ держите вручную. Продолжить?")
        if confirm != QMessageBox.StandardButton.Yes:
            return
        lim = self.settings_panel.get_limits()
        self._navigator = WaypointNavigator(
            waypoints=list(self._pending_route), max_speed_mps=lim["max_speed_mps"], kd=lim["drag_kd"],
            angle_limit_deg=lim["max_tilt_deg"], deadband_us=lim["rc_deadband_us"],
            arrival_radius_m=lim.get("arrival_radius_m", 3.0))
        self._nav_timer.start()
        self.start_nav_btn.setEnabled(False)
        self.stop_nav_btn.setEnabled(True)
        self.route_status_label.setText(f"Навигация запущена (0/{len(self._pending_route)})...")

    def _set_rc(self, idx: int, value: int):
        self._rc[idx] = value
        self.ws_client.send_channel(idx, value)

    def _stop_navigation(self):
        self._nav_timer.stop()
        self._navigator = None
        self.start_nav_btn.setEnabled(len(self._pending_route) > 0 and not self._cli_busy)
        self.stop_nav_btn.setEnabled(False)
        self.route_status_label.setText("Навигация остановлена")
        if self.ws_client.is_connected():
            for idx in (ROLL_IDX, PITCH_IDX, YAW_IDX):
                self._set_rc(idx, 1500)

    def _navigation_tick(self):
        if self._navigator is None or self._est is None:
            return
        roll_us, pitch_us, yaw_us, finished = self._navigator.update(self._est.lat, self._est.lon, self._est.psi)
        self._set_rc(ROLL_IDX, roll_us)
        self._set_rc(PITCH_IDX, pitch_us)
        self._set_rc(YAW_IDX, yaw_us)
        if not finished:
            current, total = self._navigator.progress()
            self.route_status_label.setText(f"Навигация запущена ({current}/{total})...")
        else:
            self.route_status_label.setText("Маршрут завершён")
            self._stop_navigation()

    def closeEvent(self, event):
        cli_ws.request_abort()      # прервать возможный обмен с FC, чтобы процесс не завис при выходе
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
