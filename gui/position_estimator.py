"""position_estimator.py - оценка положения дрона БЕЗ GPS (dead reckoning по стикам и MSP).

Модель (отдельно для продольной и поперечной осей):
    theta = stick * angle_limit                 # наклон в ANGLE-режиме
    dv/dt = g * tan(theta) - kd * v * |v|        # тяга по наклону минус квадратичное сопротивление
    N += (vx*cos(psi) - vy*sin(psi)) * dt
    E += (vx*sin(psi) + vy*cos(psi)) * dt
Курс psi берётся из MSP_ATTITUDE (со смещением, заданным оператором при установке старта),
а если MSP-курс давно не приходил - интегрируется по стику YAW.
Ошибка растёт со временем: это оценка, а не измерение.

ФУНКЦИИ И КЛАССЫ ФАЙЛА
----------------------
stick_from_us(us, deadband_us=0.0)
    Значение стика в микросекундах -> доля хода от -1 до 1 с учётом дедбэнда.
stick_to_us(s, deadband_us=0.0)
    Доля хода стика -> значение в микросекундах сразу за дедбэндом (обратное преобразование).
max_stick_for_speed(max_speed_mps, kd, angle_limit_deg)
    Максимальная доля хода стика PITCH, при которой установившаяся скорость не превышает
    заданную: atan(kd*v^2/g)/угол_наклона.
steady_speed(stick, kd, angle_limit_deg)
    Установившаяся скорость при постоянном стике: sqrt(g*tan(theta)/kd).
calibrate_kd(stick_frac, angle_limit_deg, distance_m, time_s)
    Коэффициент сопротивления kd по замеру: стик, предел угла, дистанция и время пролёта на
    установившейся скорости.
class DeadReckoning
    Оценка положения дрона без GPS: интегрирует скорость, полученную из наклона (стики), с
    учётом сопротивления; курс берётся из MSP со смещением, заданным оператором.
  DeadReckoning.__init__(self, lat, lon, heading_deg, kd, angle_limit_deg, deadband_us=0.0, yaw_rate_dps=180.0, yaw_raw=None)
    Задаёт начальные координаты, курс и параметры модели; если известен MSP-курс, сразу
    вычисляет смещение.
  DeadReckoning.set_params(self, kd, angle_limit_deg, deadband_us, yaw_rate_dps)
    Обновляет kd, предел угла, дедбэнд и скорость разворота на лету.
  DeadReckoning.set_msp_yaw(self, yaw_deg)
    Принимает курс из MSP_ATTITUDE и при первом вызове запоминает смещение между нужным курсом и
    курсом гироскопа FC.
  DeadReckoning.speed(self)
    Модуль текущей оценённой скорости, м/с.
  DeadReckoning.update(self, pitch_us, roll_us, yaw_us, dt, airborne=True)
    Один шаг модели: скорость по осям из наклона и сопротивления, курс из MSP (или по стику
    YAW), смещение положения; возвращает (широта, долгота, скорость). На земле скорость
    обнуляется.
"""

import math
import time

G = 9.81
R_EARTH = 6371000.0


def stick_from_us(us, deadband_us=0.0) -> float:
    """Стик в мкс -> доля хода -1..1 с учётом дедбэнда."""
    d = us - 1500.0
    if abs(d) <= deadband_us:
        return 0.0
    return math.copysign(min(1.0, (abs(d) - deadband_us) / (500.0 - deadband_us)), d)


def stick_to_us(s, deadband_us=0.0) -> int:
    """Обратное преобразование: доля хода -> мкс (сразу за дедбэндом)."""
    if abs(s) < 1e-6:
        return 1500
    s = max(-1.0, min(1.0, s))
    return int(round(1500 + math.copysign(deadband_us + abs(s) * (500.0 - deadband_us), s)))


def max_stick_for_speed(max_speed_mps, kd, angle_limit_deg) -> float:
    """Максимальная доля хода стика PITCH, при которой установившаяся скорость <= max_speed_mps."""
    if kd <= 0 or angle_limit_deg <= 0:
        return 1.0
    theta = math.atan(kd * max_speed_mps ** 2 / G)
    return max(0.0, min(1.0, theta / math.radians(angle_limit_deg)))


def steady_speed(stick, kd, angle_limit_deg) -> float:
    """Установившаяся скорость (м/с) при постоянном стике."""
    theta = abs(stick) * math.radians(angle_limit_deg)
    if kd <= 0:
        return 0.0
    return math.sqrt(G * math.tan(theta) / kd)


def calibrate_kd(stick_frac, angle_limit_deg, distance_m, time_s) -> float:
    """kd по замеру: постоянный стик, пролёт distance_m за time_s (после разгона)."""
    v = distance_m / time_s
    return G * math.tan(math.radians(abs(stick_frac) * angle_limit_deg)) / (v * v)


class DeadReckoning:
    YAW_STALE_S = 1.0

    def __init__(self, lat, lon, heading_deg, kd, angle_limit_deg, deadband_us=0.0,
                 yaw_rate_dps=180.0, yaw_raw=None):
        self.lat, self.lon = lat, lon
        self.psi = heading_deg % 360.0
        self.kd, self.lim, self.db, self.yaw_rate = kd, angle_limit_deg, deadband_us, yaw_rate_dps
        self.vx = 0.0  # вперёд, м/с
        self.vy = 0.0  # вправо, м/с
        self._yaw_raw = None
        self._yaw_t = 0.0
        self._offset = None
        if yaw_raw is not None:
            self.set_msp_yaw(yaw_raw)

    def set_params(self, kd, angle_limit_deg, deadband_us, yaw_rate_dps):
        self.kd, self.lim, self.db, self.yaw_rate = kd, angle_limit_deg, deadband_us, yaw_rate_dps

    def set_msp_yaw(self, yaw_deg):
        self._yaw_raw = yaw_deg % 360.0
        self._yaw_t = time.monotonic()
        if self._offset is None:
            self._offset = self.psi - self._yaw_raw

    @property
    def speed(self) -> float:
        return math.hypot(self.vx, self.vy)

    def update(self, pitch_us, roll_us, yaw_us, dt, airborne=True):
        """Один шаг. Возвращает (lat, lon, speed_mps)."""
        dt = max(0.0, min(dt, 0.25))
        if airborne:
            tp = math.radians(stick_from_us(pitch_us, self.db) * self.lim)
            tr = math.radians(stick_from_us(roll_us, self.db) * self.lim)
            self.vx += (G * math.tan(tp) - self.kd * self.vx * abs(self.vx)) * dt
            self.vy += (G * math.tan(tr) - self.kd * self.vy * abs(self.vy)) * dt
        else:
            self.vx = self.vy = 0.0

        fresh = self._yaw_raw is not None and (time.monotonic() - self._yaw_t) < self.YAW_STALE_S
        if fresh and self._offset is not None:
            self.psi = (self._yaw_raw + self._offset) % 360.0
        else:
            self.psi = (self.psi + stick_from_us(yaw_us, self.db) * self.yaw_rate * dt) % 360.0

        p = math.radians(self.psi)
        north = (self.vx * math.cos(p) - self.vy * math.sin(p)) * dt
        east = (self.vx * math.sin(p) + self.vy * math.cos(p)) * dt
        self.lat += math.degrees(north / R_EARTH)
        self.lon += math.degrees(east / (R_EARTH * math.cos(math.radians(self.lat))))
        return self.lat, self.lon, self.speed
