"""
telemetry_panel.py — виджет для отображения телеметрии от FC/дрона.

Как и ChannelPanel, этот виджет ничего не знает о WebSocket: он только
показывает данные и предоставляет метод update_telemetry(dict), который
main.py вызывает по сигналу ws_client.telemetry_received. Так реальные
показания дрона (батарея, углы, режим полета, качество линка) окажутся
в интерфейсе, даже если они отличаются от "базовых" значений по умолчанию.
"""

from PyQt6.QtWidgets import QFormLayout, QGroupBox, QLabel, QVBoxLayout, QWidget


class TelemetryPanel(QWidget):
    def __init__(self, parent=None):
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
