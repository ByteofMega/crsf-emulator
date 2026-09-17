"""
channel_widgets.py — визуальные компоненты, не знающие ничего о сети.

ChannelRow отвечает только за отображение одного канала (слайдер + spin-
box) и генерацию события "значение изменилось". Он ничего не знает про
WebSocket — это обязанность main.py, который подписывается на сигнал
value_changed и решает, что с этим делать (например, отправить в сеть).

ChannelPanel — контейнер, который создает все NUM_CHANNELS строк и
предоставляет удобный метод set_all_silent() для массового обновления
(например, когда пришел пакет {"channels": [...]} от ESP32).
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QLabel, QScrollArea, QSlider, QSpinBox, QVBoxLayout, QHBoxLayout, QWidget,
)

from config import CHANNEL_NAMES, DEFAULT_VALUES, MIN_US, MAX_US, NUM_CHANNELS


class ChannelRow(QWidget):
    """Один ряд: имя канала + ползунок + поле точного значения.

    Слайдер и spin-box синхронизированы между собой: изменение одного
    немедленно обновляет другой (без повторной отправки сигнала, чтобы
    не создавать цикл эхо-обновлений внутри самого виджета).
    """

    value_changed = pyqtSignal(int, int)
    """Сигнал: значение канала изменено пользователем.

    Аргументы:
        int: index — 0-based индекс канала.
        int: value — новое значение в микросекундах (1000..2000).
    """

    def __init__(self, index: int, name: str, default_value: int, parent=None):
        """Создать строку одного канала.

        Аргументы:
            index (int): 0-based индекс канала (используется в сигнале
                value_changed и для идентификации строки).
            name (str): отображаемое имя канала (например "ROLL", "AUX3").
            default_value (int): начальное значение слайдера/spin-box
                в микросекундах.
            parent (QWidget | None): родительский виджет Qt, по умолчанию None.
        """
        super().__init__(parent)
        self.index = index

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)

        self.label = QLabel(f"{name}:")
        self.label.setFixedWidth(90)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(MIN_US, MAX_US)
        self.slider.setValue(default_value)

        self.spin = QSpinBox()
        self.spin.setRange(MIN_US, MAX_US)
        self.spin.setValue(default_value)
        self.spin.setFixedWidth(100)
        self.spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.spin.setButtonSymbols(QSpinBox.ButtonSymbols.UpDownArrows)

        self.slider.valueChanged.connect(self._slider_changed)
        self.spin.valueChanged.connect(self._spin_changed)

        layout.addWidget(self.label)
        layout.addWidget(self.slider)
        layout.addWidget(self.spin)

    def _slider_changed(self, value: int):
        """Обработать перемещение слайдера пользователем.

        Аргументы:
            value (int): новое значение слайдера в микросекундах.

        Возвращает:
            None. Синхронизирует spin-box без повторного сигнала и
            испускает value_changed.
        """
        self.spin.blockSignals(True)
        self.spin.setValue(value)
        self.spin.blockSignals(False)
        self.value_changed.emit(self.index, value)

    def _spin_changed(self, value: int):
        """Обработать ручной ввод значения в spin-box.

        Аргументы:
            value (int): новое значение, введённое пользователем, в микросекундах.

        Возвращает:
            None. Синхронизирует слайдер без повторного сигнала и
            испускает value_changed.
        """
        self.slider.blockSignals(True)
        self.slider.setValue(value)
        self.slider.blockSignals(False)
        self.value_changed.emit(self.index, value)

    def set_value_silent(self, value: int):
        """Обновить отображение канала без генерации сигнала value_changed.

        Используется, когда новое значение пришло от ESP32 (а не от
        пользователя), чтобы не создавать эхо-цикл "получили от ESP32 ->
        отправили обратно на ESP32".

        Аргументы:
            value (int): значение в микросекундах для отображения.

        Возвращает:
            None.
        """
        self.slider.blockSignals(True)
        self.spin.blockSignals(True)
        self.slider.setValue(value)
        self.spin.setValue(value)
        self.slider.blockSignals(False)
        self.spin.blockSignals(False)


class ChannelPanel(QScrollArea):
    """Прокручиваемая панель со всеми каналами.

    Создаёт NUM_CHANNELS экземпляров ChannelRow и агрегирует их сигналы
    в один общий value_changed, чтобы внешний код (main.py) подписывался
    один раз, а не на каждую строку отдельно.
    """

    value_changed = pyqtSignal(int, int)
    """Сигнал: значение любого канала изменено пользователем (проброс от строк).

    Аргументы:
        int: index — 0-based индекс изменённого канала.
        int: value — новое значение в микросекундах.
    """

    def __init__(self, parent=None):
        """Создать панель и все строки каналов с значениями по умолчанию.

        Аргументы:
            parent (QWidget | None): родительский виджет Qt, по умолчанию None.
        """
        super().__init__(parent)
        self.setWidgetResizable(True)

        container = QWidget()
        layout = QVBoxLayout(container)

        self.rows: list[ChannelRow] = []
        for i, name in enumerate(CHANNEL_NAMES):
            row = ChannelRow(i, name, DEFAULT_VALUES[i])
            row.value_changed.connect(self.value_changed.emit)
            self.rows.append(row)
            layout.addWidget(row)

        self.setWidget(container)

    def set_all_silent(self, values: list[int]):
        """Обновить значения всех каналов сразу, не порождая сигналов.

        Используется при получении пакета {"channels": [...]} от ESP32.

        Аргументы:
            values (list[int]): список значений длиной ровно NUM_CHANNELS,
                в порядке ROLL, PITCH, THROTTLE, YAW, AUX1..AUX12.

        Возвращает:
            None. Если длина списка не совпадает с NUM_CHANNELS, вызов
            молча игнорируется (защита от рассинхронизации протокола).
        """
        if len(values) != NUM_CHANNELS:
            return
        for row, value in zip(self.rows, values):
            row.set_value_silent(value)
