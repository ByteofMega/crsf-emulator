/*
 * ДОКУМЕНТАЦИЯ ФАЙЛА config.h
 * Константы и настройки прошивки.
 *
 * ФУНКЦИИ:
 * Wi-Fi
 *     WIFI_SSID, WIFI_EAP_* - данные внешней сети по умолчанию (затем меняются из GUI и хранятся в
 *     NVS); AP_SSID, AP_PASS, AP_IP_* - собственная точка доступа.
 * WebSocket
 *     WS_PORT, WS_MAX_PAYLOAD - порт и максимальный размер входящего сообщения.
 * CRSF (Serial2)
 *     CRSF_SERIAL, CRSF_BAUDRATE, CRSF_RX_PIN, CRSF_TX_PIN, FRAME_PERIOD_MS - линия RC-каналов и
 *     телеметрии к UART1 FC.
 * FC_SERIAL (Serial1)
 *     FC_BAUD, FC_RX_PIN, FC_TX_PIN - линия CLI к UART3 FC (GPIO25/26).
 * Каналы
 *     NUM_MAIN_CHANNELS, NUM_AUX_CHANNELS, TOTAL_CHANNELS, перечисление ROLL/PITCH/THROTTLE/YAW,
 *     CHANNEL_NAMES.
 */

/**
 * @file config.h
 * @brief Все настройки и константы прошивки в одном месте.
 * "Настройка Wi-Fi сети ESP32" без перепрошивки платы.
 *
 * Добавлено: ESP32 всегда поднимает собственную точку доступа
 * AP_SSID/AP_PASS — ноутбук с GUI подключается СЮДА
 * напрямую (обход изоляции клиентов корпоративной сети).
 */

#pragma once

#include <Arduino.h>

// ---------- Wi-Fi: STA-сеть по умолчанию (используется при первом старте) ----------
static const char* WIFI_SSID = "YOUR_WIFI";

// Внешний EAP-идентификатор (identity) — можно оставить таким же, как логин.
<<<<<<< HEAD
static const char* WIFI_EAP_IDENTITY = "YOUR_IDENTITY";
=======
static const char* WIFI_EAP_IDENTITY = "YOUR_LOGIN";
>>>>>>> 448b1c9 (Updated GUI and firmware)

// Логин и пароль для входа в корпоративную сеть КФУ.
static const char* WIFI_EAP_USERNAME = "YOUR_LOGIN";
static const char* WIFI_EAP_PASSWORD = "YOUR_PASSWORD";

// ---------- Собственная точка доступа ESP32 для ноутбука с GUI ----------
// Ноутбук подключается сюда, — это обходит изоляцию
// клиентов корпоративной сети. Интернет на ноутбук идёт через NAT
// (WiFi.AP.enableNAPT), см. .ino.
static const char* AP_SSID = "FOTON-LINK";
static const char* AP_PASS = "YOUR_AP_PASSWORD";  // минимум 8 символов для WPA2

#define AP_IP_1 192
#define AP_IP_2 168
#define AP_IP_3 4
#define AP_IP_4 1
// IP ESP32 в собственной сети: 192.168.4.1 — именно этот адрес нужно
// вводить в поле "IP адрес ESP32" в GUI.

// ---------- WebSocket ----------
#define WS_PORT 81
#define WS_MAX_PAYLOAD 512

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

// ---------- Связь с полётным контроллером: CLI (и запасной MSP) - UART1 ESP32 на свободных GPIO ----------
// ESP32 GPIO26 (TX) -> FC RX3,   ESP32 GPIO25 (RX) <- FC TX3   (крест-накрест), общая земля.
// UART1 выведен на GPIO25/26 через GPIO-матрицу. MSP идёт по линии CRSF (Serial2 <-> UART1 FC), см. msp_bridge.h.
#define FC_SERIAL Serial1
#define FC_BAUD 115200
#define FC_RX_PIN 25
#define FC_TX_PIN 26
