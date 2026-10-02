/**
 * @file channel_state.h
 * @brief Модель данных каналов (GUI -> FC) + JSON.
 *
 * Обновлено: apply_client_message() теперь сначала проверяет, не
 * является ли сообщение конфигом сети (см. netcfg.h, ключ "wifi") —
 * если да, отдаёт обработку туда и не трогает каналы. Это позволяет
 * слать по одному и тому же WebSocket и команды каналов, и новые
 * настройки Wi-Fi, без конфликта форматов.
 */
#pragma once
#include <ArduinoJson.h>
#include <Arduino.h>
#include "config.h"
#include "netcfg.h"
#include "msp_bridge.h"

namespace state {

static uint16_t rc_channels_us[TOTAL_CHANNELS];

inline void init_defaults() {
  rc_channels_us[ROLL] = 1500;
  rc_channels_us[PITCH] = 1500;
  rc_channels_us[YAW] = 1500;
  rc_channels_us[THROTTLE] = 1000;
  for (int i = AUX_START; i < TOTAL_CHANNELS; i++) rc_channels_us[i] = 1000;
}

inline String build_state_json() {
  StaticJsonDocument<400> doc;
  JsonArray arr = doc.createNestedArray("channels");
  for (int i = 0; i < TOTAL_CHANNELS; i++) arr.add(rc_channels_us[i]);
  String out;
  serializeJson(doc, out);
  return out;
}

inline bool apply_client_message(const String& text) {
  // Сначала пробуем разобрать как конфиг сети - если это он, каналы не трогаем.
  if (netcfg::apply_client_message(text)) {
    return false;  // состояние каналов не изменилось, лишний build_state_json() не нужен
  }

  // Сообщения "cli" / "msp_req" обрабатывает msp_bridge.h (MSP и CLI полётного контроллера)
  if (msp::apply_client_message(text)) {
    return false;
  }

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
