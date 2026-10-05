"""msp_panel.py - вкладка "MSP": живые параметры дрона из Betaflight + диагностика связи ESP32 <-> FC.

ФУНКЦИИ И КЛАССЫ ФАЙЛА
----------------------
diagnose_crsf(d: dict)
    Вывод для режима MSP по CRSF: нет запросов, нет кадров от FC (телеметрия выключена?), FC
    молчит на MSP, ответы пропали, часть ответов не разобрана, всё в порядке.
diagnose(d: dict)
    Вывод по счётчикам ESP32. В режиме CRSF вызывает diagnose_crsf; в запасном режиме UART3
    различает: нет байт, свои запросы на RX, FC застряла в CLI, помехи и CRC, нестабильная
    связь.
class MspPanel
    Вкладка «MSP»: таблица живых параметров дрона, крупная строка крена, тангажа и курса,
    управление запросами и диагностика связи ESP32 и FC.
  MspPanel.__init__(self, ws_client, parent=None)
    Создаёт органы управления, таблицу для всех разрешённых MSP-кодов и таймеры (отправка
    очереди запросов, автоопрос, обновление возраста данных).
  MspPanel._ensure_row(self, code: int)
    Возвращает строку таблицы для кода MSP, создавая её при первом обращении.
  MspPanel._on_diag(self, d: dict)
    Показывает счётчики обмена от ESP32 (транспорт, запросы, ответы, ошибки) и вывод diagnose().
  MspPanel._request_all(self)
    Ставит в очередь запросы всех кодов из READ_CODES.
  MspPanel._request_one(self)
    Ставит в очередь запрос кода из поля ввода, если он разрешён.
  MspPanel._pump_queue(self)
    Таймер 80 мс: отправляет из очереди один MSP-запрос (если есть соединение).
  MspPanel._on_msp(self, cmd: int, data: bytes, err: bool)
    Обновляет строку таблицы расшифровкой и hex; для ATTITUDE обновляет крупную строку крена,
    тангажа и курса.
  MspPanel._show_att(self)
    Выводит крен, тангаж и курс с возрастом данных и пометкой «данные устарели» при давности
    более 2 секунд.
  MspPanel._refresh_age(self)
    Таймер 300 мс: обновляет столбец «Возраст» для всех строк.
"""

import time

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QCheckBox, QHBoxLayout, QHeaderView, QLabel, QPushButton, QSpinBox,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from msp_codes import READ_CODES, decode, format_decoded


def diagnose_crsf(d: dict) -> str:
    req, resp, rx = d.get("crsf_req", 0), d.get("crsf_resp", 0), d.get("crsf_rx", 0)
    bad, consec = d.get("crsf_bad", 0), d.get("consec_to", 0)
    prefix = "CLI открыт, MSP идёт параллельно по CRSF. " if d.get("mode") == "cli" else ""
    if req == 0:
        return prefix + "ESP32 ещё не отправляла MSP-запросы по CRSF"
    if resp == 0:
        if rx == 0:
            return (prefix + "FC не присылает ни одного кадра CRSF: включите на FC телеметрию CRSF "
                    "(Configuration → Telemetry) и проверьте провода GPIO16/GPIO17 ↔ RX1/TX1. "
                    "Если ответов не будет, ESP32 сама перейдёт на MSP по UART3")
        return (prefix + "FC шлёт кадры CRSF, но не отвечает на MSP-запросы. "
                "Если ответов не будет, ESP32 сама перейдёт на MSP по UART3")
    if consec >= 15:
        return prefix + "ответы по CRSF перестали приходить (FC перезагружается или зависла?)"
    if bad > 0 and bad > resp // 4:
        return prefix + f"часть ответов не разобрана ({bad}): вероятно, ответы длиннее одного кадра CRSF"
    return prefix + "MSP по CRSF работает"


def diagnose(d: dict) -> str:
    """Вывод по счётчикам ESP32: где обрывается связь."""
    if d.get("transport") == "crsf":
        return diagnose_crsf(d)
    if d.get("mode") == "cli":
        return "режим CLI на UART3 (в запасном режиме MSP по UART3 опрос на время CLI остановлен)"
    tx, rx, ok, crc, to = (d.get(k, 0) for k in ("tx", "rx_bytes", "frames", "crc_err", "timeouts"))
    consec, since_ok, rec = d.get("consec_to", 0), d.get("since_ok", 0), d.get("recoveries", 0)
    selfe = d.get("self_echo", 0)
    if tx == 0:
        return "ESP32 ещё не отправляла запросы"
    if selfe > 20 and ok < selfe:
        return (f"ESP32 слышит СОБСТВЕННЫЕ запросы на RX ({selfe} раз): линия TX замыкается/наводится на RX "
                "(провода GPIO25 и GPIO26 рядом/касаются) либо FC не держит свой TX. Раздвиньте провода, "
                "проверьте замыкание; убедитесь, что FC включена и вышла из перезагрузки")
    if rx == 0:
        return ("ОТ FC НЕТ НИ БАЙТА: проверьте провода TX/RX крест-накрест и общую землю, "
                "что на UART3 включён MSP (Ports) и сохранено, скорость 115200")
    if consec >= 15 and since_ok >= 30:
        txt = ("FC принимает запросы, но отвечает не MSP-кадрами, а эхом: она, скорее всего, ЗАСТРЯЛА В РЕЖИМЕ CLI "
               "(любой байт '#' на порту MSP, в том числе помеха, переводит её в CLI). ")
        txt += (f"ESP32 уже посылала exit автоматически ({rec} раз) - подождите несколько секунд. "
                if rec else "ESP32 пошлёт exit автоматически. ")
        return txt + "Если не помогло - выключите и включите питание FC."
    if consec >= 15 and since_ok == 0:
        return "FC молчит (в последнее время ни байта): проверьте провода, питание FC и настройку UART3"
    if ok == 0 and crc == 0:
        return "байты идут, но это не MSP: скорость UART3 не 115200 или на порту не MSP"
    if ok == 0 and crc > 0:
        return "кадры приходят с ошибками контрольной суммы: помехи/скорость/неисправный провод"
    if consec >= 3:
        return "связь с FC нестабильна: идут подряд запросы без ответа"
    return "связь с FC (MSP по UART3, запасной режим) работает"


class MspPanel(QWidget):
    COLS = ["Код", "Имя", "Значение", "Hex", "Возраст, с"]

    def __init__(self, ws_client, parent=None):
        super().__init__(parent)
        self._ws = ws_client
        self._rows: dict = {}
        self._last: dict = {}
        self._queue: list = []
        self._att = None

        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.auto_check = QCheckBox("Автоопрос всех команд (раз в 2 с)")
        controls.addWidget(self.auto_check)
        all_btn = QPushButton("Запросить все сейчас")
        all_btn.clicked.connect(self._request_all)
        controls.addWidget(all_btn)
        controls.addWidget(QLabel("Код:"))
        self.code_spin = QSpinBox()
        self.code_spin.setRange(1, 199)
        self.code_spin.setValue(108)
        controls.addWidget(self.code_spin)
        one_btn = QPushButton("Запросить код")
        one_btn.clicked.connect(self._request_one)
        controls.addWidget(one_btn)
        layout.addLayout(controls)

        self.att_label = QLabel("Положение дрона: нет данных")
        self.att_label.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(self.att_label)

        self.info = QLabel("Только команды чтения. ESP32 сама постоянно опрашивает ATTITUDE, RAW_IMU, ANALOG, STATUS; "
                           "остальные строки заполняются по кнопкам «Запросить» или автоопросу.")
        self.info.setWordWrap(True)
        layout.addWidget(self.info)

        self.diag_label = QLabel("Диагностика связи ESP32 <-> FC: нет данных (подключитесь к ESP32)")
        self.diag_label.setWordWrap(True)
        layout.addWidget(self.diag_label)
        self.diag_verdict = QLabel("")
        self.diag_verdict.setWordWrap(True)
        layout.addWidget(self.diag_verdict)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)
        for code in sorted(READ_CODES):
            self._ensure_row(code)

        self._ws.msp_received.connect(self._on_msp)
        self._ws.msp_diag.connect(self._on_diag)
        self._pump = QTimer(self)
        self._pump.setInterval(80)
        self._pump.timeout.connect(self._pump_queue)
        self._pump.start()
        self._auto = QTimer(self)
        self._auto.setInterval(2000)
        self._auto.timeout.connect(lambda: self.auto_check.isChecked() and self._request_all())
        self._auto.start()
        self._age = QTimer(self)
        self._age.setInterval(300)
        self._age.timeout.connect(self._refresh_age)
        self._age.start()

    def _ensure_row(self, code: int) -> int:
        if code in self._rows:
            return self._rows[code]
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(str(code)))
        self.table.setItem(row, 1, QTableWidgetItem(READ_CODES.get(code, "?")))
        for c in (2, 3, 4):
            self.table.setItem(row, c, QTableWidgetItem("—"))
        self._rows[code] = row
        return row

    def _on_diag(self, d: dict):
        transport = "CRSF (линия UART1 FC)" if d.get("transport") == "crsf" else "UART3 (запасной режим)"
        self.diag_label.setText(
            f"MSP идёт по: {transport}. Запросов {d.get('tx', 0)}, кадров OK {d.get('frames', 0)}, "
            f"таймаутов {d.get('timeouts', 0)} (подряд {d.get('consec_to', 0)}). "
            f"CRSF: запросов {d.get('crsf_req', 0)}, ответов {d.get('crsf_resp', 0)}, не разобрано {d.get('crsf_bad', 0)}, "
            f"всего кадров от FC {d.get('crsf_rx', 0)}. "
            f"UART3: байт {d.get('rx_bytes', 0)}, CRC {d.get('crc_err', 0)}, собственных запросов на RX {d.get('self_echo', 0)}, "
            f"авто-exit {d.get('recoveries', 0)}")
        self.diag_verdict.setText("Вывод: " + diagnose(d))

    def _request_all(self):
        self._queue = [c for c in sorted(READ_CODES)]

    def _request_one(self):
        code = self.code_spin.value()
        if code not in READ_CODES:
            self.info.setText(f"Код {code} не входит в список разрешённых команд чтения.")
            return
        self._queue.append(code)

    def _pump_queue(self):
        if self._queue and self._ws.is_connected():
            self._ws.request_msp(self._queue.pop(0))

    def _on_msp(self, cmd: int, data: bytes, err: bool):
        row = self._ensure_row(cmd)
        self._last[cmd] = time.monotonic()
        if err:
            self.table.item(row, 2).setText("ошибка / команда не поддерживается")
            self.table.item(row, 3).setText("")
            return
        decoded = decode(cmd, data)
        self.table.item(row, 2).setText(format_decoded(decoded) if decoded else "(нет декодера - см. hex)")
        self.table.item(row, 3).setText(data.hex(" "))
        if cmd == 108 and decoded:
            self._att = decoded
            self._show_att()

    def _show_att(self):
        if not self._att:
            return
        age = time.monotonic() - self._last.get(108, 0.0)
        stale = "  (ДАННЫЕ УСТАРЕЛИ)" if age > 2.0 else ""
        self.att_label.setText(
            f"Крен {self._att['roll_deg']:+.1f}°   Тангаж {self._att['pitch_deg']:+.1f}°   "
            f"Курс {self._att['yaw_deg']:.0f}°   ({age:.1f} с назад){stale}")

    def _refresh_age(self):
        now = time.monotonic()
        for code, t in self._last.items():
            self.table.item(self._rows[code], 4).setText(f"{now - t:.1f}")
        self._show_att()
