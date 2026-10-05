/*
 * ДОКУМЕНТАЦИЯ ФАЙЛА msp_crsf.h
 * MSP внутри кадров CRSF по линии, которая уже передаёт RC-каналы (формат сверен с msp_shared.c Betaflight).
 *
 * ФУНКЦИИ:
 * msp_crsf::note_frame(uint8_t type)
 *     Считает каждый корректный кадр CRSF, принятый от FC (диагностика: идёт ли вообще
 *     телеметрия).
 * msp_crsf::send_request(uint8_t cmd)
 *     Отправляет в FC кадр MSP_REQ (0x7A) с MSP v1 без данных: C8 07 7A C8 EA статус 00 команда
 *     CRC8.
 * msp_crsf::on_response(const uint8_t* p, uint8_t len)
 *     Разбирает кадр MSP_RESP (0x7B): проверяет флаг начала и версию, извлекает команду, данные и
 *     признак ошибки; сохраняет ответ для msp_bridge.
 * msp_crsf::take(Resp& out)
 *     Отдаёт принятый ответ (если есть) и освобождает слот.
 */

/**
 * @file msp_crsf.h
 * @brief MSP внутри кадров CRSF (линия ESP32 Serial2 <-> UART1 FC, та же, что передаёт RC-каналы).
 *
 * Формат сверен с исходниками Betaflight (telemetry/msp_shared.c) и Lua-скриптом пульта (MSP/crsf.lua):
 *
 * Запрос (ESP32 -> FC):  C8 | 07 | 7A | C8 | EA | статус | размер | функция | CRC8
 *   7A  - MSP_REQ; C8 - адрес FC (кому); EA - адрес "пульта" (от кого; ответ придёт на этот адрес);
 *   статус = 0x10 (бит 4: начало MSP-кадра) | 0x20 (биты 5-6 = 1: MSP v1) | номер кадра (биты 0-3);
 *   размер = 0 (запрос без данных), функция = код MSP. MSP-заголовок "$M<" и контрольная сумма MSP не передаются.
 *   ВАЖНО: версия в битах 5-6 должна быть 1. Значение 0 или 2 FC разбирает как MSP v2 и не ответит.
 * Ответ (FC -> ESP32):   C8 | длина | 7B | EA | C8 | статус | размер | функция | данные... | CRC8
 *   статус ответа повторяет версию запроса; бит 7 = ошибка; бит 4 = первый кусок.
 *   Читается в telemetry.h (handle_frame) и передаётся сюда в on_response().
 * Обрабатываются только ответы, уместившиеся в один кусок (до 57 байт) - достаточно для опрашиваемых команд.
 *
 * Для работы на FC должна быть включена телеметрия CRSF и собрана поддержка MSP over telemetry.
 */
#pragma once
#include <Arduino.h>
#include "config.h"
#include "crsf_protocol.h"

#define CRSF_FRAMETYPE_MSP_REQ   0x7A
#define CRSF_FRAMETYPE_MSP_RESP  0x7B
#define CRSF_ADDR_FC             0xC8
#define CRSF_ADDR_HANDSET        0xEA

#define MSP_CRSF_STATUS_START    0x10
#define MSP_CRSF_STATUS_V1       0x20

namespace msp_crsf {

struct Resp {
  bool ready;
  uint8_t cmd;
  uint8_t len;
  bool err;
  uint8_t data[64];
};

static Resp resp = { false, 0, 0, false, { 0 } };
static uint8_t seq = 0;
static uint32_t d_req = 0;    // отправлено MSP-запросов по CRSF
static uint32_t d_resp = 0;   // принято MSP-ответов по CRSF
static uint32_t d_bad = 0;    // кадров 0x7B, которые не удалось разобрать
static uint32_t d_rx = 0;     // всего корректных кадров CRSF от FC (телеметрия и т.д.)

inline void note_frame(uint8_t /*type*/) {
  d_rx++;
}

inline void send_request(uint8_t cmd) {
  uint8_t f[9];
  f[0] = CRSF_SYNC_BYTE;
  f[1] = 7;                       // type + dest + origin + status + size + function + crc
  f[2] = CRSF_FRAMETYPE_MSP_REQ;
  f[3] = CRSF_ADDR_FC;
  f[4] = CRSF_ADDR_HANDSET;
  f[5] = (uint8_t)(MSP_CRSF_STATUS_START | MSP_CRSF_STATUS_V1 | (seq & 0x0F));
  f[6] = 0;                       // размер данных MSP
  f[7] = cmd;                     // функция MSP
  f[8] = crsf::crc8_dvb_s2(&f[2], 6);
  CRSF_SERIAL.write(f, sizeof(f));
  seq++;
  d_req++;
}

// payload: [dest][origin][статус][размер][функция][данные...]
inline void on_response(const uint8_t* p, uint8_t len) {
  if (len < 5) { d_bad++; return; }
  uint8_t status = p[2];
  if (!(status & MSP_CRSF_STATUS_START)) { d_bad++; return; }   // не первый кусок - не поддерживается
  if (((status >> 5) & 0x03) != 1) { d_bad++; return; }          // ответ не в формате MSP v1
  uint8_t size = p[3];
  uint8_t func = p[4];
  if (size == 0xFF || size > sizeof(resp.data) || (uint16_t)5 + size > len) { d_bad++; return; }
  resp.cmd = func;
  resp.len = size;
  resp.err = (status & 0x80) != 0;
  if (size) memcpy(resp.data, &p[5], size);
  resp.ready = true;
  d_resp++;
}

inline bool take(Resp& out) {
  if (!resp.ready) return false;
  out = resp;
  resp.ready = false;
  return true;
}

} // namespace msp_crsf
