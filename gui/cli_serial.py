"""cli_serial.py — CLI полётного контроллера по USB (отдельно от ESP32).

Обновлено:
1) get_first_available() — перебирает несколько возможных имён одной и
   той же CLI-переменной (актуальное имя для новых версий Betaflight ->
   более старые варианты) и возвращает первое, которое реально понимает
   прошивка, стоящая на конкретном FC.

2) log_callback — необязательная функция обратного вызова
   log_callback(line: str), в которую отправляется КАЖДАЯ строка,
   отправленная в FC (с префиксом ">> ") и полученная от него
   (с префиксом "<< "). Используется settings_panel.py, чтобы показать
   в GUI живой лог обмена с CLI, как в терминале.
"""

import re
import time
import serial
import serial.tools.list_ports


def list_serial_ports() -> list[str]:
    """Список доступных серийных портов (имена устройств)."""
    return [p.device for p in serial.tools.list_ports.comports()]


class FcCliClient:
    """Клиент для текстового CLI Betaflight/iNav по USB-serial."""

    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 1.0, log_callback=None):
        self._ser = serial.Serial(port, baudrate=baudrate, timeout=timeout)
        self._log_callback = log_callback if log_callback is not None else (lambda line: None)
        time.sleep(0.3)

    def _log(self, line: str):
        try:
            self._log_callback(line)
        except Exception:
            pass  # лог не должен ронять обмен с FC, даже если GUI-виджет уже закрыт

    def close(self):
        if self._ser.is_open:
            self._ser.close()

    def enter_cli(self):
        """Войти в CLI (отправить '#' и очистить входной буфер)."""
        self._log(">> #")
        self._ser.write(b"#\r\n")
        time.sleep(0.2)
        self._ser.reset_input_buffer()

    def _send_line(self, line: str) -> list[str]:
        self._log(f">> {line}")
        self._ser.write((line + "\r\n").encode("ascii"))
        lines = []
        start = time.time()
        while time.time() - start < self._ser.timeout * 3:
            raw = self._ser.readline()
            if not raw:
                break
            decoded = raw.decode(errors="ignore").strip()
            if decoded:
                lines.append(decoded)
                self._log(f"<< {decoded}")
        return lines

    def get_value(self, name: str):
        """Прочитать одну CLI-переменную по точному имени.

        Возвращает строку с сырым значением или None, если прошивка не
        знает такую переменную.
        """
        response = self._send_line(f"get {name}")
        pattern = re.compile(rf"^{re.escape(name)}\s*=\s*([^\s]+)")
        for line in response:
            match = pattern.match(line)
            if match:
                return match.group(1)
        return None

    def get_first_available(self, names: list[str]):
        """Перебрать имена переменной (новое -> старое) и вернуть первое,
        которое существует в текущей прошивке.

        Возвращает:
            tuple[str | None, str | None]: (имя_которое_сработало,
                сырое_строковое_значение) либо (None, None).
        """
        for name in names:
            value = self.get_value(name)
            if value is not None:
                return name, value
        return None, None

    def set_value(self, name: str, value):
        return self._send_line(f"set {name} = {value}")

    def save(self):
        self._log(">> save")
        self._ser.write(b"save\r\n")

    def exit_cli(self, save_changes: bool = False):
        if save_changes:
            self.save()
        else:
            self._send_line("exit noreboot")
