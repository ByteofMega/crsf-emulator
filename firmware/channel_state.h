/**
 * @file channel_state.h
 * @brief Модель данных: массив текущих значений 16 каналов
 *        (rc_channels_us) плюс перевод JSON <-> этот массив.
 *
 * Единственное место, где хранится "источник истины" по каналам.
 * И ws_transport, и crsf_protocol используют только этот массив, не
 * зная друг о друге напрямую.
 */

#pragma once

#include <Arduino.h>
#include <ArduinoJson.h>
#include "config.h"

namespace state {

/** Текущие значения всех 16 каналов в микросекундах (1000..2000). */
static uint16_t rc_channels_us[TOTAL_CHANNELS];

/**
 * @brief Установить значения всех каналов по умолчанию при старте
 *        прошивки: центр для ROLL/PITCH/YAW, минимум для THROTTLE и
 *        минимум для всех AUX-каналов (безопасное состояние перед армингом).
 *
 * @param нет аргументов.
 * @return void. Результат — заполненный глобальный массив rc_channels_us.
 */
inline void init_defaults() {
    rc_channels_us[ROLL] = 1500;
    rc_channels_us[PITCH] = 1500;
    rc_channels_us[YAW] = 1500;
    rc_channels_us[THROTTLE] = 1000;
    for (int i = AUX_START; i < TOTAL_CHANNELS; i++) rc_channels_us[i] = 1000;
}

/**
 * @brief Собрать JSON-строку с текущим состоянием всех каналов для
 *        отправки в GUI (при подключении и после каждого изменения).
 *
 * @param нет аргументов (читает глобальный rc_channels_us).
 * @return String JSON вида {"channels": [v0, v1, ..., v15]}, где vN —
 *                значение канала N в микросекундах.
 */
inline String build_state_json() {
    StaticJsonDocument<400> doc;
    JsonArray arr = doc.createNestedArray("channels");
    for (int i = 0; i < TOTAL_CHANNELS; i++) arr.add(rc_channels_us[i]);
    String out;
    serializeJson(doc, out);
    return out;
}

/**
 * @brief Разобрать сообщение от GUI вида {"channel": N, "value": V} и,
 *        если оно валидно, обновить соответствующий канал в rc_channels_us.
 *
 * @param text Сырая JSON-строка, полученная по WebSocket от GUI.
 * @return bool true, если сообщение успешно разобрано, канал (1..16) и
 *              значение (1000..2000) прошли валидацию и массив каналов
 *              был обновлён; false при ошибке разбора JSON или выходе
 *              значений за допустимые границы (в этом случае состояние
 *              не меняется).
 */
inline bool apply_client_message(const String& text) {
    StaticJsonDocument<128> doc;
    DeserializationError err = deserializeJson(doc, text);
    if (err) return false;

    int channel = doc["channel"] | 0;
    int value = doc["value"] | 0;

    if (channel >= 1 && channel <= TOTAL_CHANNELS && value >= 1000 && value <= 2000) {
        rc_channels_us[channel - 1] = (uint16_t)value;
        Serial.printf("%s = %d мкс (по WebSocket)\n", CHANNEL_NAMES[channel - 1], value);
        return true;
    }
    return false;
}

}  // namespace state
