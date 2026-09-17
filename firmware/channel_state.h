/*
  channel_state.h — единственное место, где хранится "источник истины"
  по 16 каналам (rc_channels_us), плюс перевод JSON <-> этот массив.

  Раньше глобальный массив rc_channels_us и функции его чтения/записи
  из JSON были в том же файле, что transport и CRSF-протокол. Теперь
  это отдельный, маленький и легко читаемый модуль — "модель данных"
  проекта. И ws_transport, и crsf_protocol используют только этот
  массив, не зная друг о друге напрямую.
*/

#pragma once

#include <Arduino.h>
#include <ArduinoJson.h>
#include "config.h"

namespace state {

static uint16_t rc_channels_us[TOTAL_CHANNELS];

inline void init_defaults() {
    rc_channels_us[ROLL] = 1500;
    rc_channels_us[PITCH] = 1500;
    rc_channels_us[YAW] = 1500;
    rc_channels_us[THROTTLE] = 1000;
    for (int i = AUX_START; i < TOTAL_CHANNELS; i++) rc_channels_us[i] = 1000;
}

// Собирает {"channels": [16 значений]} — отправляется GUI при подключении
// и после каждого принятого сообщения.
inline String build_state_json() {
    StaticJsonDocument<400> doc;
    JsonArray arr = doc.createNestedArray("channels");
    for (int i = 0; i < TOTAL_CHANNELS; i++) arr.add(rc_channels_us[i]);
    String out;
    serializeJson(doc, out);
    return out;
}

// Разбирает {"channel": 1..16, "value": 1000..2000} от GUI и обновляет
// массив каналов. Возвращает true, если что-то реально изменилось.
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
