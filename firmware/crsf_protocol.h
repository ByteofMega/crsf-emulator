/**
 * @file crsf_protocol.h
 * @brief Кодирование протокола CRSF/ELRS: CRC8, упаковка каналов, сборка
 *        и отправка кадра RC_CHANNELS по UART.
 *
 * Модуль ничего не знает про WebSocket или JSON — он работает только с
 * массивом uint16_t rc_channels_us[TOTAL_CHANNELS], который ему передают.
 * Функции объявлены как inline, а таблица crc8_table — как static, так
 * как файл подключается только из .ino (одна единица трансляции) —
 * без риска ошибок компоновки "multiple definition".
 */

#pragma once

#include <Arduino.h>
#include "config.h"

namespace crsf {

/** Таблица предвычисленных значений CRC8 (полином 0xD5, DVB-S2). */
static uint8_t crc8_table[256];

/**
 * @brief Заполнить таблицу crc8_table значениями CRC8 (DVB-S2) для всех
 *        256 возможных байт. Вызывается один раз в setup().
 *
 * @param нет аргументов.
 * @return void. Результат — заполненный глобальный массив crc8_table.
 */
inline void build_crc8_table() {
    for (int i = 0; i < 256; i++) {
        uint8_t crc = (uint8_t)i;
        for (int b = 0; b < 8; b++) {
            crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0xD5) : (uint8_t)(crc << 1);
        }
        crc8_table[i] = crc;
    }
}

/**
 * @brief Посчитать контрольную сумму CRC8 (DVB-S2) для блока байт по
 *        предвычисленной таблице crc8_table.
 *
 * @param data Указатель на начало блока байт, для которого считается CRC.
 * @param len  Количество байт в блоке data.
 * @return uint8_t Итоговое значение контрольной суммы (1 байт).
 */
inline uint8_t crc8_dvb_s2(const uint8_t* data, size_t len) {
    uint8_t crc = 0;
    for (size_t i = 0; i < len; i++) crc = crc8_table[crc ^ data[i]];
    return crc;
}

#define CRSF_SYNC_BYTE 0xC8
#define CRSF_FRAMETYPE_RC_CHANNELS 0x16
#define CRSF_MID 992

/**
 * @brief Перевести значение канала из микросекунд (1000..2000) в
 *        11-битное значение формата CRSF (0..2047, центр = 992).
 *
 * @param us Значение канала в микросекундах. Обрезается до диапазона
 *           [1000, 2000], если выходит за его пределы.
 * @return uint16_t 11-битное значение канала для передачи в кадре CRSF.
 */
inline uint16_t us_to_crsf(int us) {
    if (us < 1000) us = 1000;
    if (us > 2000) us = 2000;
    float value = (us - 1500) * 8.0f / 5.0f + CRSF_MID;
    return (uint16_t)lroundf(value);
}

/**
 * @brief Упаковать TOTAL_CHANNELS (16) каналов по 11 бит каждый в плотный
 *        битовый поток (используется CRSF для кадра RC_CHANNELS).
 *
 * @param channels Массив из TOTAL_CHANNELS значений (каждое 0..2047),
 *                 обычно результат вызова us_to_crsf() для всех каналов.
 * @param out      Буфер для упакованных байт; должен вмещать минимум
 *                 22 байта (16 каналов * 11 бит / 8 бит на байт).
 * @return void. Результат записывается в буфер out.
 */
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

/**
 * @brief Собрать полный CRSF-кадр RC_CHANNELS из текущих значений каналов
 *        (в микросекундах) и отправить его по UART на полётный контроллер.
 *
 * Последовательность: перевод мкс -> 11 бит для каждого канала, упаковка
 * в 22 байта, добавление байта типа кадра, расчёт CRC8, сборка полного
 * кадра (sync + length + type + payload + crc) и запись в CRSF_SERIAL.
 *
 * @param rc_channels_us Массив из TOTAL_CHANNELS текущих значений каналов
 *                        в микросекундах (1000..2000). Обычно это
 *                        state::rc_channels_us из channel_state.h.
 * @return void. Кадр записывается напрямую в UART (CRSF_SERIAL.write()).
 */
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
