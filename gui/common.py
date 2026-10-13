"""common.py - общие вспомогательные функции без Qt (чтобы не дублировать их по файлам).

ФУНКЦИИ И КЛАССЫ ФАЙЛА
----------------------
parse_float(text: str)
    Число из поля ввода; допускает запятую вместо точки. При ошибке бросает ValueError.
safe_updates(sticks=True, gripper=True, throttle_arm=True)
    Безопасное состояние каналов в виде словаря «индекс канала -> мкс»: стики ROLL/PITCH/YAW по
    центру, захват закрыт, газ на минимум и дизарм. Флаги выбирают, какие группы включить.
push_updates(rc, updates: dict, set_rc, force=False)
    Отправляет каналы из словаря через set_rc(idx, us); без force - только те, что отличаются от rc.
"""
from config import (ARM_IDX, CENTER_US, DISARM_US, GRIPPER_CLOSED_US, GRIPPER_IDX, PITCH_IDX, ROLL_IDX,
                    THROTTLE_IDX, THROTTLE_MIN_US, YAW_IDX)


def parse_float(text: str) -> float:
    return float(text.replace(",", "."))


def safe_updates(sticks: bool = True, gripper: bool = True, throttle_arm: bool = True) -> dict:
    out = {}
    if sticks:
        out.update({ROLL_IDX: CENTER_US, PITCH_IDX: CENTER_US, YAW_IDX: CENTER_US})
    if gripper:
        out[GRIPPER_IDX] = GRIPPER_CLOSED_US
    if throttle_arm:
        out[THROTTLE_IDX] = THROTTLE_MIN_US
        out[ARM_IDX] = DISARM_US
    return out


def push_updates(rc, updates: dict, set_rc, force: bool = False):
    for idx, value in updates.items():
        if force or rc[idx] != value:
            set_rc(idx, int(value))
