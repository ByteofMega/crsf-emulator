/*
  config.h — все настройки и константы прошивки в одном месте.

  Раньше Wi-Fi credentials, пины UART, параметры CRSF и таблица имен
  каналов были разбросаны по всему .ino вперемешку с логикой протокола.
  Теперь это единственный файл, который нужно открыть, чтобы поменять
  SSID/пароль, пины или частоту обновления — остальной код их не хранит,
  а только использует.
*/

#pragma once

#include <Arduino.h>

// ---------- Wi-Fi ----------
static const char* WIFI_SSID = "ваш_SSID";
static const char* WIFI_PASSWORD = "ваш_пароль";

// ---------- WebSocket ----------
#define WS_PORT 81
#define WS_MAX_PAYLOAD 512

// Мощность передатчика Wi-Fi (ограничена "на всякий случай";
// можно вернуть WIFI_POWER_19_5dBm для максимальной дальности).
#define WIFI_TX_POWER WIFI_POWER_15dBm

// ---------- UART к полетному контроллеру (CRSF) ----------
#define CRSF_SERIAL Serial2
#define CRSF_BAUDRATE 420000
#define CRSF_RX_PIN 16
#define CRSF_TX_PIN 17

#define UPDATE_RATE_HZ 250
#define FRAME_PERIOD_MS 4

// ---------- Каналы ----------
#define NUM_MAIN_CHANNELS 4
#define NUM_AUX_CHANNELS 12
#define TOTAL_CHANNELS (NUM_MAIN_CHANNELS + NUM_AUX_CHANNELS)  // 16

enum { ROLL = 0, PITCH = 1, THROTTLE = 2, YAW = 3, AUX_START = 4 };

static const char* CHANNEL_NAMES[TOTAL_CHANNELS] = {
    "ROLL", "PITCH", "THROTTLE", "YAW",
    "AUX1", "AUX2", "AUX3", "AUX4", "AUX5", "AUX6",
    "AUX7", "AUX8", "AUX9", "AUX10", "AUX11", "AUX12"
};
