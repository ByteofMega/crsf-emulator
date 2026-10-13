"""flight_recorder.py - запись полёта по пульту и воспроизведение с коррекцией ROLL/PITCH/YAW по IMU (без Qt).

Что записывается (20 раз в секунду): время, все 16 каналов RC и углы из MSP_ATTITUDE (крен, тангаж, курс, град.).
Воспроизведение: каналы подаются по времени записи (стики и газ - интерполяция, переключатели AUX - ступенькой).
Коррекция (замкнутый контур по IMU): записанные углы сравниваются с текущими из MSP_ATTITUDE, разница пересчитывается
в добавку к стикам ROLL / PITCH / YAW:
  roll, pitch: добавка = KP * (угол_записи - угол_сейчас) / k_ось,  k_ось = "градусов на единицу хода стика",
               оценивается ПО САМОЙ ЗАПИСИ (регрессия угла на стик, со сдвигом по времени) - знак и масштаб
               определяются автоматически, поэтому не зависят от соглашения о знаках в Betaflight;
  yaw:         в ANGLE курсовой стик задаёт СКОРОСТЬ поворота: добавка = (ошибка_курса / YAW_TAU_S) / k_yaw,
               k_yaw = "град/с на единицу хода", тоже из записи; курс считается относительно момента старта.
Закон: P + медленный I (KP, KI), добавка ограничена (corr_max_frac хода стика), работает только когда IMU свежий и дрон "в воздухе" (арм и газ).
Если ось в записи почти не двигалась (нет данных для k_ось), по ней идёт воспроизведение без коррекции.

ФУНКЦИИ И КЛАССЫ ФАЙЛА
----------------------
wrap180(a) - угол -> диапазон -180..180.
class FlightRecording
  .samples - список (t, [16 каналов], roll|None, pitch|None, yaw|None); .duration - длительность, с.
  save_csv(path) / load_csv(path) - сохранение и загрузка CSV.
  start_is_safe() - запись начинается с дизарма и минимального газа (безопасный старт воспроизведения).
  gains() - оценка k для roll, pitch, yaw (None, если по оси нет данных).
class Recorder
  start(now) / add(now, rc, att) / finish() -> FlightRecording
class Replayer
  __init__(rec, kp, corr_max_frac, corr_enabled, ...) ; start(now, att)
  step(now, att, att_age) -> (channels[16], done, info)   info: ошибки и добавки по осям
(безопасное состояние каналов - common.safe_updates)
"""
import bisect
import csv
import math

from config import (ARM_IDX, DISARM_US, NUM_CHANNELS as NUM_CH, PITCH_IDX as PITCH, ROLL_IDX as ROLL,
                    THROTTLE_IDX as THROTTLE, THROTTLE_MIN_US, YAW_IDX as YAW)
from position_estimator import stick_from_us

ATT_STALE_S = 0.5            # IMU старше - коррекцию выключаем
CORR_MIN_THROTTLE_US = 1100  # коррекция только при газе выше (дрон "в воздухе")
YAW_TAU_S = 1.0              # за сколько секунд гасим ошибку курса
MAX_LAG_S = 0.4              # макс. сдвиг стик -> угол при оценке k
MIN_STICK_STD = 0.03         # ось считается "использованной", если стик менялся хотя бы на 3% хода
MIN_CORR = 0.4


def wrap180(a):
    return (a + 180.0) % 360.0 - 180.0


class FlightRecording:
    def __init__(self, samples=None):
        self.samples = samples or []

    @property
    def duration(self):
        return self.samples[-1][0] if self.samples else 0.0

    def save_csv(self, path):
        with open(path, "w", newline="", encoding="utf8") as f:
            w = csv.writer(f)
            w.writerow(["t"] + [f"ch{i + 1}" for i in range(NUM_CH)] + ["roll_deg", "pitch_deg", "yaw_deg"])
            for t, ch, r, p, y in self.samples:
                w.writerow([f"{t:.3f}"] + list(ch) + ["" if v is None else f"{v:.1f}" for v in (r, p, y)])

    @classmethod
    def load_csv(cls, path):
        samples = []
        with open(path, newline="", encoding="utf8") as f:
            rd = csv.reader(f)
            next(rd)
            for row in rd:
                if len(row) < 1 + NUM_CH + 3:
                    continue
                t = float(row[0])
                ch = [int(float(v)) for v in row[1:1 + NUM_CH]]
                att = [float(v) if v != "" else None for v in row[1 + NUM_CH:4 + NUM_CH]]
                samples.append((t, ch, att[0], att[1], att[2]))
        if len(samples) < 2:
            raise ValueError("в файле меньше двух отсчётов")
        return cls(samples)

    def start_is_safe(self):
        ch = self.samples[0][1]
        return ch[ARM_IDX] <= DISARM_US + 100 and ch[THROTTLE] <= THROTTLE_MIN_US + 100

    def _yaw_unwrapped(self):
        out, prev, acc = [], None, 0.0
        for _, _, _, _, y in self.samples:
            if y is None:
                out.append(None)
                continue
            if prev is not None:
                acc += wrap180(y - prev)
            else:
                acc = y
            prev = y
            out.append(acc)
        return out

    @staticmethod
    def _fit(xs, ys, dt, name_ok):
        """Наклон y(x) c поиском сдвига (0..MAX_LAG_S). Возвращает k или None."""
        n = len(xs)
        best = None
        max_lag = max(1, int(MAX_LAG_S / dt))
        for lag in range(0, max_lag + 1):
            px, py = [], []
            for i in range(lag, n):
                if xs[i - lag] is not None and ys[i] is not None and name_ok[i]:
                    px.append(xs[i - lag])
                    py.append(ys[i])
            if len(px) < 20:
                continue
            mx, my = sum(px) / len(px), sum(py) / len(py)
            vx = sum((a - mx) ** 2 for a in px) / len(px)
            vy = sum((b - my) ** 2 for b in py) / len(py)
            if vx <= 0 or vy <= 0:
                continue
            cov = sum((a - mx) * (b - my) for a, b in zip(px, py)) / len(px)
            corr = cov / math.sqrt(vx * vy)
            if math.sqrt(vx) < MIN_STICK_STD:
                continue
            if best is None or abs(corr) > abs(best[0]):
                best = (corr, cov / vx)
        if best is None or abs(best[0]) < MIN_CORR:
            return None
        return best[1]

    def gains(self):
        """(k_roll [град/ход], k_pitch [град/ход], k_yaw [град/с на ход]) - None, если данных нет."""
        n = len(self.samples)
        if n < 30:
            return None, None, None
        dt = (self.samples[-1][0] - self.samples[0][0]) / (n - 1)
        airborne = [s[1][THROTTLE] > CORR_MIN_THROTTLE_US and s[1][ARM_IDX] > DISARM_US + 100 for s in self.samples]
        sticks = [[stick_from_us(s[1][i]) for s in self.samples] for i in (ROLL, PITCH, YAW)]
        k_roll = self._fit(sticks[0], [s[2] for s in self.samples], dt, airborne)
        k_pitch = self._fit(sticks[1], [s[3] for s in self.samples], dt, airborne)
        yu = self._yaw_unwrapped()
        rate, h = [None] * n, 2
        for i in range(n):
            a, b = i - h, i + h
            if a < 0 or b >= n or yu[a] is None or yu[b] is None:
                continue
            tt = self.samples[b][0] - self.samples[a][0]
            if tt > 0:
                rate[i] = (yu[b] - yu[a]) / tt
        k_yaw = self._fit(sticks[2], rate, dt, airborne)
        return k_roll, k_pitch, k_yaw


class Recorder:
    MAX_SECONDS = 600.0

    def __init__(self):
        self.samples = []
        self._t0 = None

    def start(self, now):
        self.samples, self._t0 = [], now

    def add(self, now, rc, att):
        """att: (roll, pitch, yaw) в градусах или None, если IMU не свежий."""
        t = now - self._t0
        if t > self.MAX_SECONDS:
            return False
        r, p, y = att if att else (None, None, None)
        self.samples.append((t, [int(v) for v in rc[:NUM_CH]], r, p, y))
        return True

    def finish(self):
        return FlightRecording(self.samples)


class Replayer:
    def __init__(self, rec, kp=0.7, corr_max_frac=0.15, corr_enabled=True, ki=0.4):
        self.rec = rec
        self.kp, self.ki, self.corr_max, self.corr_enabled = kp, ki, corr_max_frac, corr_enabled
        self._i = {"roll": 0.0, "pitch": 0.0, "yaw": 0.0}
        self._last_now = None
        self.times = [s[0] for s in rec.samples]
        self.k_roll, self.k_pitch, self.k_yaw = rec.gains()
        self._yu = rec._yaw_unwrapped()
        self._t0 = None
        self._cur_prev = None
        self._cur_unw = 0.0
        self._rec_yaw0 = next((v for v in self._yu if v is not None), None)

    def start(self, now, att):
        self._t0 = now
        self._cur_prev = att[2] if att else None
        self._cur_unw = 0.0
        self._last_now = now

    def _pid(self, axis, e, k, scale=1.0):
        """P + медленный I (с ограничением): добавка в долях хода стика."""
        lim = self.corr_max * abs(k) / max(self.ki, 1e-6) / scale
        self._i[axis] = max(-lim, min(lim, self._i[axis] + e * self._dt))
        d = (self.kp * e / scale + self.ki * self._i[axis]) / k
        return max(-self.corr_max, min(self.corr_max, d))

    def _idx(self, t):
        i = bisect.bisect_right(self.times, t) - 1
        return max(0, min(i, len(self.times) - 2))

    def _lerp_channels(self, t):
        i = self._idx(t)
        t0, c0 = self.times[i], self.rec.samples[i][1]
        t1, c1 = self.times[i + 1], self.rec.samples[i + 1][1]
        a = 0.0 if t1 == t0 else max(0.0, min(1.0, (t - t0) / (t1 - t0)))
        ch = list(c0)
        for k in (ROLL, PITCH, THROTTLE, YAW):
            ch[k] = int(round(c0[k] + (c1[k] - c0[k]) * a))
        return ch

    def _lerp_att(self, t):
        i = self._idx(t)
        s0, s1 = self.rec.samples[i], self.rec.samples[i + 1]
        t0, t1 = s0[0], s1[0]
        a = 0.0 if t1 == t0 else max(0.0, min(1.0, (t - t0) / (t1 - t0)))

        def mix(v0, v1):
            return None if v0 is None or v1 is None else v0 + (v1 - v0) * a
        return mix(s0[2], s1[2]), mix(s0[3], s1[3]), mix(self._yu[i], self._yu[i + 1])

    def step(self, now, att, att_age):
        t = now - self._t0
        self._dt = max(0.0, min(0.2, now - self._last_now)) if self._last_now is not None else 0.0
        self._last_now = now
        info = dict(t=t, active=False, err_roll=None, err_pitch=None, err_yaw=None,
                    corr_roll_us=0, corr_pitch_us=0, corr_yaw_us=0)
        if t >= self.rec.duration:
            return None, True, info
        ch = self._lerp_channels(t)

        fresh = att is not None and att_age is not None and att_age <= ATT_STALE_S
        if fresh and self._cur_prev is not None:
            self._cur_unw += wrap180(att[2] - self._cur_prev)
        if fresh:
            self._cur_prev = att[2]

        in_air = ch[THROTTLE] > CORR_MIN_THROTTLE_US and ch[ARM_IDX] > DISARM_US + 100
        if not (self.corr_enabled and fresh and in_air):
            return ch, False, info
        rr, rp, ryu = self._lerp_att(t)
        info["active"] = True

        if self.k_roll and rr is not None:
            e = rr - att[0]
            d = self._pid("roll", e, self.k_roll)
            ch[ROLL] = int(round(max(1000, min(2000, ch[ROLL] + d * 500))))
            info["err_roll"], info["corr_roll_us"] = e, int(round(d * 500))
        if self.k_pitch and rp is not None:
            e = rp - att[1]
            d = self._pid("pitch", e, self.k_pitch)
            ch[PITCH] = int(round(max(1000, min(2000, ch[PITCH] + d * 500))))
            info["err_pitch"], info["corr_pitch_us"] = e, int(round(d * 500))
        if self.k_yaw and ryu is not None and self._rec_yaw0 is not None:
            e = (ryu - self._rec_yaw0) - self._cur_unw
            d = self._pid("yaw", e, self.k_yaw, scale=YAW_TAU_S)
            ch[YAW] = int(round(max(1000, min(2000, ch[YAW] + d * 500))))
            info["err_yaw"], info["corr_yaw_us"] = e, int(round(d * 500))
        return ch, False, info
