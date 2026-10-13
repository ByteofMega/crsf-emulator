"""rc_state.py - текущие значения 16 RC-каналов (единый источник правды для GUI).

ФУНКЦИИ И КЛАССЫ ФАЙЛА
----------------------
class RcState
    Хранит последние значения каналов и отправляет изменения на ESP32.
  RcState.__init__(self, ws_client)
    Начальные значения - DEFAULT_VALUES из config.py.
  RcState.set(self, idx: int, value: int)
    Запоминает значение канала и отправляет его на ESP32.
  RcState.replace(self, values: list)
    Заменяет все значения тем, что прислала ESP32 (без отправки обратно).
  RcState.__getitem__(self, idx)
    Значение канала по индексу; весь список - в атрибуте values.
"""
from config import DEFAULT_VALUES


class RcState:
    def __init__(self, ws_client):
        self._ws = ws_client
        self.values = list(DEFAULT_VALUES)

    def set(self, idx: int, value: int):
        self.values[idx] = value
        self._ws.send_channel(idx, value)

    def replace(self, values: list):
        self.values = list(values)

    def __getitem__(self, idx):
        return self.values[idx]
