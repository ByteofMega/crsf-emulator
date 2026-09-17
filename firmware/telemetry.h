/**
 * @file telemetry.h
 * @brief Приём телеметрии от полётного контроллера по тому же UART
 *        (Serial2), по которому отправляются RC-каналы.
 *
 * CRSF — двунаправленный протокол: FC сам присылает кадры телеметрии
 * (батарея, углы, режим полёта, статистика линка, GPS) через тот же
 * провод RX2 ESP32 <- TX FC. Модуль читает байты из CRSF_SERIAL по
 * кадрам (sync + length + type + payload + crc8), разбирает несколько
 * самых полезных типов и хранит последние значения в telemetry::state.
 *
 * ВАЖНО: единицы измерения (deciVolt/deciAmp и т.п.) для батареи взяты
 * из типичных реализаций CRSF/Betaflight. Если цифры на GUI выглядят
 * в 10 раз больше/меньше реальных — поправьте делители VOLTAGE_DIV /
 * CURRENT_DIV ниже под вашу прошивку FC.
 */

#pragma once

#include <Arduino.h>
#include <ArduinoJson.h>
#include "config.h"
#include "crsf_protocol.h"  // переиспользуем crsf::crc8_dvb_s2 / crc8_table

namespace telemetry {

#define CRSF_FRAMETYPE_GPS 0x02
#define CRSF_FRAMETYPE_BATTERY 0x08
#define CRSF_FRAMETYPE_LINK_STATISTICS 0x14
#define CRSF_FRAMETYPE_ATTITUDE 0x1E
#define CRSF_FRAMETYPE_FLIGHT_MODE 0x21

#define VOLTAGE_DIV 10.0f       ///< сырое значение в 0.1 В -> Вольты
#define CURRENT_DIV 10.0f       ///< сырое значение в 0.1 А -> Амперы
#define ATTITUDE_DIV 10000.0f   ///< сырое значение в радианах*10000 -> радианы

/** Последние принятые значения телеметрии по каждой категории. */
struct State {
    bool has_battery = false;
    float voltage_v = 0;
    float current_a = 0;
    uint32_t capacity_mah = 0;
    uint8_t battery_pct = 0;

    bool has_attitude = false;
    float pitch_rad = 0, roll_rad = 0, yaw_rad = 0;

    bool has_flight_mode = false;
    char flight_mode[16] = "";

    bool has_link_stats = false;
    uint8_t uplink_rssi1 = 0, uplink_lq = 0;
    int8_t uplink_snr = 0;
};

/** Глобальное хранилище последних значений телеметрии. */
static State state;

/**
 * @brief Прочитать 16-битное знаковое число в формате big-endian.
 * @param p Указатель на первый (старший) байт числа.
 * @return int16_t Прочитанное значение.
 */
inline int16_t read_i16(const uint8_t* p) { return (int16_t)((p[0] << 8) | p[1]); }

/**
 * @brief Прочитать 16-битное беззнаковое число в формате big-endian.
 * @param p Указатель на первый (старший) байт числа.
 * @return uint16_t Прочитанное значение.
 */
inline uint16_t read_u16(const uint8_t* p) { return (uint16_t)((p[0] << 8) | p[1]); }

/**
 * @brief Прочитать 24-битное беззнаковое число в формате big-endian.
 * @param p Указатель на первый (старший) байт трёхбайтного числа.
 * @return uint32_t Прочитанное значение (используются младшие 24 бита).
 */
inline uint32_t read_u24(const uint8_t* p) { return ((uint32_t)p[0] << 16) | ((uint32_t)p[1] << 8) | p[2]; }

/**
 * @brief Разобрать payload одного CRSF-кадра телеметрии по его типу и
 *        обновить соответствующие поля в глобальном state.
 *
 * @param type    Байт типа кадра (например CRSF_FRAMETYPE_BATTERY).
 * @param payload Указатель на начало payload кадра (без sync/length/type/crc).
 * @param len     Длина payload в байтах.
 * @return void. Неизвестные типы кадров молча игнорируются (case default).
 */
inline void handle_frame(uint8_t type, const uint8_t* payload, uint8_t len) {
    switch (type) {
        case CRSF_FRAMETYPE_BATTERY:
            if (len >= 8) {
                state.voltage_v = read_i16(payload) / VOLTAGE_DIV;
                state.current_a = read_i16(payload + 2) / CURRENT_DIV;
                state.capacity_mah = read_u24(payload + 4);
                state.battery_pct = payload[7];
                state.has_battery = true;
            }
            break;

        case CRSF_FRAMETYPE_ATTITUDE:
            if (len >= 6) {
                state.pitch_rad = read_i16(payload) / ATTITUDE_DIV;
                state.roll_rad = read_i16(payload + 2) / ATTITUDE_DIV;
                state.yaw_rad = read_i16(payload + 4) / ATTITUDE_DIV;
                state.has_attitude = true;
            }
            break;

        case CRSF_FRAMETYPE_FLIGHT_MODE: {
            uint8_t n = min((uint8_t)(len), (uint8_t)(sizeof(state.flight_mode) - 1));
            memcpy(state.flight_mode, payload, n);
            state.flight_mode[n] = '\0';
            state.has_flight_mode = true;
            break;
        }

        case CRSF_FRAMETYPE_LINK_STATISTICS:
            if (len >= 4) {
                state.uplink_rssi1 = payload[0];
                state.uplink_lq = payload[2];
                state.uplink_snr = (int8_t)payload[3];
                state.has_link_stats = true;
            }
            break;

        default:
            break;  // остальные типы (GPS и т.д.) можно добавить по той же схеме
    }
}

/**
 * @brief Прочитать все доступные байты из CRSF_SERIAL и собрать их в
 *        полные CRSF-кадры, проверяя CRC и вызывая handle_frame() для
 *        каждого успешно собранного кадра.
 *
 * Использует статический буфер и индекс между вызовами, поэтому
 * рассчитан на частый вызов из loop() небольшими порциями, а не на
 * блокирующее ожидание целого кадра.
 *
 * @param нет аргументов.
 * @return void. Результат — обновлённый telemetry::state при получении
 *              валидных кадров.
 */
inline void poll() {
    static uint8_t buf[64];
    static size_t idx = 0;

    while (CRSF_SERIAL.available()) {
        uint8_t b = CRSF_SERIAL.read();

        if (idx == 0) {
            if (b != CRSF_SYNC_BYTE) continue;  // ищем начало кадра
            buf[idx++] = b;
            continue;
        }

        buf[idx++] = b;

        if (idx == 2) continue;  // ждем байт length
        uint8_t length = buf[1];  // type + payload + crc
        if (length < 2 || length > sizeof(buf) - 2) {
            idx = 0;  // некорректная длина - сброс
            continue;
        }

        if (idx == (size_t)(length + 2)) {
            // кадр собран целиком: buf[0]=sync, buf[1]=length,
            // buf[2]=type, buf[3..]=payload, buf[last]=crc
            uint8_t type = buf[2];
            uint8_t payload_len = length - 2;  // без type и crc
            uint8_t crc_received = buf[length + 1];
            uint8_t crc_calc = crsf::crc8_dvb_s2(&buf[2], length - 1);  // type+payload

            if (crc_received == crc_calc) {
                handle_frame(type, &buf[3], payload_len);
            }
            idx = 0;
        }
    }
}

/**
 * @brief Собрать JSON-строку с накопленной телеметрией для отправки в GUI.
 *
 * Каждая категория (battery/attitude/flight_mode/link) включается в
 * JSON только если для неё уже был получен хотя бы один валидный кадр
 * (флаги has_* в telemetry::state).
 *
 * @param нет аргументов (читает глобальный telemetry::state).
 * @return String JSON вида {"telemetry": {"battery": {...}, ...}}.
 */
inline String build_json() {
    StaticJsonDocument<300> doc;
    JsonObject t = doc.createNestedObject("telemetry");

    if (state.has_battery) {
        JsonObject bat = t.createNestedObject("battery");
        bat["voltage_v"] = state.voltage_v;
        bat["current_a"] = state.current_a;
        bat["capacity_mah"] = state.capacity_mah;
        bat["percent"] = state.battery_pct;
    }
    if (state.has_attitude) {
        JsonObject att = t.createNestedObject("attitude");
        att["pitch"] = state.pitch_rad;
        att["roll"] = state.roll_rad;
        att["yaw"] = state.yaw_rad;
    }
    if (state.has_flight_mode) {
        t["flight_mode"] = state.flight_mode;
    }
    if (state.has_link_stats) {
        JsonObject link = t.createNestedObject("link");
        link["rssi"] = state.uplink_rssi1;
        link["lq"] = state.uplink_lq;
        link["snr"] = state.uplink_snr;
    }

    String out;
    serializeJson(doc, out);
    return out;
}

}  // namespace telemetry
