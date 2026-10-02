"""telemetry_panel.py — виджет отображения телеметрии FC/дрона.

Обновлено: добавлен блок GPS (широта, долгота, скорость, курс, высота,
число спутников и текстовый статус фикса). Раньше GPS-координаты
использовались только "невидимо" — для отрисовки маркера на карте
(map_view.py), а текстом нигде не выводились, из-за чего было невозможно
понять, доходят ли вообще GPS-кадры с FC, если на карте почему-то не
появлялся маркер. Теперь это видно сразу на вкладке "Каналы", даже без
переключения на карту.
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

        gps_box = QGroupBox("GPS")
        gps_form = QFormLayout(gps_box)
        self.gps_status_label = QLabel("Нет данных GPS")
        self.gps_coords_label = QLabel("—")
        self.gps_speed_label = QLabel("—")
        self.gps_heading_label = QLabel("—")
        self.gps_altitude_label = QLabel("—")
        self.gps_satellites_label = QLabel("—")
        gps_form.addRow("Статус:", self.gps_status_label)
        gps_form.addRow("Координаты (широта, долгота):", self.gps_coords_label)
        gps_form.addRow("Скорость, км/ч:", self.gps_speed_label)
        gps_form.addRow("Курс, °:", self.gps_heading_label)
        gps_form.addRow("Высота, м:", self.gps_altitude_label)
        gps_form.addRow("Спутники:", self.gps_satellites_label)
        layout.addWidget(gps_box)

    def update_telemetry(self, telemetry: dict):
        battery = telemetry.get("battery")
        if battery:
            self.battery_label.setText(
                f"{battery['voltage_v']:.2f} В, {battery['current_a']:.2f} А, "
                f"{battery['capacity_mah']} мАч, {battery['percent']}%"
            )

        attitude = telemetry.get("attitude")
        if attitude:
            self.attitude_label.setText(
                f"P={attitude['pitch']:.2f} R={attitude['roll']:.2f} Y={attitude['yaw']:.2f}"
            )

        flight_mode = telemetry.get("flight_mode")
        if flight_mode:
            self.flight_mode_label.setText(flight_mode)

        link = telemetry.get("link")
        if link:
            self.link_label.setText(f"{link['rssi']} дБм / LQ {link['lq']}% / SNR {link['snr']}")

        gps = telemetry.get("gps")
        if gps:
            satellites = gps.get("satellites", 0)
            if satellites >= 5:
                self.gps_status_label.setText(f"Фикс есть ({satellites} спутников)")
            elif satellites > 0:
                self.gps_status_label.setText(f"Слабый сигнал ({satellites} спутников)")
            else:
                self.gps_status_label.setText("Нет фикса (0 спутников)")
            self.gps_coords_label.setText(f"{gps['lat']:.6f}, {gps['lon']:.6f}")
            self.gps_speed_label.setText(f"{gps['speed_kmh']:.1f}")
            self.gps_heading_label.setText(f"{gps['heading_deg']:.1f}")
            self.gps_altitude_label.setText(f"{gps['altitude_m']}")
            self.gps_satellites_label.setText(str(satellites))
        else:
            self.gps_status_label.setText("Нет данных GPS (кадр CRSF GPS ещё не пришёл)")
