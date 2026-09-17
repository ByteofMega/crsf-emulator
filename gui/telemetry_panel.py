"""
telemetry_panel.py — виджет для отображения телеметрии от FC/дрона.

Как и ChannelPanel, этот виджет ничего не знает о WebSocket: он только
показывает данные и предоставляет метод update_telemetry(dict), который
main.py вызывает по сигналу ws_client.telemetry_received.
"""

from PyQt6.QtWidgets import QFormLayout, QGroupBox, QLabel, QVBoxLayout, QWidget


class TelemetryPanel(QWidget):
    """Панель с текущими показаниями телеметрии полётного контроллера.

    Отображает четыре группы данных: батарею, углы ориентации, текущий
    режим полёта и качество радиолинка. Каждая группа обновляется
    независимо — если в очередном пакете телеметрии какого-то поля нет,
    соответствующая метка просто не обновляется (сохраняет предыдущее
    значение).
    """

    def __init__(self, parent=None):
        """Создать панель с четырьмя пустыми полями телеметрии.

        Аргументы:
            parent (QWidget | None): родительский виджет Qt, по умолчанию None.
        """
        super().__init__(parent)

        layout = QVBoxLayout(self)

        box = QGroupBox("Телеметрия с дрона (FC)")
        form = QFormLayout(box)

        self.battery_label = QLabel("—")
        self.attitude_label = QLabel("—")
        self.flight_mode_label = QLabel("—")
        self.link_label = QLabel("—")

        form.addRow("Батарея:", self.battery_label)
        form.addRow("Углы (P/R/Y), рад:", self.attitude_label)
        form.addRow("Режим полета:", self.flight_mode_label)
        form.addRow("Линк (RSSI/LQ/SNR):", self.link_label)

        layout.addWidget(box)

    def update_telemetry(self, telemetry: dict):
        """Обновить отображаемые значения на основе пакета телеметрии.

        Аргументы:
            telemetry (dict): словарь, полученный из сигнала
                CrsfWsClient.telemetry_received. Поддерживаемые
                необязательные ключи:
                  - "battery" (dict): voltage_v (float), current_a (float),
                    capacity_mah (int), percent (int).
                  - "attitude" (dict): pitch, roll, yaw (float, радианы).
                  - "flight_mode" (str): название текущего режима полёта.
                  - "link" (dict): rssi (int), lq (int), snr (int).

        Возвращает:
            None. Отсутствующие ключи просто пропускаются без ошибок.
        """
        battery = telemetry.get("battery")
        if battery:
            self.battery_label.setText(
                f"{battery['voltage_v']:.2f} В, "
                f"{battery['current_a']:.2f} А, "
                f"{battery['capacity_mah']} мАч, "
                f"{battery['percent']}%"
            )

        attitude = telemetry.get("attitude")
        if attitude:
            self.attitude_label.setText(
                f"P={attitude['pitch']:.2f}  R={attitude['roll']:.2f}  Y={attitude['yaw']:.2f}"
            )

        flight_mode = telemetry.get("flight_mode")
        if flight_mode:
            self.flight_mode_label.setText(flight_mode)

        link = telemetry.get("link")
        if link:
            self.link_label.setText(f"{link['rssi']} дБм / LQ {link['lq']}% / SNR {link['snr']}")
