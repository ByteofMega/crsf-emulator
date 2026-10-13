"""config.py — константы и темы оформления GUI (тёмная и светлая).

ФУНКЦИИ И КЛАССЫ ФАЙЛА
----------------------
Константы и данные файла
    CHANNEL_NAMES, NUM_CHANNELS - 16 имён каналов и их число; MIN_US, MAX_US, DEFAULT_VALUES -
    границы и значения каналов по умолчанию; DEFAULT_IP, DEFAULT_PORT - адрес ESP32 по умолчанию.
    THEMES - палитры "dark" и "light"; DEFAULT_THEME - тема по умолчанию.
    *_IDX - индексы каналов с нуля (ROLL, PITCH, THROTTLE, YAW, ARM, ANGLE, GRIPPER); *_US - значения в мкс
    (центр, газ минимум, арм/дизарм, ANGLE, захват). ЕДИНСТВЕННОЕ место, где задаются номера и значения каналов.
    COLOR_* - цвета тёмной темы (оставлены для совместимости со старым кодом).
build_stylesheet(theme: str)
    Возвращает QSS всего приложения для темы "dark" или "light".
"""
from string import Template

CHANNEL_NAMES = ["ROLL", "PITCH", "THROTTLE", "YAW"] + [f"AUX{i}" for i in range(1, 13)]
NUM_CHANNELS = len(CHANNEL_NAMES)

MIN_US, MAX_US = 1000, 2000
DEFAULT_VALUES = [1500, 1500, 1000, 1500] + [1000] * 12

# ---------- Индексы каналов (с нуля) и значения в мкс ----------
ROLL_IDX, PITCH_IDX, THROTTLE_IDX, YAW_IDX = (CHANNEL_NAMES.index(n) for n in ("ROLL", "PITCH", "THROTTLE", "YAW"))
CENTER_US = 1500
THROTTLE_MIN_US = 1000

# Индекс с нуля: 4 = AUX1, 5 = AUX2, 6 = AUX3, 7 = AUX4
ARM_IDX = 5                              # AUX2: режим ARM включается при 1825..2100 мкс
ARM_RANGE = (1825, 2100)
ARM_US, DISARM_US = 2000, 1000
ANGLE_IDX = 7                            # AUX4: режим ANGLE включается при 1925..2100 мкс
ANGLE_RANGE = (1925, 2100)
ANGLE_US, ANGLE_OFF_US = 2000, 1000
GRIPPER_IDX = 4                          # AUX1 - ПРЕДПОЛОЖЕНИЕ: проверьте канал и значения под свой захват
GRIPPER_OPEN_US, GRIPPER_CLOSED_US = 2000, 1000

DEFAULT_IP = "10.164.135.29"
DEFAULT_PORT = 81

DEFAULT_THEME = "dark"

# Палитра: графит с синим подтоном + бирюзовый акцент (тёмная); холодная бумага + глубокий бирюзовый (светлая).
THEMES = {
    "dark": dict(
        bg="#0E1116", surface="#161A22", surface2="#1D222C", border="#2B3240", text="#E6EAF0",
        muted="#8A94A6", accent="#2DD4BF", accent_hover="#5EEAD4", accent_text="#06201C",
        danger="#F87171", danger_text="#2A0A0A", disabled="#4A5262",
        console_bg="#090B0F", console_fg="#5EEAD4", select="#134E4A", scroll="#323A4A"),
    "light": dict(
        bg="#F3F5F8", surface="#FFFFFF", surface2="#EDF0F4", border="#D3D9E2", text="#1A202C",
        muted="#5B6678", accent="#0F766E", accent_hover="#0D9488", accent_text="#FFFFFF",
        danger="#DC2626", danger_text="#FFFFFF", disabled="#A3ACBA",
        console_bg="#F8FAFC", console_fg="#0B5F59", select="#B8EDE6", scroll="#C3CAD6"),
}

# Старые имена (тёмная тема), чтобы не ломать импорты в других файлах
COLOR_BG = THEMES["dark"]["bg"]
COLOR_PANEL = THEMES["dark"]["surface"]
COLOR_GREEN = THEMES["dark"]["accent"]
COLOR_GREEN_DARK = THEMES["dark"]["select"]
COLOR_WHITE = THEMES["dark"]["text"]
COLOR_BORDER = THEMES["dark"]["border"]

_QSS = Template("""
QMainWindow, QDialog { background-color: $bg; }
QWidget { background-color: $bg; color: $text; font-family: "Segoe UI","Inter","Roboto","Helvetica Neue",sans-serif; font-size: 13px; }
QToolTip { background-color: $surface2; color: $text; border: 1px solid $border; padding: 4px 6px; }

QLabel, QCheckBox { background-color: transparent; }
QLabel#statusLabel { color: $accent; font-weight: 600; padding: 2px 0; }

QGroupBox { background-color: $surface; border: 1px solid $border; border-radius: 10px; margin-top: 16px; padding: 14px 12px 10px 12px; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; left: 14px; padding: 0 6px; color: $accent; font-weight: 600; background-color: $bg; }

QLineEdit, QPlainTextEdit, QSpinBox { background-color: $surface2; color: $text; border: 1px solid $border; border-radius: 6px; padding: 4px 8px; selection-background-color: $select; selection-color: $text; }
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus { border: 1px solid $accent; }
QLineEdit:disabled, QSpinBox:disabled { color: $disabled; }
QPlainTextEdit#console { background-color: $console_bg; color: $console_fg; border: 1px solid $border; border-radius: 8px; padding: 8px; font-family: "Cascadia Mono","Consolas","DejaVu Sans Mono",monospace; }

QPushButton { background-color: $surface2; color: $text; border: 1px solid $border; border-radius: 6px; padding: 6px 16px; font-weight: 600; }
QPushButton:hover { border: 1px solid $accent; color: $accent; }
QPushButton:pressed { background-color: $accent; color: $accent_text; border: 1px solid $accent; }
QPushButton#primary { background-color: $accent; color: $accent_text; border: 1px solid $accent; }
QPushButton#primary:hover { background-color: $accent_hover; border: 1px solid $accent_hover; color: $accent_text; }
QPushButton#danger { background-color: transparent; color: $danger; border: 1px solid $danger; }
QPushButton#danger:hover { background-color: $danger; color: $danger_text; }
QPushButton#themeBtn { background-color: transparent; padding: 6px 12px; font-weight: 500; }
QPushButton:disabled { background-color: $surface; color: $disabled; border: 1px solid $border; }

QCheckBox { spacing: 8px; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid $border; border-radius: 4px; background-color: $surface2; }
QCheckBox::indicator:hover { border: 1px solid $accent; }
QCheckBox::indicator:checked { background-color: $accent; border: 1px solid $accent; }

QSpinBox { padding-right: 2px; }
QSpinBox::up-button, QSpinBox::down-button { background-color: transparent; border: none; width: 18px; }
QSpinBox::up-arrow { border-left: 4px solid transparent; border-right: 4px solid transparent; border-bottom: 5px solid $accent; width: 0px; height: 0px; }
QSpinBox::down-arrow { border-left: 4px solid transparent; border-right: 4px solid transparent; border-top: 5px solid $accent; width: 0px; height: 0px; }

QSlider::groove:horizontal { background-color: $surface2; border: 1px solid $border; height: 6px; border-radius: 3px; }
QSlider::sub-page:horizontal { background-color: $accent; border-radius: 3px; }
QSlider::handle:horizontal { background-color: $surface; border: 2px solid $accent; width: 14px; height: 14px; margin: -6px 0; border-radius: 8px; }
QSlider::handle:horizontal:hover { background-color: $accent; }

QTabWidget::pane { border: 1px solid $border; border-radius: 10px; background-color: $bg; top: -1px; }
QTabBar { background-color: transparent; }
QTabBar::tab { background-color: transparent; color: $muted; padding: 9px 18px; border: none; border-bottom: 2px solid transparent; font-weight: 600; }
QTabBar::tab:hover { color: $text; }
QTabBar::tab:selected { color: $accent; border-bottom: 2px solid $accent; }

QScrollArea { border: none; background-color: $bg; }
QScrollBar:vertical { background-color: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background-color: $scroll; border-radius: 4px; min-height: 28px; }
QScrollBar::handle:vertical:hover { background-color: $accent; }
QScrollBar:horizontal { background-color: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background-color: $scroll; border-radius: 4px; min-width: 28px; }
QScrollBar::handle:horizontal:hover { background-color: $accent; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0px; height: 0px; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }

QTableWidget { background-color: $surface; alternate-background-color: $surface2; gridline-color: $border; border: 1px solid $border; border-radius: 8px; selection-background-color: $select; selection-color: $text; }
QTableWidget::item { padding: 3px 6px; }
QHeaderView::section { background-color: $surface2; color: $muted; border: none; border-bottom: 1px solid $border; padding: 6px 8px; font-weight: 600; }

QMessageBox QLabel { color: $text; }
""")


def build_stylesheet(theme: str = DEFAULT_THEME) -> str:
    """QSS для темы "dark" или "light" (неизвестное имя -> тёмная)."""
    return _QSS.substitute(THEMES.get(theme, THEMES[DEFAULT_THEME]))


STYLESHEET = build_stylesheet(DEFAULT_THEME)   # совместимость со старым кодом
