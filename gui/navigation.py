"""navigation.py - оценка положения дрона и автономный полёт по маршруту (без виджетов).

Раньше эта логика жила прямо в MainWindow. Здесь: DeadReckoning (оценка положения по стикам и MSP),
MissionRunner (полёт по точкам), два таймера (оценка 50 мс, навигация 200 мс) и возврат каналов в
безопасное состояние при остановке. Интерфейс общается с контроллером сигналами.

ФУНКЦИИ И КЛАССЫ ФАЙЛА
----------------------
class NavigationController
    Сигналы: position_updated(lat, lon, текст скорости), status_changed(текст), running_changed(bool).
  __init__(self, ws_client, rc, get_limits, parent=None)
    rc - RcState; get_limits(silent=False) - словарь настроек из вкладки «Настройки FC».
  is_running / has_start (свойства), position() -> (lat, lon) или None
  set_start(self, lat, lon, heading_deg)
    Создаёт оценщик положения в заданной точке с заданным курсом.
  reset_position(self)
    Останавливает навигацию и удаляет оценщик.
  set_msp_yaw(self, yaw_deg) / set_armed(self, armed)
    Данные из MSP_ATTITUDE и MSP_STATUS.
  start(self, route) / stop(self)
    Запуск миссии по списку точек и остановка (стики в центр; при миссии - захват закрыт, при
    auto_arm ещё газ минимум и дизарм).
  _estimator_tick(self) / _navigation_tick(self)
    Таймеры 50 мс и 200 мс.
"""
import time

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from common import push_updates, safe_updates
from config import ANGLE_IDX, ARM_IDX, GRIPPER_IDX, PITCH_IDX, ROLL_IDX, THROTTLE_IDX, YAW_IDX
from mission import MissionRunner
from position_estimator import DeadReckoning

NAV_TICK_MS = 200
EST_TICK_MS = 50
MAP_EVERY_N_TICKS = 4

# ключ в словаре MissionRunner.update -> индекс канала (порядок отправки сохранён)
MISSION_CHANNELS = ((ANGLE_IDX, "angle"), (ARM_IDX, "arm"), (THROTTLE_IDX, "throttle"), (ROLL_IDX, "roll"),
                    (PITCH_IDX, "pitch"), (YAW_IDX, "yaw"), (GRIPPER_IDX, "gripper"))


class NavigationController(QObject):
    position_updated = pyqtSignal(float, float, str)
    status_changed = pyqtSignal(str)
    running_changed = pyqtSignal(bool)

    def __init__(self, ws_client, rc, get_limits, parent=None):
        super().__init__(parent)
        self._ws, self._rc, self._get_limits = ws_client, rc, get_limits
        self._est = None
        self._est_last = time.monotonic()
        self._est_ticks = 0
        self._msp_yaw = None
        self._armed = None
        self._mission = None

        self._nav_timer = QTimer(self)
        self._nav_timer.setInterval(NAV_TICK_MS)
        self._nav_timer.timeout.connect(self._navigation_tick)
        self._est_timer = QTimer(self)
        self._est_timer.setInterval(EST_TICK_MS)
        self._est_timer.timeout.connect(self._estimator_tick)
        self._est_timer.start()

    # ---------- состояние ----------
    @property
    def is_running(self) -> bool:
        return self._mission is not None

    @property
    def has_start(self) -> bool:
        return self._est is not None

    def position(self):
        return None if self._est is None else (self._est.lat, self._est.lon)

    # ---------- данные MSP ----------
    def set_msp_yaw(self, yaw_deg):
        self._msp_yaw = yaw_deg
        if self._est is not None:
            self._est.set_msp_yaw(yaw_deg)

    def set_armed(self, armed):
        self._armed = armed

    # ---------- старт и положение ----------
    def set_start(self, lat: float, lon: float, heading_deg: float):
        lim = self._get_limits(silent=True)
        self._est = DeadReckoning(
            lat, lon, heading_deg, kd=lim["drag_kd"], angle_limit_deg=lim["max_tilt_deg"],
            deadband_us=lim["rc_deadband_us"], yaw_rate_dps=lim["yaw_rate_dps"], yaw_raw=self._msp_yaw)

    def reset_position(self):
        self.stop()
        self._est = None

    def _estimator_tick(self):
        now = time.monotonic()
        dt = now - self._est_last
        self._est_last = now
        if self._est is None:
            return
        lim = self._get_limits(silent=True)
        self._est.set_params(lim["drag_kd"], lim["max_tilt_deg"], lim["rc_deadband_us"], lim["yaw_rate_dps"])
        armed = self._armed if self._armed is not None else True
        airborne = armed and self._rc[THROTTLE_IDX] > lim["liftoff_throttle_us"]
        lat, lon, speed = self._est.update(self._rc[PITCH_IDX], self._rc[ROLL_IDX], self._rc[YAW_IDX], dt, airborne)
        self._est_ticks += 1
        if self._est_ticks % MAP_EVERY_N_TICKS == 0:
            state = "в воздухе" if airborne else "на земле"
            self.position_updated.emit(lat, lon, f"{speed:.1f} м/с, курс {self._est.psi:.0f}° ({state})")

    # ---------- маршрут ----------
    def start(self, route: list):
        lim = self._get_limits()
        self._mission = MissionRunner(
            list(route),
            dict(max_speed_mps=lim["max_speed_mps"], kd=lim["drag_kd"], angle_limit_deg=lim["max_tilt_deg"],
                 deadband_us=lim["rc_deadband_us"], arrival_radius_m=lim.get("arrival_radius_m", 3.0)))
        self._nav_timer.start()
        self.running_changed.emit(True)
        self.status_changed.emit(f"Навигация запущена (0/{len(route)})...")

    def stop(self):
        self._nav_timer.stop()
        mission, self._mission = self._mission, None
        self.running_changed.emit(False)
        self.status_changed.emit("Навигация остановлена")
        if self._ws.is_connected():
            updates = safe_updates(gripper=mission is not None,
                                   throttle_arm=mission is not None and mission.auto_arm)
            push_updates(self._rc, updates, self._rc.set, force=True)

    def _navigation_tick(self):
        if self._mission is None or self._est is None:
            return
        ch, finished = self._mission.update(self._est.lat, self._est.lon, self._est.psi, time.monotonic())
        for idx, key in MISSION_CHANNELS:
            value = ch.get(key)
            if value is not None and self._rc[idx] != int(value):
                self._rc.set(idx, int(value))
        if not finished:
            current, total = self._mission.progress()
            self.status_changed.emit(f"Навигация запущена ({current}/{total}), этап: {self._mission.phase}...")
        else:
            self.status_changed.emit("Маршрут завершён")
            self.stop()
