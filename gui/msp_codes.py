"""msp_codes.py - таблица разрешённых MSP-команд (только чтение) и декодеры ответов Betaflight.

Раскладка байтов взята из msp_protocol.h / msp.c Betaflight; для каждой команды в панели
"MSP" всегда показывается и сырой hex - если декодер не совпал с вашей версией прошивки,
сверьте по hex. Опасные SET-команды (MSP_SET_REBOOT=68, калибровки, сброс настроек, EEPROM)
в список НЕ входят и дополнительно блокируются в прошивке ESP32.

ФУНКЦИИ И КЛАССЫ ФАЙЛА
----------------------
_ascii(data: bytes)
    Преобразует байты в строку ASCII без нулевых байт и пробелов по краям.
_attitude(d)
    Декодирует MSP_ATTITUDE (108): крен и тангаж в десятых долях градуса, курс в градусах.
_raw_imu(d)
    Декодирует MSP_RAW_IMU (102): ускорения в g (512 LSB = 1 g), гироскоп и магнитометр в сырых
    единицах.
_analog(d)
    Декодирует MSP_ANALOG (110): напряжение батареи, потраченные мАч, RSSI и ток.
_status(d)
    Декодирует MSP_STATUS (101) и STATUS_EX (150): время цикла, список датчиков, флаги режимов,
    признак арма (бит 0), профиль, загрузка CPU.
_u16_list(d)
    Декодирует массив 16-битных значений (каналы RC, значения моторов).
_altitude(d)
    Декодирует MSP_ALTITUDE (109): высота и вертикальная скорость.
_battery_state(d)
    Декодирует MSP_BATTERY_STATE (130): число ячеек, ёмкость, потраченные мАч, ток, напряжение.
decode(cmd: int, data: bytes)
    Расшифровывает ответ MSP по коду команды; возвращает словарь или None, если декодера нет или
    длина данных не подошла.
format_decoded(d: dict)
    Превращает словарь расшифровки в одну строку «ключ=значение, ...» для таблицы.
Константы и данные файла
    MSP_* - именованные коды команд (используйте их вместо чисел); READ_CODES - код -> имя разрешённых команд чтения; SENSOR_BITS - биты датчиков в STATUS;
    ACC_1G - масштаб акселерометра; _DECODERS - таблица код -> функция расшифровки.
"""

import struct

MSP_API_VERSION = 1
MSP_FC_VARIANT = 2
MSP_FC_VERSION = 3
MSP_BOARD_INFO = 4
MSP_BUILD_INFO = 5
MSP_NAME = 10
MSP_STATUS = 101
MSP_RAW_IMU = 102
MSP_MOTOR = 104
MSP_RC = 105
MSP_ATTITUDE = 108
MSP_ALTITUDE = 109
MSP_ANALOG = 110
MSP_RC_TUNING = 111
MSP_PID = 112
MSP_BATTERY_STATE = 130
MSP_STATUS_EX = 150

READ_CODES = {
    MSP_API_VERSION: "API_VERSION",
    MSP_FC_VARIANT: "FC_VARIANT",
    MSP_FC_VERSION: "FC_VERSION",
    MSP_BOARD_INFO: "BOARD_INFO",
    MSP_BUILD_INFO: "BUILD_INFO",
    MSP_NAME: "NAME",
    MSP_STATUS: "STATUS",
    MSP_RAW_IMU: "RAW_IMU",
    MSP_MOTOR: "MOTOR",
    MSP_RC: "RC",
    MSP_ATTITUDE: "ATTITUDE",
    MSP_ALTITUDE: "ALTITUDE",
    MSP_ANALOG: "ANALOG",
    MSP_RC_TUNING: "RC_TUNING",
    MSP_PID: "PID",
    MSP_BATTERY_STATE: "BATTERY_STATE",
    MSP_STATUS_EX: "STATUS_EX",
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
    MSP_API_VERSION: lambda d: {"protocol": d[0], "api": f"{d[1]}.{d[2]}"},
    MSP_FC_VARIANT: lambda d: {"variant": _ascii(d[:4])},
    MSP_FC_VERSION: lambda d: {"version": f"{d[0]}.{d[1]}.{d[2]}"},
    MSP_BOARD_INFO: lambda d: {"board": _ascii(d[:4])},
    MSP_BUILD_INFO: lambda d: {"build": _ascii(d)},
    MSP_NAME: lambda d: {"name": _ascii(d)},
    MSP_STATUS: _status,
    MSP_RAW_IMU: _raw_imu,
    MSP_MOTOR: _u16_list,
    MSP_RC: _u16_list,
    MSP_ATTITUDE: _attitude,
    MSP_ALTITUDE: _altitude,
    MSP_ANALOG: _analog,
    MSP_BATTERY_STATE: _battery_state,
    MSP_STATUS_EX: _status,
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
