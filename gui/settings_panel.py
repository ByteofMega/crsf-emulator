"""settings_panel.py - настройки FC через CLI (проводом: GUI -> ESP32 -> UART3 FC).

Вкладка прокручиваемая (QScrollArea): все блоки сохраняют нормальную высоту и не накладываются
друг на друга в невысоком окне.
Блоки: чтение/запись параметров, калибровка kd, полный дамп CLI (dump all / diff all),
ручная команда, консоль обмена.

Имена CLI-переменных зависят от версии Betaflight:
- лимит наклона в ANGLE: angle_limit (4.5 и новее), level_limit (4.0-4.4);
- P-усиление самовыравнивания: angle_p_gain (4.5 и новее), angle_level_strength (4.0-4.4).
Параметры gps_rescue_* - это лимиты GPS Rescue, а не наклон по стику, поэтому для угла не используются.
"""

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from cli_ws import FcCliClient
from position_estimator import calibrate_kd


def _from_raw(raw: str, scale: float, offset: float) -> float:
    return float(raw) / scale - offset


def _to_raw(value: float, scale: float, offset: float) -> int:
    return int(round((value + offset) * scale))


FC_PARAMS = [
    {"key": "max_speed_mps", "label": "Макс. скорость маршрута (м/с)",
     "candidates": ["gps_rescue_ground_speed"], "scale": 100.0, "offset": 0.0, "default": 5.0},
    {"key": "max_tilt_deg", "label": "Макс. угол наклона в ANGLE-режиме (°)",
     "candidates": ["angle_limit", "level_limit"],
     "scale": 1.0, "offset": 0.0, "default": 30.0},
    {"key": "arrival_radius_m", "label": "Радиус прилёта в точку, м (только GUI)",
     "candidates": [], "scale": 1.0, "offset": 0.0, "default": 3.0},
    {"key": "yaw_rate_dps", "label": "Скорость разворота, °/с (оценка курса и времени)",
     "candidates": [], "scale": 1.0, "offset": 0.0, "default": 180.0},
    {"key": "rc_deadband_us", "label": "RC-дедбэнд по стикам, мкс (CLI: deadband)",
     "candidates": ["deadband"], "scale": 1.0, "offset": 0.0, "default": 0.0},
    {"key": "angle_p_gain", "label": "P self-level ANGLE (CLI: angle_p_gain / angle_level_strength)",
     "candidates": ["angle_p_gain", "angle_level_strength"], "scale": 1.0, "offset": 0.0, "default": 50.0},
    {"key": "drag_kd", "label": "Коэф. сопротивления kd, 1/м (калибровка ниже, только GUI)",
     "candidates": [], "scale": 1.0, "offset": 0.0, "default": 0.05},
    {"key": "liftoff_throttle_us", "label": "Газ «в воздухе», мкс (оценка положения, только GUI)",
     "candidates": [], "scale": 1.0, "offset": 0.0, "default": 1300.0},
    {"key": "battery_capacity_mah", "label": "Ёмкость батареи, мАч (только GUI)",
     "candidates": [], "scale": 1.0, "offset": 0.0, "default": 1500.0},
]

CONSOLE_PLACEHOLDER = (
    "Здесь будет обмен командами с FC по CLI (через ESP32), например:\n"
    ">> get level_limit\n<< level_limit = 55\n"
    "Нажмите «Прочитать с FC», «Записать на FC» или «dump all»."
)


class FcSettingsPanel(QScrollArea):
    def __init__(self, ws_client, nav_active=None, parent=None):
        super().__init__(parent)
        self._ws = ws_client
        self._nav_active = nav_active if nav_active is not None else (lambda: False)
        self.limits = {p["key"]: p["default"] for p in FC_PARAMS}
        self._resolved_names: dict = {}
        self._last_dump = ""

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(12)

        params_box = QGroupBox("Параметры для планирования маршрута и настройки FC")
        params_layout = QFormLayout(params_box)
        params_layout.setVerticalSpacing(8)
        self.inputs: dict = {}
        for p in FC_PARAMS:
            field = QLineEdit(str(p["default"]))
            field.setMinimumHeight(26)
            self.inputs[p["key"]] = field
            params_layout.addRow(f"{p['label']}:", field)
        read_btn = QPushButton("Прочитать с FC")
        read_btn.clicked.connect(self._read_from_fc)
        write_btn = QPushButton("Записать на FC")
        write_btn.clicked.connect(self._write_to_fc)
        params_layout.addRow(read_btn)
        params_layout.addRow(write_btn)
        layout.addWidget(params_box)

        cal_box = QGroupBox("Калибровка kd: постоянный стик PITCH, пролёт известной дистанции")
        cal_layout = QFormLayout(cal_box)
        self.cal_stick = QLineEdit("50")
        self.cal_dist = QLineEdit("20")
        self.cal_time = QLineEdit("")
        cal_layout.addRow("Стик PITCH, % хода:", self.cal_stick)
        cal_layout.addRow("Дистанция на установившейся скорости, м:", self.cal_dist)
        cal_layout.addRow("Время пролёта, с:", self.cal_time)
        cal_btn = QPushButton("Вычислить и подставить kd")
        cal_btn.clicked.connect(self._calibrate_kd)
        cal_layout.addRow(cal_btn)
        layout.addWidget(cal_box)

        dump_box = QGroupBox("Полный дамп настроек CLI")
        dump_layout = QHBoxLayout(dump_box)
        for text, handler in (("dump all", lambda: self._dump("dump all")),
                              ("diff all", lambda: self._dump("diff all")),
                              ("Сохранить дамп в файл", self._save_dump)):
            b = QPushButton(text)
            b.clicked.connect(handler)
            dump_layout.addWidget(b)
        layout.addWidget(dump_box)

        manual_box = QGroupBox("Ручная команда CLI (get ищет по части имени, например: get angle)")
        manual_layout = QHBoxLayout(manual_box)
        self.manual_input = QLineEdit()
        self.manual_input.setPlaceholderText("например: get level")
        self.manual_input.returnPressed.connect(self._send_manual)
        manual_btn = QPushButton("Отправить")
        manual_btn.clicked.connect(self._send_manual)
        manual_layout.addWidget(self.manual_input)
        manual_layout.addWidget(manual_btn)
        layout.addWidget(manual_box)

        console_box = QGroupBox("Консоль CLI (лог обмена с FC)")
        console_layout = QVBoxLayout(console_box)
        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setPlainText(CONSOLE_PLACEHOLDER)
        self.console.setMinimumHeight(200)
        self.console.setStyleSheet(
            "QPlainTextEdit { background-color: #000000; color: #00E676; border: 1px solid #00A854;"
            " border-radius: 4px; padding: 6px; }")
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSize(10)
        self.console.setFont(mono)
        console_layout.addWidget(self.console)
        clear_btn = QPushButton("Очистить консоль")
        clear_btn.clicked.connect(self._clear_console)
        console_layout.addWidget(clear_btn)
        layout.addWidget(console_box)

        self._console_has_real_output = False

        self.setWidgetResizable(True)
        self.setWidget(content)

    # ---------- консоль ----------
    def _clear_console(self):
        self.console.clear()
        self._console_has_real_output = False

    def _log_to_console(self, line: str):
        if not self._console_has_real_output:
            self.console.clear()
            self._console_has_real_output = True
        self.console.appendPlainText(line)

    # ---------- параметры ----------
    def _apply_manual_inputs(self, silent: bool = False) -> bool:
        parsed = {}
        try:
            for key, field in self.inputs.items():
                parsed[key] = float(field.text().replace(",", "."))
        except ValueError:
            if not silent:
                QMessageBox.warning(self, "Ошибка ввода", "Все поля должны быть числами.")
            return False
        self.limits.update(parsed)
        return True

    def _param_scale(self, param: dict, resolved_name: str) -> float:
        return param.get("scale_by_name", {}).get(resolved_name, param["scale"])

    def _ready(self, need_idle_nav: bool = False) -> bool:
        if not self._ws.is_connected():
            QMessageBox.warning(self, "Нет соединения",
                                "Сначала подключитесь к ESP32 (кнопка «Подключиться» вверху).")
            return False
        if need_idle_nav and self._nav_active():
            QMessageBox.warning(self, "Идёт навигация", "Остановите маршрут перед работой с CLI.")
            return False
        return True

    def _read_from_fc(self):
        if not self._ready(True):
            return
        client = FcCliClient(self._ws, log_callback=self._log_to_console)
        found, total = [], 0
        missing = []
        try:
            client.enter_cli()
            for p in FC_PARAMS:
                if not p["candidates"]:
                    continue
                total += 1
                name, raw = client.get_first_available(p["candidates"])
                if raw is None:
                    missing.append(p["key"])
                    self._log_to_console(f"-- {p['key']}: параметр не найден в этой прошивке "
                                         f"(пробовали: {', '.join(p['candidates'])})")
                    continue
                value = _from_raw(raw, self._param_scale(p, name), p["offset"])
                self.inputs[p["key"]].setText(f"{value:.3f}")
                self._resolved_names[p["key"]] = name
                found.append(f"{p['key']} <- {name} = {raw}")
            client.exit_cli(save_changes=False)
        except Exception as exc:
            client.close()
            self._log_to_console(f"!! Ошибка чтения CLI: {exc}")
            QMessageBox.critical(self, "Ошибка чтения CLI", str(exc))
            return
        self._apply_manual_inputs()
        if not found:
            self._log_to_console("!! Ни один параметр не прочитан: FC не отвечает на CLI-команды (см. лог выше).")
            QMessageBox.warning(self, "Чтение CLI", "Ни один параметр не прочитан. Проверьте связь на вкладке «MSP».")
            return
        self._log_to_console(f"-- Прочитано параметров: {len(found)} из {total}:")
        for line in found:
            self._log_to_console(f"   {line}")
        if missing:
            self._log_to_console(f"-- Не найдено: {', '.join(missing)}. Найти имя: в «Ручной команде» "
                                 "выполните, например, get angle или get level")

    def _write_to_fc(self):
        if not self._apply_manual_inputs() or not self._ready(True):
            return
        confirm = QMessageBox.question(
            self, "Подтверждение", "Запись настроек вызовет перезагрузку полётного контроллера. Продолжить?")
        if confirm != QMessageBox.StandardButton.Yes:
            return
        client = FcCliClient(self._ws, log_callback=self._log_to_console)
        written = 0
        try:
            client.enter_cli()
            for p in FC_PARAMS:
                if not p["candidates"]:
                    continue
                name, _ = client.get_first_available(p["candidates"])
                if name is None:
                    self._log_to_console(f"-- {p['key']}: параметр не найден в этой прошивке, пропущен")
                    continue
                raw = _to_raw(self.limits[p["key"]], self._param_scale(p, name), p["offset"])
                client.set_value(name, raw)
                self._resolved_names[p["key"]] = name
                written += 1
            client.exit_cli(save_changes=True)
        except Exception as exc:
            client.close()
            self._log_to_console(f"!! Ошибка записи CLI: {exc}")
            QMessageBox.critical(self, "Ошибка записи CLI", str(exc))
            return
        self._log_to_console(f"-- Отправлено параметров: {written}. FC перезагружается. "
                             "Проверьте результат кнопкой «Прочитать с FC».")

    # ---------- калибровка ----------
    def _calibrate_kd(self):
        try:
            stick = float(self.cal_stick.text().replace(",", ".")) / 100.0
            dist = float(self.cal_dist.text().replace(",", "."))
            t = float(self.cal_time.text().replace(",", "."))
            lim = float(self.inputs["max_tilt_deg"].text().replace(",", "."))
            if min(stick, dist, t, lim) <= 0:
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "Калибровка", "Заполните стик, дистанцию и время положительными числами.")
            return
        kd = calibrate_kd(stick, lim, dist, t)
        self.inputs["drag_kd"].setText(f"{kd:.5f}")
        self._apply_manual_inputs()
        self._log_to_console(f"-- kd = {kd:.5f} (v = {dist / t:.2f} м/с при угле {stick * lim:.1f}°)")

    # ---------- дамп и ручные команды ----------
    def _dump(self, kind: str):
        if not self._ready(True):
            return
        client = FcCliClient(self._ws, log_callback=self._log_to_console)
        try:
            client.enter_cli()
            lines = client.dump(kind)
            client.exit_cli(save_changes=False)
        except Exception as exc:
            client.close()
            QMessageBox.critical(self, "Ошибка CLI", str(exc))
            return
        self._last_dump = "\n".join(lines)
        self._log_to_console(f"-- {kind}: получено строк: {len(lines)}")

    def _save_dump(self):
        if not self._last_dump:
            QMessageBox.information(self, "Дамп", "Сначала выполните dump all или diff all.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить дамп", "betaflight_dump.txt", "Text (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._last_dump)

    def _send_manual(self):
        text = self.manual_input.text().strip()
        if not text or not self._ready(True):
            return
        first = text.lower().split()[0]
        if first in {"save", "exit", "bl", "defaults", "dfu", "resource", "flash_erase", "reboot"}:
            QMessageBox.warning(self, "Команда заблокирована",
                                "Сохранение и опасные команды выполняются только кнопками выше.")
            return
        client = FcCliClient(self._ws, log_callback=self._log_to_console)
        try:
            client.enter_cli()
            client._send_line(text)
            client.exit_cli(save_changes=False)
        except Exception as exc:
            client.close()
            QMessageBox.critical(self, "Ошибка CLI", str(exc))

    def get_limits(self, silent: bool = False) -> dict:
        """Текущие значения параметров (silent=True - без всплывающих окон, для таймеров)."""
        self._apply_manual_inputs(silent=silent)
        return dict(self.limits)
