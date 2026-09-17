"""
channel_widgets.py — визуальные компоненты, не знающие ничего о сети.

ChannelRow отвечает только за отображение одного канала (слайдер + spin-
box) и генерацию события "значение изменилось". Он ничего не знает
про WebSocket — это обязанность main.py, который подписывается на
сигнал value_changed и решает, что с этим делать (например, отправить
в сеть). Такое разделение позволяет тестировать/переиспользовать
виджет отдельно от сетевого кода.

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
    """Один ряд: имя канала + ползунок + поле точного значения."""

    value_changed = pyqtSignal(int, int)  # (index, value)

    def __init__(self, index: int, name: str, default_value: int, parent=None):
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
        self.spin.blockSignals(True)
        self.spin.setValue(value)
        self.spin.blockSignals(False)
        self.value_changed.emit(self.index, value)

    def _spin_changed(self, value: int):
        self.slider.blockSignals(True)
        self.slider.setValue(value)
        self.slider.blockSignals(False)
        self.value_changed.emit(self.index, value)

    def set_value_silent(self, value: int):
        """Обновить отображение, не порождая сигнал value_changed (нужно
        при получении состояния с ESP32, чтобы не создавать эхо-цикл)."""
        self.slider.blockSignals(True)
        self.spin.blockSignals(True)
        self.slider.setValue(value)
        self.spin.setValue(value)
        self.slider.blockSignals(False)
        self.spin.blockSignals(False)


class ChannelPanel(QScrollArea):
    """Прокручиваемая панель со всеми каналами."""

    value_changed = pyqtSignal(int, int)  # (index, value) — проброс от строк

    def __init__(self, parent=None):
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
        if len(values) != NUM_CHANNELS:
            return
        for row, value in zip(self.rows, values):
            row.set_value_silent(value)
