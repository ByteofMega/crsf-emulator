"""route_planner.py - навигация по точкам БЕЗ GPS: положение приходит от DeadReckoning.

WaypointNavigator считает команды RC (ROLL, PITCH, YAW) по оценённому положению и курсу.
Максимальная скорость теперь реально ограничивает стик PITCH:
    s_max = atan(kd * v_max^2 / g) / angle_limit      (см. position_estimator.max_stick_for_speed)
"""

import math

from position_estimator import max_stick_for_speed, stick_to_us


def haversine_distance_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def initial_bearing_deg(lat1, lon1, lat2, lon2) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_lambda = math.radians(lon2 - lon1)
    x = math.sin(d_lambda) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(d_lambda)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _angle_diff_deg(target_deg, current_deg) -> float:
    return (target_deg - current_deg + 180) % 360 - 180


def total_path_distance_m(path: list) -> float:
    if len(path) < 2:
        return 0.0
    return sum(haversine_distance_m(a[0], a[1], b[0], b[1]) for a, b in zip(path[:-1], path[1:]))


def turn_angles_deg(path: list) -> list:
    """Углы разворота (0..180 град) в каждой промежуточной точке пути."""
    angles = []
    for i in range(1, len(path) - 1):
        b_in = initial_bearing_deg(path[i - 1][0], path[i - 1][1], path[i][0], path[i][1])
        b_out = initial_bearing_deg(path[i][0], path[i][1], path[i + 1][0], path[i + 1][1])
        angles.append(abs(_angle_diff_deg(b_out, b_in)))
    return angles


def estimate_energy_usage(path: list, max_speed_mps: float, current_a: float,
                          capacity_mah: float, yaw_rate_dps: float = 180.0) -> dict:
    """Оценка времени и расхода батареи: время на прямых + время на развороты."""
    if max_speed_mps <= 0:
        return None
    distance_m = total_path_distance_m(path)
    cruise_time_s = distance_m / max_speed_mps
    angles = turn_angles_deg(path)
    turn_time_s = sum(a / yaw_rate_dps for a in angles) if yaw_rate_dps > 0 else 0.0
    time_s = cruise_time_s + turn_time_s
    consumed_mah = current_a * 1000.0 * (time_s / 3600.0)
    percent = consumed_mah / capacity_mah * 100.0 if capacity_mah and capacity_mah > 0 else None
    return {
        "distance_m": distance_m,
        "cruise_time_s": cruise_time_s,
        "turn_time_s": turn_time_s,
        "turn_count": len(angles),
        "time_s": time_s,
        "consumed_mah": consumed_mah,
        "percent_of_capacity": percent,
    }


class WaypointNavigator:
    """Ведёт дрон через waypoints по оценённому положению (lat, lon, курс)."""

    def __init__(self, waypoints, max_speed_mps: float, kd: float, angle_limit_deg: float,
                 deadband_us: float = 0.0, arrival_radius_m: float = 3.0, brake_dist_m: float = 10.0):
        if not waypoints:
            raise ValueError("Список точек маршрута не может быть пустым")
        self.waypoints = list(waypoints)
        self.current_index = 0
        self.arrival_radius_m = arrival_radius_m
        self.brake_dist_m = max(1.0, brake_dist_m)
        self.deadband_us = deadband_us
        self.s_max = max_stick_for_speed(max_speed_mps, kd, angle_limit_deg)
        self.finished = False

    def update(self, lat, lon, heading_deg):
        """Возвращает (roll_us, pitch_us, yaw_us, finished)."""
        if self.finished:
            return 1500, 1500, 1500, True

        t_lat, t_lon = self.waypoints[self.current_index]
        distance = haversine_distance_m(lat, lon, t_lat, t_lon)
        if distance <= self.arrival_radius_m:
            self.current_index += 1
            if self.current_index >= len(self.waypoints):
                self.finished = True
                return 1500, 1500, 1500, True
            t_lat, t_lon = self.waypoints[self.current_index]
            distance = haversine_distance_m(lat, lon, t_lat, t_lon)

        bearing = initial_bearing_deg(lat, lon, t_lat, t_lon)
        heading_error = _angle_diff_deg(bearing, heading_deg)

        yaw_s = max(-1.0, min(1.0, heading_error / 45.0))
        align = max(0.0, math.cos(math.radians(heading_error)))
        speed_ratio = min(1.0, distance / self.brake_dist_m)
        pitch_s = self.s_max * align * speed_ratio

        return (1500,
                stick_to_us(pitch_s, self.deadband_us),
                stick_to_us(yaw_s, self.deadband_us),
                False)

    def progress(self) -> tuple:
        return min(self.current_index + 1, len(self.waypoints)), len(self.waypoints)
