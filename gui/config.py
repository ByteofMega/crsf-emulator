"""
config.py — все константы и настройки внешнего вида GUI.

Раньше эти значения (имена каналов, диапазоны, цвета, QSS-стиль) были
перемешаны прямо в main-файле вместе с логикой окна. Вынесены отдельно,
чтобы:
  - менять оформление / диапазоны без риска задеть логику;
  - переиспользовать константы в других модулях (widgets, ws_client)
    без циклических импортов.
"""

CHANNEL_NAMES = ["ROLL", "PITCH", "THROTTLE", "YAW"] + [f"AUX{i}" for i in range(1, 13)]
NUM_CHANNELS = len(CHANNEL_NAMES)  # 16

MIN_US, MAX_US = 1000, 2000
DEFAULT_VALUES = [1500, 1500, 1000, 1500] + [1000] * 12  # ROLL,PITCH,THROTTLE,YAW,AUX1..12

DEFAULT_IP = "10.164.135.29"
DEFAULT_PORT = 81  # должен совпадать с WS_PORT в прошивке ESP32

# ---------- Цветовая схема: черный / зеленый / белый ----------
COLOR_BG = "#0A0A0A"
COLOR_PANEL = "#141414"
COLOR_GREEN = "#00E676"
COLOR_GREEN_DARK = "#00A854"
COLOR_WHITE = "#F5F5F5"
COLOR_BORDER = "#00E676"

STYLESHEET = f"""
QMainWindow {{
    background-color: {COLOR_BG};
}}

QWidget {{
    background-color: {COLOR_BG};
    color: {COLOR_WHITE};
    font-family: "Consolas", "Segoe UI", monospace;
    font-size: 13px;
}}

QLabel {{
    color: {COLOR_WHITE};
    background-color: transparent;
}}

QLabel#statusLabel {{
    color: {COLOR_GREEN};
    font-weight: bold;
}}

QLineEdit {{
    background-color: {COLOR_PANEL};
    color: {COLOR_GREEN};
    border: 1px solid {COLOR_BORDER};
    border-radius: 4px;
    padding: 3px 6px;
    selection-background-color: {COLOR_GREEN_DARK};
}}

QLineEdit:focus {{
    border: 1px solid {COLOR_WHITE};
}}

QPushButton {{
    background-color: {COLOR_PANEL};
    color: {COLOR_GREEN};
    border: 1px solid {COLOR_GREEN};
    border-radius: 4px;
    padding: 5px 14px;
    font-weight: bold;
}}

QPushButton:hover {{
    background-color: {COLOR_GREEN_DARK};
    color: {COLOR_BG};
}}

QPushButton:pressed {{
    background-color: {COLOR_GREEN};
    color: {COLOR_BG};
}}

QSpinBox {{
    background-color: {COLOR_PANEL};
    color: {COLOR_WHITE};
    border: 1px solid {COLOR_GREEN_DARK};
    border-radius: 4px;
    padding-right: 2px;
}}

QSpinBox::up-button, QSpinBox::down-button {{
    background-color: {COLOR_PANEL};
    border-left: 1px solid {COLOR_GREEN_DARK};
    width: 16px;
}}

QSpinBox::up-arrow {{
    border-bottom: 4px solid {COLOR_GREEN};
    width: 0px;
    height: 0px;
}}

QSpinBox::down-arrow {{
    border-top: 4px solid {COLOR_GREEN};
    width: 0px;
    height: 0px;
}}

QSlider::groove:horizontal {{
    background-color: {COLOR_PANEL};
    border: 1px solid {COLOR_GREEN_DARK};
    height: 6px;
    border-radius: 3px;
}}

QSlider::handle:horizontal {{
    background-color: {COLOR_GREEN};
    border: 1px solid {COLOR_WHITE};
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}}

QSlider::handle:horizontal:hover {{
    background-color: {COLOR_WHITE};
}}

QSlider::sub-page:horizontal {{
    background-color: {COLOR_GREEN_DARK};
    border-radius: 3px;
}}

QScrollArea {{
    border: 1px solid {COLOR_GREEN_DARK};
    background-color: {COLOR_BG};
}}

QScrollBar:vertical {{
    background-color: {COLOR_PANEL};
    width: 12px;
}}

QScrollBar::handle:vertical {{
    background-color: {COLOR_GREEN_DARK};
    border-radius: 4px;
    min-height: 20px;
}}

QScrollBar::handle:vertical:hover {{
    background-color: {COLOR_GREEN};
}}
"""
