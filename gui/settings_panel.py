"""settings_panel.py - настройки FC через CLI (проводом: GUI -> ESP32 -> UART3 FC).

На время обмена с FC блокируются ТОЛЬКО кнопки CLI этой вкладки (и поле ручной команды); остальное окно -
карта, каналы, вкладка MSP, отключение - остаётся рабочим. Сигнал busy_changed позволяет главному окну
заблокировать запуск маршрута, пока FC занята (она перезагружается после выхода из CLI).

Чтение: значения подтверждаются двумя одинаковыми ответами и проверяются по "Allowed range" (cli_ws.read_many).
Запись: пишутся только изменённые параметры; каждое записанное значение перечитывается и подтверждается;
сохранение (save, перезагрузка FC) выполняется только если подтверждено ВСЁ, иначе изменения сбрасываются.
Найденные имена параметров запоминаются (QSettings) - следующие чтения быстрее.

Имена CLI-переменных зависят от версии Betaflight (версию панель получает по MSP_FC_VERSION):
- лимит наклона в ANGLE: angle_limit (4.5 и новее), level_limit (4.0-4.4);
- P-усиление самовыравнивания: angle_p_gain (4.5 и новее), angle_level_strength (4.0-4.4).
Пока версия неизвестна, опрашиваются оба варианта.
"""

from PyQt6.QtCore import QSettings, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

import cli_ws
from cli_ws import FcCliClient, same_number
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

OLD_ONLY = {"level_limit", "angle_level_strength"}   # Betaflight 4.0-4.4
NEW_ONLY = {"angle_limit", "angle_p_gain"}           # Betaflight 4.5 и новее

CONSOLE_PLACEHOLDER = (
    "Здесь будет обмен командами с FC по CLI (через ESP32), например:\n"
    ">> get level_limit\n<< level_limit = 55\n"
    "Нажмите «Прочитать с FC», «Записать на FC» или «dump all»."
)


def filter_candidates(cands: list, fw) -> list:
    """Оставить имена, подходящие версии прошивки fw = (major, minor, patch); неизвестная версия - все имена."""
    if not fw or fw[0] != 4:
        return list(cands)
    drop = NEW_ONLY if fw[1] <= 4 else OLD_ONLY
    return [c for c in cands if c not in drop] or list(cands)


class FcSettingsPanel(QScrollArea):
    busy_changed = pyqtSignal(bool)

    def __init__(self, ws_client, nav_active=None, parent=None):
        super().__init__(parent)
        self._ws = ws_client
        self._nav_active = nav_active if nav_active is not None else (lambda: False)
        self.limits = {p["key"]: p["default"] for p in FC_PARAMS}
        self._last_dump = ""
        self._busy = False
        self._fw = None
        self._cli_widgets = []
        self._qs = QSettings("CRSFEmulator", "GUI")
        self._ws.msp_received.connect(self._on_msp_version)
        self._ws.connected.connect(lambda: QTimer.singleShot(1500, lambda: self._ws.request_msp(3)))

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
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        params_layout.addRow(self.status_label)
        self._cli_widgets += [read_btn, write_btn]
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
                              ("diff all", lambda: self._dump("diff all"))):
            b = QPushButton(text)
            b.clicked.connect(handler)
            dump_layout.addWidget(b)
            self._cli_widgets.append(b)
        save_dump_btn = QPushButton("Сохранить дамп в файл")
        save_dump_btn.clicked.connect(self._save_dump)
        dump_layout.addWidget(save_dump_btn)
        layout.addWidget(dump_box)

        manual_box = QGroupBox("Ручная команда CLI (get ищет по части имени, например: get level)")
        manual_layout = QHBoxLayout(manual_box)
        self.manual_input = QLineEdit()
        self.manual_input.setPlaceholderText("например: get level")
        self.manual_input.returnPressed.connect(self._send_manual)
        manual_btn = QPushButton("Отправить")
        manual_btn.clicked.connect(self._send_manual)
        manual_layout.addWidget(self.manual_input)
        manual_layout.addWidget(manual_btn)
        self._cli_widgets += [self.manual_input, manual_btn]
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

    # ---------- версия прошивки (по MSP_FC_VERSION) ----------
    def _on_msp_version(self, cmd, data, err):
        if cmd == 3 and not err and len(data) >= 3:
            self._fw = (data[0], data[1], data[2])

    # ---------- занятость: блокируются только кнопки CLI ----------
    def is_busy(self) -> bool:
        return self._busy

    def _begin(self, what: str) -> bool:
        if self._busy:
            return False
        cli_ws.reset_abort()
        self._busy = True
        for w in self._cli_widgets:
            w.setEnabled(False)
        self.status_label.setText(f"⏳ {what}… (кнопки CLI временно недоступны, остальное окно работает)")
        self.busy_changed.emit(True)
        return True

    def _end(self, message: str = ""):
        self._busy = False
        for w in self._cli_widgets:
            w.setEnabled(True)
        self.status_label.setText(message)
        self.busy_changed.emit(False)

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
        if self._busy:
            QMessageBox.information(self, "FC занята", "Дождитесь окончания текущей операции с FC.")
            return False
        if not self._ws.is_connected():
            QMessageBox.warning(self, "Нет соединения",
                                "Сначала подключитесь к ESP32 (кнопка «Подключиться» вверху).")
            return False
        if need_idle_nav and self._nav_active():
            QMessageBox.warning(self, "Идёт навигация", "Остановите маршрут перед работой с CLI.")
            return False
        return True

    def _requests(self) -> dict:
        return {p["key"]: filter_candidates(p["candidates"], self._fw) for p in FC_PARAMS if p["candidates"]}

    def _load_names(self) -> dict:
        names = {}
        for key, cands in self._requests().items():
            n = self._qs.value(f"fc_names/{key}", "", type=str)
            if n in cands:
                names[key] = n
        return names

    def _save_names(self, found: dict):
        for key, (name, _raw) in found.items():
            self._qs.setValue(f"fc_names/{key}", name)

    def _fail(self, title: str, exc: Exception):
        if isinstance(exc, cli_ws.CliAborted):
            return
        self._log_to_console(f"!! {title}: {exc}")
        QMessageBox.critical(self, title, str(exc))

    def _read_from_fc(self):
        if not self._ready(True) or not self._begin("Чтение настроек с FC"):
            return
        client = FcCliClient(self._ws, log_callback=self._log_to_console)
        message = ""
        try:
            client.enter_cli()
            requests = self._requests()
            found = client.read_many(requests, cached=self._load_names())
            client.exit_cli(save_changes=False)
            total = len(requests)
            for p in FC_PARAMS:
                if p["key"] not in found:
                    continue
                name, raw = found[p["key"]]
                self.inputs[p["key"]].setText(f"{_from_raw(raw, self._param_scale(p, name), p['offset']):.3f}")
            self._save_names(found)
            self._apply_manual_inputs()
            if not found:
                message = "Ни один параметр не прочитан: FC не отвечает на CLI-команды."
                self._log_to_console("!! " + message)
            else:
                missing = [k for k in requests if k not in found]
                message = f"Прочитано параметров: {len(found)} из {total}" + (f"; не найдено: {', '.join(missing)}" if missing else "")
                self._log_to_console("-- " + message)
                for key, (name, raw) in found.items():
                    self._log_to_console(f"   {key} <- {name} = {raw}")
        except Exception as exc:
            client.close()
            message = "Чтение прервано"
            self._fail("Ошибка чтения CLI", exc)
        finally:
            self._end(message)

    def _write_to_fc(self):
        if not self._apply_manual_inputs() or not self._ready(True):
            return
        confirm = QMessageBox.question(
            self, "Подтверждение",
            "Будут записаны только изменённые параметры. После записи FC перезагрузится. Продолжить?")
        if confirm != QMessageBox.StandardButton.Yes or not self._begin("Запись настроек на FC"):
            return
        client = FcCliClient(self._ws, log_callback=self._log_to_console)
        message = ""
        try:
            client.enter_cli()
            current = client.read_many(self._requests(), cached=self._load_names())
            self._save_names(current)
            changes = []
            for p in FC_PARAMS:
                if not p["candidates"]:
                    continue
                if p["key"] not in current:
                    self._log_to_console(f"-- {p['key']}: параметр не найден/не подтверждён, пропущен")
                    continue
                name, raw_old = current[p["key"]]
                raw_new = _to_raw(self.limits[p["key"]], self._param_scale(p, name), p["offset"])
                if not same_number(raw_old, raw_new):
                    changes.append((name, raw_new))
            if not changes:
                message = "Изменений нет: запись и перезагрузка FC не нужны"
                self._log_to_console("-- " + message)
                client.exit_cli(save_changes=False)
            else:
                failed = client.write_verified(changes)
                if failed:
                    client.exit_cli(save_changes=False)     # без save: изменения сбрасываются
                    message = f"Не удалось подтвердить запись: {', '.join(failed)}. Ничего не сохранено."
                    self._log_to_console("!! " + message)
                    QMessageBox.warning(self, "Запись не подтверждена", message)
                else:
                    client.exit_cli(save_changes=True)
                    message = f"Записано и подтверждено параметров: {len(changes)}. FC перезагружена."
                    self._log_to_console("-- " + message)
        except Exception as exc:
            client.close()
            message = "Запись прервана, настройки на FC не сохранены"
            self._fail("Ошибка записи CLI", exc)
        finally:
            self._end(message)

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
        if not self._ready(True) or not self._begin(f"Команда {kind}"):
            return
        client = FcCliClient(self._ws, log_callback=self._log_to_console)
        message = ""
        try:
            client.enter_cli()
            lines = client.dump(kind)
            client.exit_cli(save_changes=False)
            self._last_dump = "\n".join(lines)
            message = f"{kind}: получено строк {len(lines)}"
            self._log_to_console("-- " + message)
        except Exception as exc:
            client.close()
            message = "Операция прервана"
            self._fail("Ошибка CLI", exc)
        finally:
            self._end(message)

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
        if not self._begin(f"Команда «{text}»"):
            return
        client = FcCliClient(self._ws, log_callback=self._log_to_console)
        message = ""
        try:
            client.enter_cli()
            client._send_line(text)
            client.exit_cli(save_changes=False)
        except Exception as exc:
            client.close()
            message = "Операция прервана"
            self._fail("Ошибка CLI", exc)
        finally:
            self._end(message)

    def get_limits(self, silent: bool = False) -> dict:
        """Текущие значения параметров (silent=True - без всплывающих окон, для таймеров)."""
        self._apply_manual_inputs(silent=silent)
        return dict(self.limits)
