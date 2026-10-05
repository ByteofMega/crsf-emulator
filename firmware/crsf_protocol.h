/*
 * ДОКУМЕНТАЦИЯ ФАЙЛА crsf_protocol.h
 * Протокол CRSF для передачи RC-каналов в FC: контрольная сумма, упаковка каналов, сборка и отправка кадра.
 *
 * ФУНКЦИИ:
 * crsf::build_crc8_table()
 *     Строит таблицу CRC8 с полиномом 0xD5 (DVB-S2); вызывается один раз в setup().
 * crsf::crc8_dvb_s2(const uint8_t* data, size_t len)
 *     Считает CRC8 по массиву байт с помощью таблицы.
 * crsf::us_to_crsf(int us)
 *     Переводит длительность импульса 1000-2000 мкс в значение канала CRSF (центр 992).
 * crsf::pack_channels_11bit(const uint16_t* channels, uint8_t* out)
 *     Упаковывает 16 каналов по 11 бит (младшими битами вперёд) в 22 байта полезной нагрузки.
 * crsf::send_crsf_frame(const uint16_t* rc_channels_us)
 *     Собирает кадр RC_CHANNELS (адрес 0xC8, тип 0x16, 22 байта данных, CRC8) и отправляет его в
 *     Serial2.
 */

/**
 * @file crsf_protocol.h
 * @brief CRC8, упаковка каналов, сборка и отправка кадра RC_CHANNELS по UART.
 */
#pragma once
#include <Arduino.h>
#include "config.h"

namespace crsf {

static uint8_t crc8_table[256];

inline void build_crc8_table() {
    for (int i = 0; i < 256; i++) {
        uint8_t crc = (uint8_t)i;
        for (int b = 0; b < 8; b++) {
            crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0xD5) : (uint8_t)(crc << 1);
        }
        crc8_table[i] = crc;
    }
}

inline uint8_t crc8_dvb_s2(const uint8_t* data, size_t len) {
    uint8_t crc = 0;
    for (size_t i = 0; i < len; i++) crc = crc8_table[crc ^ data[i]];
    return crc;
}

#define CRSF_SYNC_BYTE 0xC8
#define CRSF_FRAMETYPE_RC_CHANNELS 0x16
#define CRSF_MID 992

inline uint16_t us_to_crsf(int us) {
    if (us < 1000) us = 1000;
    if (us > 2000) us = 2000;
    float value = (us - 1500) * 8.0f / 5.0f + CRSF_MID;
    return (uint16_t)lroundf(value);
}

inline void pack_channels_11bit(const uint16_t* channels, uint8_t* out) {
    uint32_t bit_buffer = 0;
    int bit_count = 0;
    int out_index = 0;
    for (int i = 0; i < TOTAL_CHANNELS; i++) {
        bit_buffer |= ((uint32_t)(channels[i] & 0x7FF)) << bit_count;
        bit_count += 11;
        while (bit_count >= 8) {
            out[out_index++] = (uint8_t)(bit_buffer & 0xFF);
            bit_buffer >>= 8;
            bit_count -= 8;
        }
    }
    if (bit_count > 0) out[out_index++] = (uint8_t)(bit_buffer & 0xFF);
}

inline void send_crsf_frame(const uint16_t* rc_channels_us) {
    uint16_t crsf_values[TOTAL_CHANNELS];
    for (int i = 0; i < TOTAL_CHANNELS; i++) crsf_values[i] = us_to_crsf(rc_channels_us[i]);

    uint8_t payload[22];
    pack_channels_11bit(crsf_values, payload);

    uint8_t type_and_payload[23];
    type_and_payload[0] = CRSF_FRAMETYPE_RC_CHANNELS;
    memcpy(&type_and_payload[1], payload, 22);

    uint8_t crc = crc8_dvb_s2(type_and_payload, sizeof(type_and_payload));
    uint8_t length = sizeof(type_and_payload) + 1;

    uint8_t frame[26];
    size_t idx = 0;
    frame[idx++] = CRSF_SYNC_BYTE;
    frame[idx++] = length;
    memcpy(&frame[idx], type_and_payload, sizeof(type_and_payload));
    idx += sizeof(type_and_payload);
    frame[idx++] = crc;

    CRSF_SERIAL.write(frame, idx);
}

}  // namespace crsf
