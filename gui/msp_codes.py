"""msp_codes.py - таблица разрешённых MSP-команд (только чтение) и декодеры ответов Betaflight.

Раскладка байтов взята из msp_protocol.h / msp.c Betaflight; для каждой команды в панели
"MSP" всегда показывается и сырой hex - если декодер не совпал с вашей версией прошивки,
сверьте по hex. Опасные SET-команды (MSP_SET_REBOOT=68, калибровки, сброс настроек, EEPROM)
в список НЕ входят и дополнительно блокируются в прошивке ESP32.
"""

import struct

READ_CODES = {
    1: "API_VERSION",
    2: "FC_VARIANT",
    3: "FC_VERSION",
    4: "BOARD_INFO",
    5: "BUILD_INFO",
    10: "NAME",
    101: "STATUS",
    102: "RAW_IMU",
    104: "MOTOR",
    105: "RC",
    108: "ATTITUDE",
    109: "ALTITUDE",
    110: "ANALOG",
    111: "RC_TUNING",
    112: "PID",
    130: "BATTERY_STATE",
    150: "STATUS_EX",
}

SENSOR_BITS = {0x01: "ACC", 0x02: "BARO", 0x04: "MAG", 0x08: "GPS", 0x10: "SONAR", 0x20: "GYRO"}
ACC_1G = 512.0  # MSP_RAW_IMU: 512 LSB = 1 g (проверить: на столе |acc| ~ 1.0)


def _ascii(data: bytes) -> str:
    return data.decode("ascii", errors="replace").strip("\x00 ")


def _attitude(d):
    r, p, y = struct.unpack_from("<hhh", d)
    return {"roll_deg": r / 10.0, "pitch_deg": p / 10.0, "yaw_deg": float(y)}


def _raw_imu(d):
    v = struct.unpack_from("<9h", d)
    return {
        "acc_x_g": v[0] / ACC_1G, "acc_y_g": v[1] / ACC_1G, "acc_z_g": v[2] / ACC_1G,
        "gyro_x": v[3], "gyro_y": v[4], "gyro_z": v[5],
        "mag_x": v[6], "mag_y": v[7], "mag_z": v[8],
    }


def _analog(d):
    vbat, mah, rssi, amp = struct.unpack_from("<BHHh", d)
    out = {"vbat_v": vbat / 10.0, "mah_drawn": mah, "rssi": rssi, "current_a": amp / 100.0}
    if len(d) >= 9:
        out["vbat_v"] = struct.unpack_from("<H", d, 7)[0] / 100.0
    return out


def _status(d):
    cycle, i2c, sensors, flags, profile = struct.unpack_from("<HHHIB", d)
    out = {
        "cycle_us": cycle, "i2c_errors": i2c,
        "sensors": ",".join(n for b, n in SENSOR_BITS.items() if sensors & b) or "-",
        "mode_flags": flags,
        "armed": bool(flags & 1),  # бит 0 = ARM (проверить: при арме значение меняется)
        "profile": profile,
    }
    if len(d) >= 13:
        out["cpu_load"] = struct.unpack_from("<H", d, 11)[0]
    return out


def _u16_list(d):
    n = len(d) // 2
    return {"values": list(struct.unpack_from(f"<{n}H", d))}


def _altitude(d):
    alt, vario = struct.unpack_from("<ih", d)
    return {"alt_m": alt / 100.0, "vario_mps": vario / 100.0}


def _battery_state(d):
    cells, cap, vleg, mah, amp, state, vbat = struct.unpack_from("<BHBHhBH", d)
    return {"cells": cells, "capacity_mah": cap, "mah_drawn": mah,
            "current_a": amp / 100.0, "state": state, "vbat_v": vbat / 100.0}


_DECODERS = {
    1: lambda d: {"protocol": d[0], "api": f"{d[1]}.{d[2]}"},
    2: lambda d: {"variant": _ascii(d[:4])},
    3: lambda d: {"version": f"{d[0]}.{d[1]}.{d[2]}"},
    4: lambda d: {"board": _ascii(d[:4])},
    5: lambda d: {"build": _ascii(d)},
    10: lambda d: {"name": _ascii(d)},
    101: _status,
    102: _raw_imu,
    104: _u16_list,
    105: _u16_list,
    108: _attitude,
    109: _altitude,
    110: _analog,
    130: _battery_state,
    150: _status,
}


def decode(cmd: int, data: bytes):
    """dict с расшифровкой или None (нет декодера / длина не подошла)."""
    fn = _DECODERS.get(cmd)
    if fn is None:
        return None
    try:
        return fn(data)
    except (struct.error, IndexError):
        return None


def format_decoded(d: dict) -> str:
    parts = []
    for k, v in d.items():
        parts.append(f"{k}={v:.3f}" if isinstance(v, float) else f"{k}={v}")
    return ", ".join(parts)
