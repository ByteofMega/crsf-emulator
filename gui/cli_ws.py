"""cli_ws.py - CLI Betaflight через ESP32 (WebSocket -> UART1 ESP32 -> UART3 FC).

API как у прежнего FcCliClient: enter_cli / get_value / get_first_available / set_value /
save / exit_cli / dump.

Как устроен обмен:
- ПЕРЕД входом в CLI проверяется, что FC жива и отвечает по MSP (свежий MSP-кадр); иначе вход не
  выполняется (раньше команды уходили "в пустоту", а в лог возвращалось только эхо);
- вход в CLI подтверждается баннером/приглашением "#", до 3 попыток;
- конец ответа определяется по приглашению "# ";
- каждая команда уходит сразу после пустой строки (сброс мусора в строке CLI FC);
- ответ принимается по СОДЕРЖАНИЮ: get - "имя = значение", set - "... set to ..."; иначе до 2 повторов;
- если FC ответила "INVALID NAME: имя", параметра в этой прошивке нет - повторов нет;
- во время ожидания события мыши/клавиатуры игнорируются;
- выход: "exit" (FC перезагружается); затем ждём, пока снова пойдут MSP-кадры (а не слепая пауза).
"""

import re
import time

from PyQt6.QtCore import QCoreApplication, QEventLoop, QThread

ERR_MARK = "UNKNOWN COMMAND"
PROMPT_RE = re.compile(r"(?:^|\n)# ")
PROMPT_QUIET_S = 0.12     # после приглашения ждём ещё немного, чтобы забрать хвост
NO_PROMPT_QUIET_S = 1.5   # если приглашения нет, но поток замолчал на столько - считаем ответ законченным
MSP_ALIVE_WAIT_S = 8.0    # сколько ждать MSP-кадр перед входом в CLI
MSP_RETURN_WAIT_S = 12.0  # сколько ждать возврата MSP после exit/save (перезагрузка FC)


class FcCliClient:
    def __init__(self, ws, log_callback=None, max_s: float = 4.0):
        self._ws = ws
        self._log_callback = log_callback if log_callback is not None else (lambda line: None)
        self._max_s = max_s
        self._buf = ""
        self._got = False
        self._last = 0.0
        self.retries_used = 0
        self.answers = 0          # сколько команд получили приглашение в ответ (признак живой связи)
        self._ws.cli_output.connect(self._on_out)

    def _log(self, line: str):
        try:
            self._log_callback(line)
        except Exception:
            pass

    def _on_out(self, text: str):
        self._buf += text
        self._got = True
        self._last = time.monotonic()

    def _pump(self, ms: int = 20):
        QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents, ms)
        QThread.msleep(5)

    def _reset_buf(self):
        self._buf, self._got = "", False

    @staticmethod
    def _prompt_after(text: str, cmd: str) -> bool:
        """Есть ли приглашение "# " ПОСЛЕ эха команды (приглашение от пустой строки не считается)."""
        pos = text.find(cmd) if cmd else -1
        if pos >= 0:
            return PROMPT_RE.search(text, pos + len(cmd)) is not None
        return len(PROMPT_RE.findall(text)) >= (2 if cmd else 1)

    def _take(self) -> list:
        text, self._buf, self._got = self._buf, "", False
        lines = [ln.strip() for ln in text.replace("\r", "\n").split("\n")]
        return [ln for ln in lines if ln]

    def _wait_prompt(self, cmd: str = "", max_s=None, no_prompt_quiet_s=NO_PROMPT_QUIET_S) -> list:
        """Ждать ответ до приглашения "# " (после эха cmd)."""
        max_s = self._max_s if max_s is None else max_s
        start = time.monotonic()
        got_prompt = False
        while True:
            self._pump()
            now = time.monotonic()
            if self._got:
                quiet = now - self._last
                text = self._buf.replace("\r", "\n")
                if self._prompt_after(text, cmd):
                    got_prompt = True
                    if quiet >= PROMPT_QUIET_S:
                        break
                elif quiet >= no_prompt_quiet_s:
                    break
            if now - start >= max_s:
                break
        if got_prompt:
            self.answers += 1
        return self._take()

    def _wait_quiet(self, quiet_s: float, max_s: float) -> list:
        start = time.monotonic()
        while True:
            self._pump()
            now = time.monotonic()
            if self._got and now - self._last >= quiet_s:
                break
            if now - start >= max_s:
                break
        return self._take()

    def _pause(self, seconds: float):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self._pump()
        self._reset_buf()

    def _wait_msp_frame(self, max_s: float, newer_than: float) -> bool:
        """Ждать MSP-кадр от FC, пришедший не раньше момента newer_than (time.monotonic)."""
        end = time.monotonic() + max_s
        while time.monotonic() < end:
            if self._ws.last_msp_time >= newer_than:
                return True
            self._pump()
        return self._ws.last_msp_time >= newer_than

    def close(self):
        try:
            self._ws.cli_output.disconnect(self._on_out)
        except TypeError:
            pass
        if self._ws.cli_mode:
            self._ws.cli_leave()

    def enter_cli(self):
        """Войти в CLI: FC должна быть жива по MSP; до 3 попыток; успех - баннер/приглашение '#'."""
        if not self._ws.is_connected():
            raise RuntimeError("Нет соединения с ESP32 (WebSocket)")
        if not self._wait_msp_frame(MSP_ALIVE_WAIT_S, time.monotonic() - 1.5):
            raise RuntimeError("FC не отвечает по MSP (нет кадров). Откройте вкладку «MSP», убедитесь, что значения "
                               "обновляются, подождите 5-10 с после перезагрузки FC и повторите.")
        for attempt in range(1, 4):
            self._log(f">> #  (попытка {attempt})")
            self._reset_buf()
            self._ws.cli_enter()
            lines = self._wait_prompt("", max_s=4.0, no_prompt_quiet_s=1.5)
            for ln in lines:
                self._log(f"<< {ln}")
            text = "\n".join(lines).upper()
            if "CLI MODE" in text or any(ln.startswith("#") for ln in lines):
                return
            self._log("-- FC не вошла в CLI, повтор через 1.5 с")
            self._ws.cli_leave()      # вернуть ESP32 в режим MSP
            self._pause(1.5)
        raise RuntimeError("FC не входит в CLI после 3 попыток. Проверьте, что FC не заармлена "
                           "(AUX1 = 1000), питание и связь на вкладке «MSP».")

    def _send_line(self, line: str, max_s=None, retries: int = 2, no_prompt_quiet_s=NO_PROMPT_QUIET_S,
                   accept=None) -> list:
        """Отправить команду. accept(lines) -> True, если ответ годный (иначе повтор).
        Без accept: повтор при искажённом эхо. Возвращает строки ВСЕХ попыток."""
        all_lines = []
        for attempt in range(retries + 1):
            self._reset_buf()
            self._log(f">> {line}")
            self._ws.cli_line("")      # пустая строка: FC выбрасывает накопленный мусор
            self._ws.cli_line(line)    # сразу за ней - сама команда
            lines = self._wait_prompt(line, max_s, no_prompt_quiet_s)
            for ln in lines:
                self._log(f"<< {ln}")
            all_lines.extend(lines)
            if accept is not None:
                if accept(lines):
                    break
                if attempt < retries:
                    self.retries_used += 1
                    self._log("-- ответ искажён помехами, повтор команды")
                    continue
                break
            has_error = any(ERR_MARK in ln.upper() for ln in lines)
            echo_ok = any(ln == line for ln in lines)
            if has_error and not echo_ok and attempt < retries:
                self.retries_used += 1
                self._log("-- эхо искажено (помехи на линии), повтор команды")
                continue
            break
        return all_lines

    def get_value(self, name: str):
        pattern = re.compile(rf"^{re.escape(name)}\s*=\s*([A-Za-z0-9_.+\-]+)\s*$")
        invalid = f"INVALID NAME: {name}".upper()

        def found(lines):
            return any(pattern.match(ln) for ln in lines)

        def accept(lines):
            return found(lines) or any(invalid in ln.upper() for ln in lines)

        for line in self._send_line(f"get {name}", accept=accept):
            m = pattern.match(line)
            if m:
                return m.group(1)
        return None

    def get_first_available(self, names: list):
        for name in names:
            value = self.get_value(name)
            if value is not None:
                return name, value
        return None, None

    def set_value(self, name: str, value):
        """set имя = значение; успех - строка "... set to ..." (иначе повтор: set идемпотентна)."""
        return self._send_line(f"set {name} = {value}", accept=lambda ls: any(" SET TO " in ln.upper() for ln in ls))

    def dump(self, kind: str = "dump all") -> list:
        """Полный дамп настроек: 'dump all', 'diff all', 'dump' и т.д."""
        return self._send_line(kind, max_s=90.0, retries=0, no_prompt_quiet_s=4.0)

    def _leave_and_wait_msp(self, what: str):
        t_leave = time.monotonic()
        self._ws.cli_leave()
        self._log(f"-- {what}, FC перезагружается; жду возврата MSP (до {MSP_RETURN_WAIT_S:.0f} с)")
        self._pause(1.5)
        if self._wait_msp_frame(MSP_RETURN_WAIT_S, t_leave + 1.0):
            self._log("-- FC снова отвечает по MSP")
        else:
            self._log("!! FC не вернулась в MSP: возможно, осталась в CLI. ESP32 пошлёт exit сама; "
                      "если за минуту не оживёт - выключите и включите питание FC")

    def save(self):
        self._reset_buf()
        self._log(">> save")
        self._ws.cli_line("")
        self._ws.cli_line("save")
        self._wait_quiet(0.3, 1.0)
        self._leave_and_wait_msp("save")

    def exit_cli(self, save_changes: bool = False):
        if save_changes:
            self.save()
        else:
            self._reset_buf()
            self._log(">> exit")
            self._ws.cli_line("")
            self._ws.cli_line("exit")
            self._wait_quiet(0.3, 1.0)
            self._leave_and_wait_msp("выход из CLI")
        self.close()
