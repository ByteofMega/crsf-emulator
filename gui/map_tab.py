"""map_tab.py - вкладка «Карта»: карта, стартовая точка, маршрут, запуск полёта и оценка маршрута.

Положение и полёт ведёт NavigationController (navigation.py); здесь только виджеты, проверки перед
запуском и пересчёт оценки (длина, время, повороты, расход батареи).

ФУНКЦИИ И КЛАССЫ ФАЙЛА
----------------------
class MapTab
  __init__(self, nav, get_limits, flight_active, ws_client, parent=None)
    nav - NavigationController; get_limits(silent=False) - настройки; flight_active() - идёт ли запись
    или воспроизведение на вкладке «Запись полёта».
  set_theme(self, dark: bool) - тёмная или светлая карта.
  set_cli_busy(self, busy: bool) - пока идёт обмен с FC по CLI, запуск маршрута недоступен.
  on_analog(self, analog: dict) / on_battery(self, battery: dict)
    Ток из MSP_ANALOG и из телеметрии CRSF (резерв) для оценки расхода батареи.
  _enable_start_mode / _on_start_set / _reset_position
    Выбор стартовой точки кликом по карте и её сброс.
  _on_position_updated(lat, lon, text) - метка дрона на карте и строка скорости.
  _on_route_updated(waypoints) / _clear_route / _update_route_estimate
    Список точек маршрута и оценка маршрута.
  _start_navigation(self) - проверки, подтверждение и запуск; _on_running_changed(running) - кнопки.
  _refresh_start_btn(self) - кнопка «Лететь по маршруту» доступна, если есть маршрут, FC не занята и
    полёт не идёт.
"""
from PyQt6.QtWidgets import QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget

from common import parse_float
from map_view import MapView
from mission import AUTO_ARM
from route_planner import estimate_energy_usage


class MapTab(QWidget):
    def __init__(self, nav, get_limits, flight_active, ws_client, parent=None):
        super().__init__(parent)
        self._nav, self._get_limits, self._flight_active, self._ws = nav, get_limits, flight_active, ws_client
        self._pending_route: list = []
        self._cli_busy = False
        self._analog = None
        self._last_battery = None

        layout = QVBoxLayout(self)
        self.map_view = MapView()
        self.map_view.bridge.route_updated.connect(self._on_route_updated)
        self.map_view.bridge.start_set.connect(self._on_start_set)
        layout.addWidget(self.map_view)

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
        layout.addLayout(start_layout)

        nav_controls = QHBoxLayout()
        self.route_status_label = QLabel("Кликайте по карте: каждый клик - новая точка маршрута")
        nav_controls.addWidget(self.route_status_label)
        self.start_nav_btn = QPushButton("Лететь по маршруту")
        self.start_nav_btn.setEnabled(False)
        self.start_nav_btn.clicked.connect(self._start_navigation)
        nav_controls.addWidget(self.start_nav_btn)
        self.stop_nav_btn = QPushButton("Стоп")
        self.stop_nav_btn.setObjectName("danger")
        self.stop_nav_btn.setEnabled(False)
        self.stop_nav_btn.clicked.connect(self._nav.stop)
        nav_controls.addWidget(self.stop_nav_btn)
        self.clear_route_btn = QPushButton("Очистить маршрут")
        self.clear_route_btn.clicked.connect(self._clear_route)
        nav_controls.addWidget(self.clear_route_btn)
        layout.addLayout(nav_controls)

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
        layout.addWidget(energy_box)

        self._nav.position_updated.connect(self._on_position_updated)
        self._nav.status_changed.connect(self.route_status_label.setText)
        self._nav.running_changed.connect(self._on_running_changed)

    # ---------- внешние события ----------
    def set_theme(self, dark: bool):
        self.map_view.set_theme(dark)

    def set_cli_busy(self, busy: bool):
        self._cli_busy = busy
        self._refresh_start_btn()

    def on_analog(self, analog: dict):
        self._analog = analog
        self._update_route_estimate()

    def on_battery(self, battery: dict):
        self._last_battery = battery

    def _refresh_start_btn(self):
        self.start_nav_btn.setEnabled(bool(self._pending_route) and not self._cli_busy and not self._nav.is_running)

    def _on_running_changed(self, running: bool):
        self.stop_nav_btn.setEnabled(running)
        self._refresh_start_btn()

    # ---------- старт и положение ----------
    def _enable_start_mode(self):
        self.pos_label.setText("Кликните по карте: там стоит дрон")
        self.map_view.enable_start_mode()

    def _on_start_set(self, lat: float, lon: float):
        try:
            heading = parse_float(self.start_heading_input.text()) % 360.0
        except ValueError:
            QMessageBox.warning(self, "Курс", "Курс на старте должен быть числом (0 = север, 90 = восток).")
            heading = 0.0
        self._nav.set_start(lat, lon, heading)
        self.map_view.clear_track()
        self.map_view.update_drone_position(lat, lon)
        self.pos_label.setText(f"Старт: {lat:.6f}, {lon:.6f}, курс {heading:.0f}°")
        self._update_route_estimate()

    def _reset_position(self):
        self._nav.reset_position()
        self.map_view.clear_start()  # стирает старт, метку дрона и трек
        self.pos_label.setText("Старт не задан: нажмите кнопку и кликните по карте")
        self.speed_label.setText("-")
        self._update_route_estimate()

    def _on_position_updated(self, lat: float, lon: float, text: str):
        self.map_view.update_drone_position(lat, lon)
        self.speed_label.setText(text)

    # ---------- маршрут ----------
    def _on_route_updated(self, waypoints: list):
        self._pending_route = waypoints
        self._refresh_start_btn()
        if waypoints:
            self.route_status_label.setText(f"Точек в маршруте: {len(waypoints)} (финиш - точка №{len(waypoints)})")
        else:
            self.route_status_label.setText("Кликайте по карте: каждый клик - новая точка маршрута")
        self._update_route_estimate()

    def _clear_route(self):
        """Сбросить маршрут на карте и в состоянии Python (через _on_route_updated)."""
        if self._nav.is_running:
            self._nav.stop()
        self.map_view.clear_route()

    def _update_route_estimate(self):
        if not self._pending_route:
            for lbl in (self.route_distance_label, self.route_time_label,
                        self.route_turns_label, self.route_energy_label):
                lbl.setText("-")
            return

        path = list(self._pending_route)
        position = self._nav.position()
        if position is not None:
            path = [position] + path

        limits = self._get_limits(silent=True)
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

    # ---------- запуск ----------
    def _start_navigation(self):
        if self._flight_active():
            QMessageBox.warning(self, "Запись полёта",
                                "Идёт запись или воспроизведение - остановите их на вкладке «Запись полёта».")
            return
        if self._cli_busy:
            QMessageBox.warning(self, "FC занята", "Идёт обмен с FC по CLI (она перезагружается). Дождитесь окончания.")
            return
        if not self._ws.is_connected():
            QMessageBox.warning(self, "Нет соединения", "Сначала подключитесь к ESP32.")
            return
        if not self._nav.has_start:
            QMessageBox.warning(self, "Нет старта", "Сначала задайте стартовую точку дрона на карте.")
            return
        if not self._pending_route:
            QMessageBox.warning(self, "Нет маршрута", "Сначала выберите точки на карте.")
            return
        confirm = QMessageBox.question(
            self, "Подтверждение",
            f"Маршрут из {len(self._pending_route)} точек. Автономный полёт перекроет ручное управление "
            "ROLL/PITCH/YAW до нажатия «Стоп». Положение дрона - ОЦЕНКА без GPS, ошибка растёт со временем. "
            "Дрон в ANGLE-режиме. " + (
                "Программа сама включит ANGLE, заармит дрон, поднимет газ, пролетит маршрут, выполнит действия "
                "захвата и задизармит дрон (каналы и газ - в mission.py). Держите под рукой отключение питания. "
                if AUTO_ARM else "ANGLE, арм и газ держите вручную. ") + "Продолжить?")
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._nav.start(self._pending_route)
