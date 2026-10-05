"""cli_ws.py - CLI Betaflight через ESP32 (WebSocket -> UART1 ESP32 -> UART3 FC).

Главное:
- Окно НЕ блокируется на время обмена: пока идёт обмен, обрабатываются все события GUI. Блокировать
  нужные кнопки должен вызывающий код (см. settings_panel.py: кнопки CLI недоступны, остальные - рабочие).
- Чтение настроек: read_many() опрашивает параметры и принимает значение, только если оно ПОДТВЕРЖДЕНО
  (одно и то же значение получено в двух независимых ответах) и лежит в диапазоне "Allowed range".
  Искажённые помехами ответы просто отбрасываются и запрашиваются снова.
- Имя параметра определяется автоматически (разные версии Betaflight называют его по-разному);
  найденные имена можно передать как cached, тогда опрашиваются только они (быстрее всего).
- Запись: write_verified() выполняет set и ПЕРЕЧИТЫВАЕТ значение с подтверждением; пока значение не
  совпало, set повторяется. Вызывающий код сохраняет (save) только если всё подтверждено.
- Выход из CLI: "exit" (FC перезагружается), затем ожидание возврата MSP (а не слепая пауза).
- Обрыв связи с ESP32 или закрытие окна прерывают ожидание (исключения), зависаний нет.
"""

import re
import time

from PyQt6.QtCore import QCoreApplication, QEventLoop, QThread

ERR_MARK = "UNKNOWN COMMAND"
PROMPT_RE = re.compile(r"(?:^|\n)# ")
VAR_RE = re.compile(r"^([A-Za-z0-9_]+)\s*=\s*([A-Za-z0-9_.+\-]+)\s*$")
RANGE_RE = re.compile(r"^Allowed range:\s*(-?\d+)\s*-\s*(-?\d+)\s*$", re.IGNORECASE)
INVALID_RE = re.compile(r"INVALID NAME:\s*([A-Za-z0-9_]+)\s*#*\s*$", re.IGNORECASE)

PROMPT_QUIET_S = 0.10     # после приглашения ждём ещё немного, чтобы забрать хвост
NO_PROMPT_QUIET_S = 0.6   # приглашения нет, но поток замолчал на столько - ответ закончен (искажён)
MSP_ALIVE_WAIT_S = 8.0    # сколько ждать MSP-кадр перед входом в CLI
MSP_RETURN_WAIT_S = 12.0  # сколько ждать возврата MSP после exit/save (перезагрузка FC)

ABORT = False


class CliAborted(RuntimeError):
    """Обмен прерван (закрытие окна)."""


def request_abort():
    global ABORT
    ABORT = True


def reset_abort():
    global ABORT
    ABORT = False


def parse_vars(lines: list) -> dict:
    """Полные строки "имя = значение". Числа проверяются по строке "Allowed range: a - b" рядом."""
    out = {}
    for i, ln in enumerate(lines):
        m = VAR_RE.match(ln)
        if not m:
            continue
        name, val = m.group(1), m.group(2)
        rng = None
        for nxt in lines[i + 1:i + 3]:
            r = RANGE_RE.match(nxt)
            if r:
                rng = (int(r.group(1)), int(r.group(2)))
                break
            if VAR_RE.match(nxt):
                break
        if rng is not None and re.fullmatch(r"-?\d+", val) and not (rng[0] <= int(val) <= rng[1]):
            continue
        out[name] = val
    return out


def name_in_lines(lines: list, name: str) -> bool:
    """Встречается ли "имя =" (возможно, искажённое значение) - признак, что целевая строка была, но повреждена."""
    pat = re.compile(rf"(?:^|[^A-Za-z0-9_]){re.escape(name)}\s*=")
    return any(pat.search(ln) for ln in lines)


def has_invalid(lines: list, name: str) -> bool:
    for ln in lines:
        m = INVALID_RE.search(ln)
        if m and m.group(1) == name:
            return True
    return False


def same_number(a, b) -> bool:
    try:
        return int(float(a)) == int(float(b))
    except (TypeError, ValueError):
        return False


class FcCliClient:
    def __init__(self, ws, log_callback=None, max_s: float = 3.5):
        self._ws = ws
        self._log_callback = log_callback if log_callback is not None else (lambda line: None)
        self._max_s = max_s
        self._buf = ""
        self._got = False
        self._last = 0.0
        self.retries_used = 0
        self.exchanges = 0
        self._ws.cli_output.connect(self._on_out)

    # ---------- служебное ----------
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
        """Обработать события GUI (окно остаётся живым). Прерывается при закрытии окна и обрыве связи."""
        if ABORT:
            raise CliAborted("обмен прерван")
        if not self._ws.is_connected():
            raise RuntimeError("Соединение с ESP32 потеряно")
        QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, ms)
        QThread.msleep(5)

    def _reset_buf(self):
        self._buf, self._got = "", False

    @staticmethod
    def _prompt_after(text: str, cmd: str) -> bool:
        pos = text.find(cmd) if cmd else -1
        if pos >= 0:
            return PROMPT_RE.search(text, pos + len(cmd)) is not None
        return len(PROMPT_RE.findall(text)) >= (2 if cmd else 1)

    def _take(self) -> list:
        text, self._buf, self._got = self._buf, "", False
        lines = [ln.strip() for ln in text.replace("\r", "\n").split("\n")]
        return [ln for ln in lines if ln]

    def _wait_prompt(self, cmd: str = "", max_s=None, no_prompt_quiet_s=NO_PROMPT_QUIET_S) -> list:
        max_s = self._max_s if max_s is None else max_s
        start = time.monotonic()
        while True:
            self._pump()
            now = time.monotonic()
            if self._got:
                quiet = now - self._last
                text = self._buf.replace("\r", "\n")
                if self._prompt_after(text, cmd):
                    if quiet >= PROMPT_QUIET_S:
                        break
                elif quiet >= no_prompt_quiet_s:
                    break
            if now - start >= max_s:
                break
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
        end = time.monotonic() + max_s
        while time.monotonic() < end:
            if self._ws.last_msp_time >= newer_than:
                return True
            self._pump()
        return self._ws.last_msp_time >= newer_than

    def close(self):
        try:
            self._ws.cli_output.disconnect(self._on_out)
        except (TypeError, RuntimeError):
            pass
        if self._ws.cli_mode:
            self._ws.cli_leave()

    # ---------- вход / выход ----------
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
            text = "\n".join(lines).upper()
            if "CLI MODE" in text or any(ln.startswith("#") for ln in lines):
                self._log("<< вход в CLI выполнен")
                return
            self._log("-- FC не вошла в CLI, повтор через 1.5 с")
            self._ws.cli_leave()
            self._pause(1.5)
        raise RuntimeError("FC не входит в CLI после 3 попыток. Проверьте, что FC не заармлена "
                           "(AUX1 = 1000), питание и связь на вкладке «MSP».")

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

    # ---------- одиночные команды ----------
    def _exchange(self, cmd: str, max_s=None, no_prompt_quiet_s=NO_PROMPT_QUIET_S) -> list:
        """Одна команда: пустая строка (сброс мусора) + команда; ждём приглашение. Возвращает строки ответа."""
        self.exchanges += 1
        self._reset_buf()
        self._ws.cli_line("")
        self._ws.cli_line(cmd)
        return self._wait_prompt(cmd, max_s, no_prompt_quiet_s)

    def _send_line(self, line: str, max_s=None, retries: int = 2, no_prompt_quiet_s=NO_PROMPT_QUIET_S) -> list:
        """Ручная команда: полный вывод в лог, повтор при искажённом эхо. Строки всех попыток."""
        all_lines = []
        for attempt in range(retries + 1):
            self._log(f">> {line}")
            lines = self._exchange(line, max_s, no_prompt_quiet_s)
            for ln in lines:
                self._log(f"<< {ln}")
            all_lines.extend(lines)
            has_error = any(ERR_MARK in ln.upper() for ln in lines)
            echo_ok = any(ln == line for ln in lines)
            if has_error and not echo_ok and attempt < retries:
                self.retries_used += 1
                self._log("-- эхо искажено (помехи на линии), повтор команды")
                continue
            break
        return all_lines

    def dump(self, kind: str = "dump all") -> list:
        return self._send_line(kind, max_s=90.0, retries=0, no_prompt_quiet_s=4.0)

    # ---------- подтверждённое чтение ----------
    def read_confirmed(self, name: str, confirm: int = 2, max_reads: int = 6):
        """Значение переменной, подтверждённое confirm одинаковыми ответами подряд (или None)."""
        last, streak = None, 0
        for _ in range(max_reads):
            lines = self._exchange(f"get {name}")
            v = parse_vars(lines).get(name)
            if v is None:
                continue
            streak = streak + 1 if v == last else 1
            last = v
            if streak >= confirm:
                return v
        return None

    def read_many(self, requests: dict, cached: dict = None, confirm: int = 2, max_rounds: int = 8) -> dict:
        """requests: ключ -> [имена-кандидаты по приоритету]. cached: ключ -> имя, найденное ранее.
        Возвращает ключ -> (имя, значение) только для подтверждённых значений."""
        cached = cached or {}
        values = {}      # имя -> список значений из чистых ответов
        absent = {}      # имя -> сколько чистых ответов без этого параметра
        invalid = set()  # имена с чётким "INVALID NAME"
        result = {}

        def decided_absent(n):
            return n in invalid or absent.get(n, 0) >= 2

        def confirmed(n):
            v = values.get(n, [])
            return len(v) >= confirm and len(set(v[-confirm:])) == 1

        for rnd in range(1, max_rounds + 1):
            todo = []
            for key, cands in requests.items():
                if key in result:
                    continue
                c_name = cached.get(key)
                if c_name in cands and not decided_absent(c_name):
                    names = [c_name]          # известное по прошлому чтению имя: остальных кандидатов не опрашиваем
                else:
                    names = [c for c in cands if not decided_absent(c)]
                for n in names:
                    if n not in todo and not confirmed(n):
                        todo.append(n)
            if not todo:
                break
            for name in todo:
                lines = self._exchange(f"get {name}")
                got = parse_vars(lines)
                if name in got:
                    values.setdefault(name, []).append(got[name])
                    self._log(f"<< {name} = {got[name]}")
                elif has_invalid(lines, name):
                    invalid.add(name)
                    self._log(f"<< {name}: такого параметра нет в этой прошивке")
                elif got and not name_in_lines(lines, name) and not values.get(name):
                    absent[name] = absent.get(name, 0) + 1
                    self._log(f"<< {name}: точного совпадения нет ({absent[name]})")
                else:
                    self.retries_used += 1
                    self._log(f"<< {name}: ответ искажён помехами, повтор")
            for key, cands in requests.items():
                if key in result:
                    continue
                c_name = cached.get(key)
                if c_name in cands and confirmed(c_name):
                    result[key] = (c_name, values[c_name][-1])
                    continue
                if c_name in cands and not decided_absent(c_name):
                    continue                  # ждём подтверждения известного имени
                for n in cands:
                    if confirmed(n):
                        result[key] = (n, values[n][-1])
                        break
                    if decided_absent(n):
                        continue
                    break     # более приоритетный кандидат ещё не решён - ждём следующий раунд
        return result

    # ---------- подтверждённая запись ----------
    def write_verified(self, items: list, tries: int = 3) -> list:
        """items: [(имя, целое значение)]. Для каждого: set, затем подтверждённое чтение; повтор set при расхождении.
        Возвращает список имён, которые подтвердить не удалось (пусто = всё записано верно)."""
        failed = []
        for name, raw in items:
            ok = False
            for attempt in range(tries):
                self._log(f">> set {name} = {raw}")
                self._exchange(f"set {name} = {raw}")
                v = self.read_confirmed(name)
                if v is not None and same_number(v, raw):
                    self._log(f"<< {name} = {v}: запись подтверждена")
                    ok = True
                    break
                self._log(f"-- {name}: значение не подтверждено ({v}), повтор записи")
            if not ok:
                failed.append(name)
        return failed
