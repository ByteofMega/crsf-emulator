"""flight_panel.py - вкладка "Запись полёта": запись управления с пульта и воспроизведение с коррекцией по IMU.

Запись: кнопка "Запись" - 20 раз в секунду сохраняются все 16 каналов и углы ROLL/PITCH/YAW из MSP_ATTITUDE.
Воспроизведение: кнопка "Воспроизвести" - каналы подаются по записанному времени, а ROLL/PITCH/YAW корректируются по
отклонению текущих углов IMU от записанных (логика в flight_recorder.py).
Запись сохраняется в CSV ("Сохранить...") и загружается обратно ("Загрузить...").

ФУНКЦИИ И КЛАССЫ ФАЙЛА
----------------------
class FlightPanel
  __init__(ws_client, get_rc, set_rc, nav_active, sync_ui) - get_rc() -> список 16 каналов; set_rc(idx, us) - отправить
      канал в ESP32; nav_active() - идёт ли навигация по карте; sync_ui(values) - обновить ползунки.
  on_attitude(roll, pitch, yaw) - вызывается из main при каждом кадре MSP_ATTITUDE.
  is_active() - идёт запись или воспроизведение.
  stop_all() - остановить всё (воспроизведение -> безопасное состояние: стики по центру, газ минимум, дизарм).
  _start_record / _stop_record / _start_replay / _stop_replay / _tick - кнопки и таймер 50 мс.
"""
import os
import time

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (QCheckBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                             QMessageBox, QPushButton, QVBoxLayout, QWidget)

import flight_recorder as fr
from common import parse_float, push_updates, safe_updates

TICK_MS = 50
UI_SYNC_EVERY = 4


class FlightPanel(QWidget):
    def __init__(self, ws_client, get_rc, set_rc, nav_active, sync_ui, parent=None):
        super().__init__(parent)
        self.ws, self._get_rc, self._set_rc = ws_client, get_rc, set_rc
        self._nav_active, self._sync_ui = nav_active, sync_ui
        self.mode = "idle"
        self.recording = None
        self._att = None
        self._att_t = 0.0
        self._recorder = fr.Recorder()
        self._player = None
        self._ticks = 0

        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)
        self.ws.disconnected.connect(self.stop_all)

        lay = QVBoxLayout(self)
        rec_box = QGroupBox("Запись и воспроизведение")
        rl = QHBoxLayout(rec_box)
        self.rec_btn = QPushButton("● Запись")
        self.rec_btn.clicked.connect(self._start_record)
        self.stop_btn = QPushButton("■ Стоп")
        self.stop_btn.clicked.connect(self._on_stop_clicked)
        self.play_btn = QPushButton("▶ Воспроизвести")
        self.play_btn.clicked.connect(self._start_replay)
        self.save_btn = QPushButton("Сохранить...")
        self.save_btn.clicked.connect(self._save)
        self.load_btn = QPushButton("Загрузить...")
        self.load_btn.clicked.connect(self._load)
        for b in (self.rec_btn, self.stop_btn, self.play_btn, self.save_btn, self.load_btn):
            rl.addWidget(b)
        lay.addWidget(rec_box)

        par_box = QGroupBox("Коррекция по IMU (ROLL / PITCH / YAW)")
        pl = QFormLayout(par_box)
        self.corr_check = QCheckBox("Включить коррекцию по углам IMU")
        self.corr_check.setChecked(True)
        pl.addRow(self.corr_check)
        self.kp_input = QLineEdit("0.7")
        self.ki_input = QLineEdit("0.4")
        self.max_input = QLineEdit("15")
        pl.addRow("Kp (пропорциональный):", self.kp_input)
        pl.addRow("Ki (интегральный, 1/с):", self.ki_input)
        pl.addRow("Макс. добавка к стику, % хода:", self.max_input)
        lay.addWidget(par_box)

        self.status = QLabel("Запись не сделана. Нажмите «Запись» и управляйте каналами.")
        self.status.setWordWrap(True)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        lay.addWidget(self.status)
        lay.addWidget(self.info)
        lay.addStretch(1)
        self._update_buttons()

    # ---------- данные IMU из main ----------
    def on_attitude(self, roll, pitch, yaw):
        self._att = (roll, pitch, yaw)
        self._att_t = time.monotonic()

    def _att_now(self):
        if self._att is None:
            return None, None
        return self._att, time.monotonic() - self._att_t

    # ---------- состояние ----------
    def is_active(self):
        return self.mode != "idle"

    def _update_buttons(self):
        idle = self.mode == "idle"
        self.rec_btn.setEnabled(idle)
        self.play_btn.setEnabled(idle and self.recording is not None)
        self.save_btn.setEnabled(idle and self.recording is not None)
        self.load_btn.setEnabled(idle)
        self.stop_btn.setEnabled(not idle)

    def _on_stop_clicked(self):
        if self.mode == "record":
            self._stop_record()
        else:
            self._stop_replay("Воспроизведение остановлено оператором. Дрон переведён в безопасное состояние.")

    def _check_ready(self):
        if not self.ws.is_connected():
            QMessageBox.warning(self, "Нет соединения", "Сначала подключитесь к ESP32.")
            return False
        if self._nav_active():
            QMessageBox.warning(self, "Идёт навигация", "Остановите навигацию по маршруту на вкладке «Карта».")
            return False
        return True

    # ---------- запись ----------
    def _start_record(self):
        if not self._check_ready():
            return
        self._recorder.start(time.monotonic())
        self.mode = "record"
        self._ticks = 0
        self._timer.start()
        self.status.setText("ИДЁТ ЗАПИСЬ: управляйте каналами на вкладке «Каналы». Нажмите «Стоп» для завершения.")
        self._update_buttons()

    def _stop_record(self):
        self._timer.stop()
        self.mode = "idle"
        self.recording = self._recorder.finish()
        rec = self.recording
        if len(rec.samples) < 30:
            self.recording = None
            self.status.setText("Запись слишком короткая (меньше 30 отсчётов) - не сохранена.")
        else:
            with_att = sum(1 for s in rec.samples if s[2] is not None)
            kr, kp, ky = rec.gains()
            names = [n for n, k in (("ROLL", kr), ("PITCH", kp), ("YAW", ky)) if k]
            self.status.setText(
                f"Запись готова: {rec.duration:.1f} с, {len(rec.samples)} отсчётов, углы IMU в {with_att}. "
                f"Коррекция возможна по осям: {', '.join(names) if names else 'нет (оси почти не двигались)'}. "
                + ("" if rec.start_is_safe() else "ВНИМАНИЕ: запись начинается не с дизарма и минимального газа."))
        self.info.setText("")
        self._update_buttons()

    # ---------- воспроизведение ----------
    def _read_params(self):
        try:
            kp = parse_float(self.kp_input.text())
            ki = parse_float(self.ki_input.text())
            cm = parse_float(self.max_input.text()) / 100.0
            if not (0 <= kp <= 3 and 0 <= ki <= 3 and 0 < cm <= 0.5):
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "Параметры", "Kp и Ki - числа 0..3, макс. добавка - 1..50 %.")
            return None
        return kp, ki, cm

    def _start_replay(self):
        if self.recording is None or not self._check_ready():
            return
        params = self._read_params()
        if params is None:
            return
        kp, ki, cm = params
        warn = "" if self.recording.start_is_safe() else (
            "\n\nЗапись начинается НЕ с дизарма и минимального газа - при старте каналы мгновенно перескочат "
            "на записанные значения!")
        if self._att_now()[0] is None and self.corr_check.isChecked():
            warn += "\n\nКадры MSP_ATTITUDE не приходят - коррекция по IMU работать не будет."
        ok = QMessageBox.question(
            self, "Воспроизведение",
            f"Воспроизвести запись ({self.recording.duration:.1f} с)? Каналы будут поданы автоматически.\n"
            "Дрон закреплён, питание можно отключить мгновенно. «Стоп» переводит дрон в безопасное состояние." + warn)
        if ok != QMessageBox.StandardButton.Yes:
            return
        self._player = fr.Replayer(self.recording, kp=kp, ki=ki, corr_max_frac=cm,
                                   corr_enabled=self.corr_check.isChecked())
        att, age = self._att_now()
        self._player.start(time.monotonic(), att if age is not None and age <= fr.ATT_STALE_S else None)
        self.mode = "replay"
        self._ticks = 0
        self._timer.start()
        self.status.setText("ИДЁТ ВОСПРОИЗВЕДЕНИЕ записи...")
        self._update_buttons()

    def _apply_channels(self, channels):
        cur = self._get_rc()
        for idx, value in enumerate(channels):
            if cur[idx] != value:
                self._set_rc(idx, int(value))

    def _stop_replay(self, message):
        if self.mode != "replay":
            return
        self._timer.stop()
        self.mode = "idle"
        push_updates(self._get_rc(), safe_updates(), self._set_rc)
        self._sync_ui(list(self._get_rc()))
        self.status.setText(message)
        self.info.setText("")
        self._update_buttons()

    def stop_all(self):
        if self.mode == "record":
            self._stop_record()
        elif self.mode == "replay":
            self._stop_replay("Воспроизведение прервано (обрыв связи или закрытие окна). Дрон в безопасном состоянии.")

    # ---------- таймер ----------
    def _tick(self):
        now = time.monotonic()
        att, age = self._att_now()
        fresh = att if (age is not None and age <= fr.ATT_STALE_S) else None
        self._ticks += 1
        if self.mode == "record":
            if not self._recorder.add(now, self._get_rc(), fresh):
                self._stop_record()
                return
            n = len(self._recorder.samples)
            self.info.setText(f"Записано: {n * TICK_MS / 1000.0:.1f} с; IMU: "
                              f"{'идёт' if fresh else 'НЕТ данных MSP_ATTITUDE'}")
        elif self.mode == "replay":
            if self._nav_active():
                self._stop_replay("Воспроизведение остановлено: запущена навигация по карте.")
                return
            channels, done, info = self._player.step(now, fresh, age)
            if done:
                self._stop_replay("Воспроизведение завершено. Дрон переведён в безопасное состояние.")
                return
            self._apply_channels(channels)
            if self._ticks % UI_SYNC_EVERY == 0:
                self._sync_ui(list(self._get_rc()))
                if info["active"]:
                    def f(v, u):
                        return "-" if v is None else f"{v:+.1f}° ({u:+d} мкс)"
                    self.info.setText(
                        f"t = {info['t']:.1f} / {self.recording.duration:.1f} с | ошибка: "
                        f"ROLL {f(info['err_roll'], info['corr_roll_us'])}, "
                        f"PITCH {f(info['err_pitch'], info['corr_pitch_us'])}, "
                        f"YAW {f(info['err_yaw'], info['corr_yaw_us'])}")
                else:
                    self.info.setText(f"t = {info['t']:.1f} / {self.recording.duration:.1f} с | коррекция не активна "
                                      "(выключена, нет свежего IMU или дрон не в воздухе)")

    # ---------- файлы ----------
    def _save(self):
        if self.recording is None:
            return
        os.makedirs("flights", exist_ok=True)
        default = os.path.join("flights", time.strftime("flight_%Y%m%d_%H%M%S.csv"))
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить запись", default, "CSV (*.csv)")
        if path:
            self.recording.save_csv(path)
            self.status.setText(f"Запись сохранена: {path}")

    def _load(self):
        path, _ = QFileDialog.getOpenFileName(self, "Загрузить запись", "flights", "CSV (*.csv)")
        if not path:
            return
        try:
            self.recording = fr.FlightRecording.load_csv(path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Ошибка загрузки", str(exc))
            return
        kr, kp, ky = self.recording.gains()
        names = [n for n, k in (("ROLL", kr), ("PITCH", kp), ("YAW", ky)) if k]
        self.status.setText(f"Загружено: {path} - {self.recording.duration:.1f} с. Коррекция по осям: "
                            f"{', '.join(names) if names else 'нет'}.")
        self._update_buttons()
