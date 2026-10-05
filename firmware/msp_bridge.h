/*
 * ДОКУМЕНТАЦИЯ ФАЙЛА msp_bridge.h
 * Мост ESP32 и FC: MSP (параметры дрона) по линии CRSF и CLI по UART1 (GPIO25/26); запасной MSP по UART3; диагностика.
 *
 * ФУНКЦИИ:
 * msp::command_allowed(int c)
 *     Разрешает только коды чтения MSP (1-199, кроме 68 - перезагрузка).
 * msp::queue_push(uint8_t c) / msp::queue_pop(uint8_t& c)
 *     Кольцевая очередь запросов MSP от GUI (8 элементов).
 * msp::next_poll_cmd()
 *     Следующий запрос: из очереди GUI, иначе по циклу постоянного опроса (ATTITUDE, RAW_IMU,
 *     ANALOG, STATUS).
 * msp::drain_input()
 *     Сбрасывает входной буфер UART1 и состояние разбора.
 * msp::send_request_uart3(uint8_t cmd)
 *     Запасной режим: отправляет MSP-запрос по UART3 в формате $M<.
 * msp::feed(uint8_t b)
 *     Побайтовый разбор MSP-ответа UART3 ($M>, длина, код, данные, контрольная сумма); считает
 *     ошибки CRC и собственные запросы на RX.
 * msp::to_hex(const uint8_t* d, size_t n)
 *     Байты -> строка hex.
 * msp::emit_frame(WiFiClient& c)
 *     Отправляет в GUI принятый MSP-кадр в виде JSON.
 * msp::emit_diag(WiFiClient& c)
 *     Раз в секунду отправляет в GUI счётчики обмена (транспорт, запросы, ответы, ошибки).
 * msp::flush_cli(WiFiClient& c)
 *     Отправляет в GUI накопленный текст из CLI.
 * msp::enter_cli()
 *     Включает режим CLI: сбрасывает буфер и планирует отправку одного символа # после паузы.
 * msp::leave_cli()
 *     Возвращает режим MSP и сбрасывает счётчики восстановления.
 * msp::on_client_lost()
 *     При обрыве связи с GUI во время CLI отправляет exit и выходит из режима CLI.
 * msp::apply_client_message(const String& text)
 *     Обрабатывает сообщения GUI «msp_req» и «cli» (enter, line, leave); true, если сообщение
 *     предназначалось модулю.
 * msp::pump_cli(WiFiClient& c, uint32_t now)
 *     В режиме CLI отправляет # по таймеру и пересылает принятые от FC символы в GUI порциями.
 * msp::send_exit(uint32_t now)
 *     Отправляет в FC пустую строку и exit (выход из зависшего CLI).
 * msp::crsf_poll(WiFiClient& c, uint32_t now)
 *     Основной цикл MSP по CRSF: передаёт ответы в GUI, отслеживает тайм-ауты, отправляет
 *     следующий запрос и при полном отсутствии ответов переключается на UART3.
 * msp::uart3_poll(WiFiClient& c, uint32_t now)
 *     Запасной цикл MSP по UART3 с автовыходом из CLI, если FC зависла в нём.
 * msp::poll(WiFiClient& c, bool ws_ready)
 *     Вызывается из loop(): отправляет диагностику и запускает MSP по выбранному транспорту,
 *     параллельно обслуживая CLI.
 */

/**
 * @file msp_bridge.h
 * @brief Мост ESP32 <-> полётный контроллер: MSP (опрос параметров дрона) и CLI (настройки).
 *
 * ДВЕ НЕЗАВИСИМЫЕ ЛИНИИ:
 *   - Serial2 (GPIO16/17) <-> UART1 FC: CRSF (RC-каналы + телеметрия) и, ПО УМОЛЧАНИЮ, MSP внутри CRSF-кадров
 *     (msp_crsf.h). Эта линия работает всегда, в том числе пока открыт CLI.
 *   - FC_SERIAL = Serial1 (GPIO25/26) <-> UART3 FC: CLI (и запасной канал MSP).
 * Поэтому MSP и CLI работают одновременно: во время CLI-сессии MSP-данные продолжают идти.
 * Если за MSP_CRSF_FALLBACK_REQS запросов по CRSF не пришло ни одного ответа (на FC не включена телеметрия CRSF
 * или версия прошивки не поддерживает MSP по CRSF), ESP32 сама переходит на MSP по UART3 (как раньше; тогда
 * на время CLI опрос MSP останавливается). Принудительно только UART3: #define MSP_USE_CRSF 0.
 *
 * Сообщения от GUI (WebSocket, JSON):
 *   {"msp_req": 108}                      - один раз запросить MSP-команду
 *   {"cli": "enter"} / {"cli": "line", "text": "..."} / {"cli": "leave"}
 * Сообщения в GUI:
 *   {"msp": {"cmd": 108, "err": 0, "data": "hex..."}}
 *   {"cli_out": "текст из CLI"}
 *   {"msp_diag": {...}}  - счётчики обмена (диагностика)
 *
 * Вход в CLI: ESP32 шлёт ОДИН '#' (без \r\n) после паузы 400 мс без своей передачи по UART3.
 * Автовыход из CLI (только в режиме MSP по UART3): см. MSP_AUTO_RECOVER.
 * Разрешены только команды чтения (command_allowed): SET-команды MSP запрещены.
 */
#pragma once
#include <Arduino.h>
#include <ArduinoJson.h>
#include <WiFi.h>
#include "config.h"
#include "ws_transport.h"
#include "msp_crsf.h"

#ifndef MSP_USE_CRSF
#define MSP_USE_CRSF 1
#endif

#define MSP_POLL_PERIOD_MS   30
#define MSP_RESP_TIMEOUT_MS  120
#define CLI_IDLE_FLUSH_MS    40
#define CLI_CHUNK_MAX        200
#define MSP_DIAG_PERIOD_MS   1000
#define CLI_HASH_DELAY_MS    400
#define MSP_CRSF_FALLBACK_REQS 250   // запросов по CRSF без единого ответа (~35 с) -> переход на UART3

#define MSP_AUTO_RECOVER         1      // 0 - выключить автоматический "exit" (режим MSP по UART3)
#define MSP_CLI_EXIT_PERIOD_MS   5000
#define MSP_CLI_EXIT_MAX_TRIES   4
#define MSP_RECOVER_TIMEOUTS     40
#define MSP_RECOVER_MIN_BYTES    200
#define MSP_RECOVER_PERIOD_MS    20000

namespace msp {

enum Mode : uint8_t { MODE_MSP = 0, MODE_CLI = 1 };
static Mode mode = MODE_MSP;
static bool use_crsf = (MSP_USE_CRSF != 0);

// Постоянный опрос: ATTITUDE(108) чаще остальных, RAW_IMU(102), ANALOG(110), STATUS(101)
static const uint8_t POLL_CMDS[] = { 108, 102, 108, 110, 101 };
static const size_t POLL_COUNT = sizeof(POLL_CMDS) / sizeof(POLL_CMDS[0]);
static size_t poll_idx = 0;

static uint8_t req_queue[8];
static uint8_t q_head = 0, q_tail = 0;

static bool waiting = false;
static uint32_t req_ms = 0;
static uint32_t last_req_ms = 0;
static uint32_t last_diag_ms = 0;
static uint32_t last_recover_ms = 0;
static uint32_t last_exit_ms = 0;
static uint8_t exit_tries = 0;
static bool cli_prompt_seen = false;
static uint8_t tail3[3] = { 0, 0, 0 };
static bool hash_pending = false;
static uint32_t hash_due_ms = 0;

// Диагностика
static uint32_t d_tx = 0;        // отправлено MSP-запросов (любым путём)
static uint32_t d_rx_bytes = 0;  // принято байт по UART3
static uint32_t d_frames = 0;    // принято корректных MSP-кадров
static uint32_t d_crc = 0;       // кадров с ошибкой контрольной суммы (UART3)
static uint32_t d_err = 0;       // ответов-ошибок
static uint32_t d_timeout = 0;   // запросов без ответа
static uint32_t d_recover = 0;   // автоматических "exit"
static uint32_t d_self = 0;      // собственных запросов '$M<' на RX UART3
static uint32_t consec_timeouts = 0;
static uint32_t rx_since_ok = 0;

struct Parser {
  uint8_t state = 0;
  uint8_t len = 0, cmd = 0, idx = 0, crc = 0;
  bool err = false;
  uint8_t buf[256];
};
static Parser p;

static char cli_buf[CLI_CHUNK_MAX];
static size_t cli_len = 0;
static uint32_t cli_last_rx_ms = 0;

inline bool command_allowed(int c) {
  return c > 0 && c < 200 && c != 68;
}

inline bool queue_push(uint8_t c) {
  uint8_t next = (q_head + 1) % sizeof(req_queue);
  if (next == q_tail) return false;
  req_queue[q_head] = c;
  q_head = next;
  return true;
}

inline bool queue_pop(uint8_t& c) {
  if (q_head == q_tail) return false;
  c = req_queue[q_tail];
  q_tail = (q_tail + 1) % sizeof(req_queue);
  return true;
}

inline uint8_t next_poll_cmd() {
  uint8_t cmd;
  if (!queue_pop(cmd)) {
    cmd = POLL_CMDS[poll_idx];
    poll_idx = (poll_idx + 1) % POLL_COUNT;
  }
  return cmd;
}

inline void drain_input() {
  while (FC_SERIAL.available()) FC_SERIAL.read();
  p.state = 0;
  tail3[0] = tail3[1] = tail3[2] = 0;
  cli_prompt_seen = false;
}

inline void send_request_uart3(uint8_t cmd) {
  // MSP v1: '$' 'M' '<' len cmd [payload] checksum; без payload checksum = len ^ cmd = cmd
  uint8_t f[6] = { '$', 'M', '<', 0, cmd, cmd };
  FC_SERIAL.write(f, sizeof(f));
  d_tx++;
}

// Разбор MSP-кадра с UART3. true - собран корректный кадр (p.cmd, p.len, p.buf, p.err)
inline bool feed(uint8_t b) {
  switch (p.state) {
    case 0: if (b == '$') p.state = 1; break;
    case 1: p.state = (b == 'M') ? 2 : 0; break;
    case 2:
      if (b == '>') { p.err = false; p.state = 3; }
      else if (b == '!') { p.err = true; p.state = 3; }
      else {
        if (b == '<') d_self++;   // FC так не отвечает: наш собственный запрос вернулся на RX
        p.state = 0;
      }
      break;
    case 3: p.len = b; p.crc = b; p.idx = 0; p.state = 4; break;
    case 4:
      p.cmd = b; p.crc ^= b;
      p.state = p.len ? 5 : 6;
      break;
    case 5:
      p.buf[p.idx++] = b; p.crc ^= b;
      if (p.idx >= p.len) p.state = 6;
      break;
    case 6:
      p.state = 0;
      if (b == p.crc) return true;
      d_crc++;
      return false;
  }
  return false;
}

inline String to_hex(const uint8_t* d, size_t n) {
  static const char* H = "0123456789abcdef";
  String s;
  s.reserve(n * 2);
  for (size_t i = 0; i < n; i++) {
    s += H[d[i] >> 4];
    s += H[d[i] & 0x0F];
  }
  return s;
}

inline void emit_frame(WiFiClient& c) {
  String msg;
  msg.reserve(64 + p.len * 2);
  msg += "{\"msp\":{\"cmd\":";
  msg += String((int)p.cmd);
  msg += ",\"err\":";
  msg += p.err ? "1" : "0";
  msg += ",\"data\":\"";
  msg += to_hex(p.buf, p.len);
  msg += "\"}}";
  ws::send_text(c, msg);
}

inline void emit_diag(WiFiClient& c) {
  String msg = "{\"msp_diag\":{\"mode\":\"";
  msg += (mode == MODE_CLI) ? "cli" : "msp";
  msg += "\",\"transport\":\"";
  msg += use_crsf ? "crsf" : "uart3";
  msg += "\",\"tx\":" + String(d_tx);
  msg += ",\"rx_bytes\":" + String(d_rx_bytes);
  msg += ",\"frames\":" + String(d_frames);
  msg += ",\"crc_err\":" + String(d_crc);
  msg += ",\"msp_err\":" + String(d_err);
  msg += ",\"timeouts\":" + String(d_timeout);
  msg += ",\"consec_to\":" + String(consec_timeouts);
  msg += ",\"since_ok\":" + String(rx_since_ok);
  msg += ",\"recoveries\":" + String(d_recover);
  msg += ",\"self_echo\":" + String(d_self);
  msg += ",\"crsf_req\":" + String(msp_crsf::d_req);
  msg += ",\"crsf_resp\":" + String(msp_crsf::d_resp);
  msg += ",\"crsf_bad\":" + String(msp_crsf::d_bad);
  msg += ",\"crsf_rx\":" + String(msp_crsf::d_rx);
  msg += "}}";
  ws::send_text(c, msg);
}

inline void flush_cli(WiFiClient& c) {
  if (cli_len == 0) return;
  cli_buf[cli_len] = '\0';
  StaticJsonDocument<JSON_OBJECT_SIZE(1) + 16> doc;
  doc["cli_out"] = (const char*)cli_buf;
  String out;
  serializeJson(doc, out);
  ws::send_text(c, out);
  cli_len = 0;
}

inline void enter_cli() {
  mode = MODE_CLI;
  waiting = use_crsf ? waiting : false;
  cli_len = 0;
  drain_input();
  // '#' шлём один, без \r\n и не сразу: перед входом в CLI на порту нужна тишина
  hash_pending = true;
  hash_due_ms = millis() + CLI_HASH_DELAY_MS;
  cli_last_rx_ms = millis();
}

inline void leave_cli() {
  mode = MODE_MSP;
  if (!use_crsf) waiting = false;
  cli_len = 0;
  hash_pending = false;
  consec_timeouts = 0;
  rx_since_ok = 0;
  exit_tries = 0;
  uint32_t now = millis();
  last_recover_ms = now;   // после выхода из CLI FC ~4 с перезагружается: не считать это "залипанием"
  last_exit_ms = now;
  drain_input();
}

inline void on_client_lost() {
  if (mode == MODE_CLI) {
    FC_SERIAL.print("\r\nexit\r\n");
    leave_cli();
  }
}

/**
 * @brief Обработать сообщение GUI. true - сообщение предназначалось этому модулю.
 */
inline bool apply_client_message(const String& text) {
  if (text.indexOf("\"cli\"") < 0 && text.indexOf("\"msp_req\"") < 0) return false;

  StaticJsonDocument<384> doc;
  if (deserializeJson(doc, text)) return true;

  if (doc.containsKey("msp_req")) {
    int c = doc["msp_req"] | 0;
    // по CRSF MSP-запросы идут и во время CLI; по UART3 - только вне CLI
    if (command_allowed(c) && (use_crsf || mode == MODE_MSP)) queue_push((uint8_t)c);
    return true;
  }

  const char* action = doc["cli"] | "";
  if (strcmp(action, "enter") == 0) {
    enter_cli();
  } else if (strcmp(action, "leave") == 0) {
    leave_cli();
  } else if (strcmp(action, "line") == 0 && mode == MODE_CLI) {
    const char* t = doc["text"] | "";
    FC_SERIAL.print(t);
    FC_SERIAL.print("\r\n");
  }
  return true;
}

inline void pump_cli(WiFiClient& c, uint32_t now) {
  if (hash_pending && now >= hash_due_ms) {
    FC_SERIAL.write('#');
    hash_pending = false;
  }
  while (FC_SERIAL.available()) {
    int b = FC_SERIAL.read();
    if (b < 0) break;
    d_rx_bytes++;
    char ch = (char)b;
    bool printable = (b >= 32 && b < 127) || b == '\n' || b == '\r' || b == '\t';
    cli_buf[cli_len++] = printable ? ch : '?';
    cli_last_rx_ms = now;
    if (cli_len >= sizeof(cli_buf) - 1) flush_cli(c);
  }
  if (cli_len > 0 && now - cli_last_rx_ms >= CLI_IDLE_FLUSH_MS) flush_cli(c);
}

inline void send_exit(uint32_t now) {
  FC_SERIAL.print("\r\nexit\r\n");
  d_recover++;
  last_exit_ms = now;
  consec_timeouts = 0;
  rx_since_ok = 0;
  waiting = false;
  cli_prompt_seen = false;
}

// ---------- MSP по CRSF (линия Serial2) ----------
inline void crsf_poll(WiFiClient& c, uint32_t now) {
  msp_crsf::Resp r;
  while (msp_crsf::take(r)) {
    p.cmd = r.cmd;
    p.len = r.len;
    p.err = r.err;
    memcpy(p.buf, r.data, r.len);
    d_frames++;
    if (p.err) d_err++;
    consec_timeouts = 0;
    emit_frame(c);
    waiting = false;
  }

  if (waiting && now - req_ms > MSP_RESP_TIMEOUT_MS) {
    waiting = false;
    d_timeout++;
    consec_timeouts++;
  }

  // ни одного ответа по CRSF -> телеметрия на FC не включена / нет поддержки: переходим на MSP по UART3
  if (msp_crsf::d_req >= MSP_CRSF_FALLBACK_REQS && msp_crsf::d_resp == 0) {
    use_crsf = false;
    waiting = false;
    drain_input();
    return;
  }

  if (!waiting && now - last_req_ms >= MSP_POLL_PERIOD_MS) {
    msp_crsf::send_request(next_poll_cmd());
    d_tx++;
    waiting = true;
    req_ms = now;
    last_req_ms = now;
  }
}

// ---------- MSP по UART3 (запасной вариант) ----------
inline void uart3_poll(WiFiClient& c, uint32_t now) {
  while (FC_SERIAL.available()) {
    uint8_t b = (uint8_t)FC_SERIAL.read();
    d_rx_bytes++;
    rx_since_ok++;
    tail3[0] = tail3[1]; tail3[1] = tail3[2]; tail3[2] = b;
    if (tail3[0] == '\n' && tail3[1] == '#' && tail3[2] == ' ') cli_prompt_seen = true;
    if (feed(b)) {
      d_frames++;
      if (p.err) d_err++;
      consec_timeouts = 0;
      rx_since_ok = 0;
      exit_tries = 0;
      cli_prompt_seen = false;
      emit_frame(c);
      waiting = false;
    }
  }

  if (waiting && now - req_ms > MSP_RESP_TIMEOUT_MS) {
    waiting = false;
    d_timeout++;
    consec_timeouts++;
  }

#if MSP_AUTO_RECOVER
  if (cli_prompt_seen && exit_tries < MSP_CLI_EXIT_MAX_TRIES && now - last_exit_ms >= MSP_CLI_EXIT_PERIOD_MS) {
    exit_tries++;
    send_exit(now);
    return;
  }
  if (consec_timeouts >= MSP_RECOVER_TIMEOUTS && rx_since_ok >= MSP_RECOVER_MIN_BYTES &&
      now - last_recover_ms >= MSP_RECOVER_PERIOD_MS) {
    last_recover_ms = now;
    send_exit(now);
    return;
  }
#endif

  if (!waiting && now - last_req_ms >= MSP_POLL_PERIOD_MS) {
    send_request_uart3(next_poll_cmd());
    waiting = true;
    req_ms = now;
    last_req_ms = now;
  }
}

/**
 * @brief Вызывать из loop() на каждой итерации.
 * @param c WebSocket-клиент GUI
 * @param ws_ready true, если GUI подключён и handshake выполнен
 */
inline void poll(WiFiClient& c, bool ws_ready) {
  uint32_t now = millis();

  if (!ws_ready) {
    if (mode == MODE_CLI) on_client_lost();
    drain_input();
    waiting = false;
    msp_crsf::resp.ready = false;
    return;
  }

  if (now - last_diag_ms >= MSP_DIAG_PERIOD_MS) {
    last_diag_ms = now;
    emit_diag(c);
  }

  if (use_crsf) {
    if (mode == MODE_CLI) pump_cli(c, now);
    else drain_input();           // UART3 вне CLI не используется
    crsf_poll(c, now);
    return;
  }

  if (mode == MODE_CLI) {
    pump_cli(c, now);
    return;
  }
  uart3_poll(c, now);
}

} // namespace msp
