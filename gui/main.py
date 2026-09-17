"""
main.py — точка входа и "клей" между модулями.

MainWindow занимается только компоновкой виджетов (панель подключения +
TelemetryPanel + ChannelPanel) и подпиской на сигналы:
  ChannelPanel.value_changed  -> ws_client.send_channel
  ws_client.channels_received -> ChannelPanel.set_all_silent
  ws_client.telemetry_received -> TelemetryPanel.update_telemetry
  ws_client.connected/disconnected/error_occurred -> обновление статус-лейбла

Установка зависимостей:
    pip install PyQt6 PyQt6-WebSockets
"""

import sys

from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPushButton,
    QVBoxLayout, QWidget,
)

from channel_widgets import ChannelPanel
from config import DEFAULT_IP, DEFAULT_PORT, STYLESHEET
from telemetry_panel import TelemetryPanel
from ws_client import CrsfWsClient


class MainWindow(QMainWindow):
    """Главное окно приложения: панель подключения + телеметрия + каналы.

    Не содержит сетевой логики (делегирует её в CrsfWsClient) и не
    содержит логики отрисовки отдельных каналов (делегирует в
    ChannelPanel/TelemetryPanel) — только компонует их и связывает сигналами.
    """

    def __init__(self):
        """Построить интерфейс окна и подключить сигналы между модулями.

        Аргументы: нет (стандартный конструктор без параметров).
        """
        super().__init__()
        self.setWindowTitle("ELRS CRSF Emulator — ESP32 WebSocket GUI")
        self.resize(600, 780)
        self.setStyleSheet(STYLESHEET)

        self.ws_client = CrsfWsClient(self)
        self.ws_client.connected.connect(self._on_connected)
        self.ws_client.disconnected.connect(self._on_disconnected)
        self.ws_client.error_occurred.connect(self._on_error)
        self.ws_client.channels_received.connect(self._on_channels_received)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        # ---------- Панель подключения ----------
        conn_layout = QHBoxLayout()

        conn_layout.addWidget(QLabel("IP адрес ESP32:"))
        self.ip_input = QLineEdit(DEFAULT_IP)
        self.ip_input.setPlaceholderText("например 10.164.135.29 (без порта)")
        conn_layout.addWidget(self.ip_input)

        conn_layout.addWidget(QLabel("Порт:"))
        self.port_input = QLineEdit(str(DEFAULT_PORT))
        self.port_input.setFixedWidth(60)
        conn_layout.addWidget(self.port_input)

        self.connect_btn = QPushButton("Подключиться")
        self.connect_btn.clicked.connect(self._toggle_connection)
        conn_layout.addWidget(self.connect_btn)

        main_layout.addLayout(conn_layout)

        self.status_label = QLabel("Не подключено")
        self.status_label.setObjectName("statusLabel")
        main_layout.addWidget(self.status_label)

        # ---------- Панель телеметрии от дрона ----------
        self.telemetry_panel = TelemetryPanel()
        self.ws_client.telemetry_received.connect(self.telemetry_panel.update_telemetry)
        main_layout.addWidget(self.telemetry_panel)

        # ---------- Панель каналов ----------
        self.channel_panel = ChannelPanel()
        self.channel_panel.value_changed.connect(self.ws_client.send_channel)
        main_layout.addWidget(self.channel_panel)

    # ---------- обработчики UI ----------

    def _toggle_connection(self):
        """Обработать нажатие кнопки «Подключиться»/«Отключиться».

        Аргументы: нет.

        Возвращает:
            None. Если уже подключены — инициирует отключение. Если нет —
            валидирует введённые IP/порт и вызывает ws_client.connect_to().
            При ошибке валидации выводит сообщение в status_label и
            прерывает выполнение.
        """
        if self.ws_client.is_connected():
            self.ws_client.disconnect_from_host()
            return

        ip = self.ip_input.text().strip()
        port_text = self.port_input.text().strip()

        if not ip:
            self.status_label.setText("Введите IP адрес")
            return
        if not port_text.isdigit():
            self.status_label.setText("Порт должен быть числом")
            return

        self.status_label.setText(f"Подключение к {ip}:{port_text} ...")
        self.ws_client.connect_to(ip, int(port_text))

    # ---------- обработчики ws_client ----------

    def _on_connected(self):
        """Обновить UI после успешного подключения к ESP32.

        Аргументы: нет. Возвращает: None.
        """
        self.status_label.setText("Подключено")
        self.connect_btn.setText("Отключиться")

    def _on_disconnected(self):
        """Обновить UI после разрыва соединения с ESP32.

        Аргументы: нет. Возвращает: None.
        """
        self.status_label.setText("Отключено")
        self.connect_btn.setText("Подключиться")

    def _on_error(self, message: str):
        """Показать пользователю текст сетевой ошибки.

        Аргументы:
            message (str): человекочитаемое описание ошибки от CrsfWsClient.

        Возвращает:
            None.
        """
        self.status_label.setText(f"Ошибка: {message}")

    def _on_channels_received(self, values: list):
        """Применить полученное от ESP32 состояние каналов к панели.

        Аргументы:
            values (list[int]): список из NUM_CHANNELS значений в микросекундах.

        Возвращает:
            None.
        """
        self.channel_panel.set_all_silent(values)


def main():
    """Точка входа приложения: создать QApplication, окно и запустить цикл событий.

    Аргументы: нет.
    Возвращает: None (процесс завершается через sys.exit с кодом выхода Qt).
    """
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
