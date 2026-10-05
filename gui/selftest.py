"""selftest.py - проверка основных функций БЕЗ железа, GUI и pytest.
Положить в папку gui/ рядом с main.py и запустить:   python selftest.py
"""
import math
import os
import struct
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import msp_codes as mc
import position_estimator as pe
import route_planner as rp

R = pe.R_EARTH
LAT0, LON0 = 55.78, 49.12


def ll(n, e):
    return LAT0 + math.degrees(n / R), LON0 + math.degrees(e / (R * math.cos(math.radians(LAT0))))


def check_decode_attitude():
    d = mc.decode(108, struct.pack("<hhh", 25, -130, 271))
    assert d["roll_deg"] == 2.5 and d["pitch_deg"] == -13.0 and d["yaw_deg"] == 271.0


def check_decode_version():
    assert mc.decode(3, bytes([4, 4, 2]))["version"] == "4.4.2"


def check_decode_analog_and_status():
    a = mc.decode(110, struct.pack("<BHHhH", 168, 120, 800, 1550, 1670))
    assert a["current_a"] == 15.5 and a["vbat_v"] == 16.7
    s = mc.decode(101, struct.pack("<HHHIB", 312, 0, 0x27, 1, 0))
    assert s["armed"] and "BARO" in s["sensors"] and "MAG" in s["sensors"]


def check_decode_short_payload():
    assert mc.decode(108, b"\x01\x02") is None


def check_dangerous_codes_blocked():
    for code in (68, 205, 206, 208, 250):
        assert code not in mc.READ_CODES
    for code in (3, 101, 108, 110):
        assert code in mc.READ_CODES


def check_stick_roundtrip_with_deadband():
    for s in (-1.0, -0.4, 0.0, 0.25, 1.0):
        assert abs(pe.stick_from_us(pe.stick_to_us(s, 20), 20) - s) < 0.01


def check_kd_calibration():
    kd = pe.calibrate_kd(0.5, 30, 20.0, 4.0)  # 20 м за 4 с = 5 м/с
    assert abs(pe.steady_speed(0.5, kd, 30) - 5.0) < 1e-6


def check_estimator_straight_north():
    est = pe.DeadReckoning(LAT0, LON0, 0.0, kd=0.05, angle_limit_deg=30)
    for _ in range(400):  # 20 с
        est.update(pe.stick_to_us(0.5), 1500, 1500, 0.05, True)
    v_ss = pe.steady_speed(0.5, 0.05, 30)
    dist = rp.haversine_distance_m(LAT0, LON0, est.lat, est.lon)
    assert abs(est.speed - v_ss) < 0.02 * v_ss
    assert 0.8 * v_ss * 20 < dist < v_ss * 20
    assert abs(est.lon - LON0) < 1e-9


def check_estimator_stays_put_on_ground():
    est = pe.DeadReckoning(LAT0, LON0, 0.0, kd=0.05, angle_limit_deg=30)
    for _ in range(100):
        est.update(1800, 1500, 1500, 0.05, False)
    assert est.lat == LAT0 and est.speed == 0.0


def check_estimator_heading_east():
    est = pe.DeadReckoning(LAT0, LON0, 90.0, kd=0.05, angle_limit_deg=30)
    for _ in range(100):
        est.update(pe.stick_to_us(0.5), 1500, 1500, 0.05, True)
    assert est.lon > LON0 and abs(est.lat - LAT0) < 1e-7


def check_msp_yaw_offset():
    est = pe.DeadReckoning(LAT0, LON0, 90.0, kd=0.05, angle_limit_deg=30, yaw_raw=10.0)
    est.update(1500, 1500, 1500, 0.05, True)
    assert abs(est.psi - 90.0) < 1e-6
    est.set_msp_yaw(40.0)
    est.update(1500, 1500, 1500, 0.05, True)
    assert abs(est.psi - 120.0) < 1e-6


def _fly(vmax, route, kd=0.05, lim=30, db=0):
    est = pe.DeadReckoning(LAT0, LON0, 0.0, kd, lim, db, 180.0)
    nav = rp.WaypointNavigator([ll(*p) for p in route], vmax, kd, lim, db, 3.0, 10.0)
    t, k, peak, fin = 0.0, 0, 0.0, False
    r = p = y = 1500
    while t < 600 and not fin:
        if k % 4 == 0:
            r, p, y, fin = nav.update(est.lat, est.lon, est.psi)
        est.update(p, r, y, 0.05, True)
        peak = max(peak, est.speed)
        t += 0.05
        k += 1
    return fin, peak


def check_navigator_respects_speed_limit():
    for vmax in (2.0, 5.0):
        fin, peak = _fly(vmax, [(0, 100), (80, 100)])
        assert fin and peak <= vmax * 1.02, (vmax, fin, peak)


def check_navigator_with_deadband():
    fin, peak = _fly(3.0, [(50, 0), (50, 50)], db=20)
    assert fin and peak <= 3.0 * 1.02


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("check_") and callable(fn):
            try:
                fn()
                print(f"OK    {name}")
            except Exception:
                failed += 1
                print(f"FAIL  {name}")
                traceback.print_exc()
    print("\nВСЁ ПРОШЛО" if not failed else f"\nПровалено: {failed}")
    sys.exit(1 if failed else 0)
