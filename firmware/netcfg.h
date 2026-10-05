/*
 * ДОКУМЕНТАЦИЯ ФАЙЛА netcfg.h
 * Хранение и применение настроек STA-сети Wi-Fi: чтение и запись в NVS, приём новых данных от GUI, флаг запроса на переподключение.
 *
 * ФУНКЦИИ:
 * netcfg::load_saved()
 *     Читает SSID, identity, логин, пароль и тип сети из NVS; при отсутствии берёт значения по
 *     умолчанию из config.h.
 * netcfg::save(const Credentials& c)
 *     Записывает настройки сети в NVS.
 * netcfg::apply_client_message(const String& text)
 *     Если сообщение содержит ключ «wifi», сохраняет новые данные и ставит флаг переподключения;
 *     true, если сообщение было настройкой сети.
 * netcfg::consume_reconnect_request()
 *     Возвращает true один раз после получения новых настроек и сбрасывает флаг.
 */

/**
 * @file netcfg.h
 * @brief Хранение и приём по WebSocket данных Wi-Fi сети (STA), к которой
 *        должен подключаться ESP32 для интернета.
 *
 * собственная точка доступа ESP32 (FOTON-LINK)
 * поднимается независимо от того, удалось ли STA подключиться к
 * какой-либо сети. Поэтому GUI, уже подключённый к FOTON-LINK по
 * WebSocket, может в любой момент прислать новые сетевые настройки
 * (SSID/identity/username/password) — ESP32 сохранит их в NVS
 * (энергонезависимая память, переживает перезагрузки) и попробует
 * подключиться к ним, не трогая точку доступа для GUI.
 *
 * Формат сообщения от GUI (JSON по тому же WebSocket, что и каналы):
 *   {"wifi": {"ssid": "...", "identity": "...", "username": "...", "password": "..."}}
 *
 * Если сеть открытая (не WPA2-Enterprise, обычный пароль) — оставьте
 * identity/username пустыми, в .ino это интерпретируется как обычный
 * WiFi.begin(ssid, password) без EAP.
 */
#pragma once

#include <ArduinoJson.h>
#include <Preferences.h>
#include <WiFi.h>
#include "config.h"

namespace netcfg {

struct Credentials {
  String ssid;
  String identity;
  String username;
  String password;
  bool enterprise;  // true = WPA2-Enterprise (EAP), false = обычный WPA2-Personal
};

static Preferences prefs;
static Credentials current;
static bool reconnect_requested = false;

/**
 * @brief Загрузить сохранённые сетевые настройки из NVS, либо значения
 *        по умолчанию из config.h, если ничего ещё не сохранено.
 */
inline Credentials load_saved() {
  prefs.begin("wifi", true);
  Credentials c;
  c.ssid = prefs.getString("ssid", WIFI_SSID);
  c.identity = prefs.getString("identity", WIFI_EAP_IDENTITY);
  c.username = prefs.getString("username", WIFI_EAP_USERNAME);
  c.password = prefs.getString("password", WIFI_EAP_PASSWORD);
  c.enterprise = prefs.getBool("enterprise", true);
  prefs.end();
  return c;
}

/**
 * @brief Сохранить сетевые настройки в NVS — переживают перезагрузку.
 */
inline void save(const Credentials& c) {
  prefs.begin("wifi", false);
  prefs.putString("ssid", c.ssid);
  prefs.putString("identity", c.identity);
  prefs.putString("username", c.username);
  prefs.putString("password", c.password);
  prefs.putBool("enterprise", c.enterprise);
  prefs.end();
}

/**
 * @brief Разобрать входящее WS-сообщение; если это конфиг сети — сохранить
 *        и выставить флаг reconnect_requested.
 *
 * Аргументы:
 *     text (String): сырой текст, пришедший по WebSocket.
 *
 * Возвращает:
 *     bool: true, если сообщение было распознано как конфиг сети
 *     (независимо от валидности содержимого), false — если ключа "wifi"
 *     в сообщении не было (тогда это, скорее всего, сообщение про канал,
 *     и его должен обработать channel_state.h).
 */
inline bool apply_client_message(const String& text) {
  StaticJsonDocument<320> doc;
  if (deserializeJson(doc, text)) return false;
  if (!doc.containsKey("wifi")) return false;

  JsonObject w = doc["wifi"];
  Credentials c;
  c.ssid = String((const char*)(w["ssid"] | ""));
  c.identity = String((const char*)(w["identity"] | ""));
  c.username = String((const char*)(w["username"] | ""));
  c.password = String((const char*)(w["password"] | ""));
  c.enterprise = w["enterprise"] | true;

  if (c.ssid.length() == 0) {
    Serial.println("netcfg: получено пустое имя сети (ssid) - игнорирую.");
    return true;  // сообщение опознано, но данные некорректны
  }
  if (c.enterprise && c.identity.length() == 0) {
    c.identity = c.username;  // identity можно не указывать явно
  }

  save(c);
  current = c;
  reconnect_requested = true;
  Serial.printf("netcfg: новые настройки сети сохранены (SSID=\"%s\"), запрошено переподключение.\n",
                c.ssid.c_str());
  return true;
}

/**
 * @brief Проверить и сбросить флаг "нужно переподключить STA".
 *
 * Возвращает:
 *     bool: true один раз после apply_client_message() с валидным SSID,
 *     затем false, пока не придёт новое сообщение "wifi".
 */
inline bool consume_reconnect_request() {
  if (!reconnect_requested) return false;
  reconnect_requested = false;
  return true;
}

}  // namespace netcfg
