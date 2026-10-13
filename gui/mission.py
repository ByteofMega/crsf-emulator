"""mission.py - миссия поверх WaypointNavigator: эмуляция пульта целиком.

Последовательность: ANGLE вкл -> АРМ -> ВЗЛЁТ (газ плавно до HOVER_THROTTLE_US) -> ПОЛЁТ по точкам
(с действиями захвата) -> ПОСАДКА (газ плавно вниз) -> ДИЗАРМ.

КАНАЛЫ (константы - в config.py; индекс с нуля: 4 = AUX1, 5 = AUX2, 6 = AUX3, 7 = AUX4):
  ARM_IDX = 5 (AUX2)   - режим ARM включается при 1825..2100 мкс -> шлём ARM_US = 2000.
  ANGLE_IDX = 7 (AUX4) - режим ANGLE включается при 1925..2100 мкс -> шлём ANGLE_US = 2000.
                         ANGLE включается ДО арма и держится до конца: навигатор рассчитан на ANGLE.
  GRIPPER_IDX = 4 (AUX1) - ПРЕДПОЛОЖЕНИЕ: проверьте канал и значения открыт/закрыт под свой захват.
  HOVER_THROTTLE_US    - газ в "полёте" (должен быть выше liftoff_throttle_us из настроек).
AUTO_ARM = False -> программа НЕ трогает арм, ANGLE и газ (как раньше: их ведёт оператор).

ФУНКЦИИ И КЛАССЫ ФАЙЛА
----------------------
class MissionRunner
  __init__(waypoints, nav_kwargs, actions=None, auto_arm=AUTO_ARM, ...)
      waypoints: [(lat, lon)]; actions: {индекс_точки_с_0: "gripper_cycle"}.
  update(lat, lon, heading_deg, now) -> (ch: dict, finished: bool)
      ch: roll, pitch, yaw, gripper (мкс) и, при auto_arm, throttle, arm и angle. None = канал не трогать.
  progress() -> (номер текущей точки, всего точек)
  phase - этап ("arm", "takeoff", "fly", "land", "done"); events - журнал [(время, текст)].
"""
from route_planner import WaypointNavigator, haversine_distance_m

from config import (ANGLE_OFF_US, ANGLE_RANGE, ANGLE_US, ARM_RANGE, ARM_US, DISARM_US, GRIPPER_CLOSED_US,
                    GRIPPER_OPEN_US, THROTTLE_MIN_US)

HOVER_THROTTLE_US = 1300
AUTO_ARM = True
DEFAULT_ACTIONS = {0: "gripper_cycle", 2: "gripper_cycle"}   # 1-я точка: снять старую лампочку, 3-я: поставить новую

assert ARM_RANGE[0] <= ARM_US <= ARM_RANGE[1] and not (ARM_RANGE[0] <= DISARM_US <= ARM_RANGE[1])
assert ANGLE_RANGE[0] <= ANGLE_US <= ANGLE_RANGE[1] and not (ANGLE_RANGE[0] <= ANGLE_OFF_US <= ANGLE_RANGE[1])


class MissionRunner:
    def __init__(self, waypoints, nav_kwargs, actions=None, auto_arm=AUTO_ARM,
                 angle_hold_s=0.6, arm_hold_s=1.5, takeoff_s=2.0, land_s=2.0,
                 settle_s=2.0, open_hold_s=1.5, closed_hold_s=1.5,
                 hover_throttle_us=HOVER_THROTTLE_US):
        self.waypoints = list(waypoints)
        self.nav = WaypointNavigator(self.waypoints, **nav_kwargs)
        self.arrival_radius_m = self.nav.arrival_radius_m
        self.actions = dict(DEFAULT_ACTIONS if actions is None else actions)
        self.auto_arm = auto_arm
        self.angle_hold_s, self.arm_hold_s = angle_hold_s, arm_hold_s
        self.takeoff_s, self.land_s = takeoff_s, land_s
        self.settle_s, self.open_hold_s, self.closed_hold_s = settle_s, open_hold_s, closed_hold_s
        self.hover_us = hover_throttle_us
        self.gripper_us = GRIPPER_CLOSED_US
        self.events = []
        self.phase = "arm" if auto_arm else "fly"
        self._phase_t0 = None
        self._arm_logged = False
        self._act_phase = None
        self._act_t0 = 0.0
        self._act_wp = 0
        self._done_actions = set()

    def _log(self, now, text):
        self.events.append((now, text))

    def _go(self, phase, now, text):
        self.phase, self._phase_t0 = phase, now
        self._log(now, text)

    def _channels(self, roll=1500, pitch=1500, yaw=1500, throttle=None, arm=None):
        ch = dict(roll=roll, pitch=pitch, yaw=yaw, gripper=self.gripper_us, throttle=None, arm=None, angle=None)
        if self.auto_arm:
            ch["throttle"], ch["arm"], ch["angle"] = throttle, arm, ANGLE_US
        return ch

    def update(self, lat, lon, heading_deg, now):
        if self._phase_t0 is None:
            self._phase_t0 = now
            self._log(now, "ANGLE включён" if self.auto_arm else "старт полёта (арм, ANGLE и газ вручную)")
        dt = now - self._phase_t0

        if self.phase == "arm":
            if dt < self.angle_hold_s:       # сначала ANGLE, арм - только после паузы
                return self._channels(throttle=THROTTLE_MIN_US, arm=DISARM_US), False
            if not self._arm_logged:
                self._arm_logged = True
                self._log(now, "АРМ")
            if dt >= self.angle_hold_s + self.arm_hold_s:
                self._go("takeoff", now, "ВЗЛЁТ (газ растёт)")
            return self._channels(throttle=THROTTLE_MIN_US, arm=ARM_US), False

        if self.phase == "takeoff":
            k = min(1.0, dt / self.takeoff_s)
            thr = int(THROTTLE_MIN_US + (self.hover_us - THROTTLE_MIN_US) * k)
            if dt >= self.takeoff_s:
                self._go("fly", now, "ПОЛЁТ по маршруту")
            return self._channels(throttle=thr, arm=ARM_US), False

        if self.phase == "land":
            k = min(1.0, dt / self.land_s)
            thr = int(self.hover_us - (self.hover_us - THROTTLE_MIN_US) * k)
            if dt >= self.land_s:
                self._go("done", now, "ДИЗАРМ")
                return self._channels(throttle=THROTTLE_MIN_US, arm=DISARM_US), True
            return self._channels(throttle=thr, arm=ARM_US), False

        if self.phase == "done":
            return self._channels(throttle=THROTTLE_MIN_US, arm=DISARM_US), True

        hover = self.hover_us
        idx = self.nav.current_index
        if self._act_phase is None and idx < len(self.waypoints) and idx in self.actions \
                and idx not in self._done_actions:
            t_lat, t_lon = self.waypoints[idx]
            if haversine_distance_m(lat, lon, t_lat, t_lon) <= self.arrival_radius_m:
                self._act_phase, self._act_t0, self._act_wp = "settle", now, idx
                self._log(now, f"точка {idx + 1}: прилёт, зависание")

        if self._act_phase is not None:
            d = now - self._act_t0
            if self._act_phase == "settle" and d >= self.settle_s:
                self.gripper_us = GRIPPER_OPEN_US
                self._log(now, f"точка {self._act_wp + 1}: ЗАХВАТ ОТКРЫТ")
                self._act_phase, self._act_t0 = "open", now
            elif self._act_phase == "open" and d >= self.open_hold_s:
                self.gripper_us = GRIPPER_CLOSED_US
                self._log(now, f"точка {self._act_wp + 1}: ЗАХВАТ ЗАКРЫТ")
                self._act_phase, self._act_t0 = "closed", now
            elif self._act_phase == "closed" and d >= self.closed_hold_s:
                self._done_actions.add(self._act_wp)
                self._act_phase = None
            if self._act_phase is not None:
                return self._channels(throttle=hover, arm=ARM_US), False

        roll, pitch, yaw, finished = self.nav.update(lat, lon, heading_deg)
        if finished:
            if self.auto_arm:
                self._go("land", now, "маршрут завершён, ПОСАДКА (газ вниз)")
                return self._channels(throttle=hover, arm=ARM_US), False
            self.phase = "done"
            self._log(now, "маршрут завершён")
            return self._channels(), True
        return self._channels(roll, pitch, yaw, hover, ARM_US), False

    def progress(self):
        return self.nav.progress()
