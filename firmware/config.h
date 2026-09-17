/**
 * @file config.h
 * @brief Все настройки и константы прошивки в одном месте: Wi-Fi
 *        credentials, пины UART, параметры CRSF, имена каналов.
 *
 * Единственный файл, который нужно открыть, чтобы поменять SSID/пароль,
 * пины или частоту обновления — остальной код их не хранит, а только
 * использует. Функций в этом файле нет — только константы и enum,
 * поэтому Doxygen-комментарии здесь поясняют назначение каждой группы.
 */

#pragma once

#include <Arduino.h>

// ---------- Wi-Fi ----------
/** SSID точки доступа, к которой подключается ESP32. */
static const char* WIFI_SSID = "GalaxyA35";
/** Пароль точки доступа Wi-Fi. */
static const char* WIFI_PASSWORD = "GahWer6539";

// ---------- WebSocket ----------
/** TCP-порт, на котором ESP32 поднимает WebSocket-сервер для GUI. */
#define WS_PORT 81
/** Максимальный размер payload одного входящего WebSocket-кадра (байт). */
#define WS_MAX_PAYLOAD 512

/**
 * Мощность передатчика Wi-Fi (ограничена "на всякий случай"; можно
 * вернуть WIFI_POWER_19_5dBm для максимальной дальности).
 */
#define WIFI_TX_POWER WIFI_POWER_15dBm

// ---------- UART к полетному контроллеру (CRSF) ----------
/** Объект Serial, используемый для связи с FC по протоколу CRSF. */
#define CRSF_SERIAL Serial2
/** Скорость UART для CRSF (стандарт для Betaflight/ELRS). */
#define CRSF_BAUDRATE 420000
/** Пин ESP32, принимающий данные от FC (RX2, подключается к TX FC). */
#define CRSF_RX_PIN 16
/** Пин ESP32, передающий данные на FC (TX2, подключается к RX FC). */
#define CRSF_TX_PIN 17

/** Целевая частота отправки кадров RC_CHANNELS на FC, Гц. */
#define UPDATE_RATE_HZ 250
/** Период между кадрами RC_CHANNELS, мс (соответствует UPDATE_RATE_HZ). */
#define FRAME_PERIOD_MS 4

// ---------- Каналы ----------
/** Количество основных каналов (ROLL/PITCH/THROTTLE/YAW). */
#define NUM_MAIN_CHANNELS 4
/** Количество вспомогательных каналов (AUX1..AUX12). */
#define NUM_AUX_CHANNELS 12
/** Общее количество каналов CRSF (16). */
#define TOTAL_CHANNELS (NUM_MAIN_CHANNELS + NUM_AUX_CHANNELS)  // 16

/** Индексы основных каналов в массиве rc_channels_us. */
enum { ROLL = 0, PITCH = 1, THROTTLE = 2, YAW = 3, AUX_START = 4 };

/** Отображаемые имена всех 16 каналов, используются в логах и JSON. */
static const char* CHANNEL_NAMES[TOTAL_CHANNELS] = {
    "ROLL", "PITCH", "THROTTLE", "YAW",
    "AUX1", "AUX2", "AUX3", "AUX4", "AUX5", "AUX6",
    "AUX7", "AUX8", "AUX9", "AUX10", "AUX11", "AUX12"
};
