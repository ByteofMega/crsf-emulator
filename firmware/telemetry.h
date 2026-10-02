/**
 * @file telemetry.h
 * @brief Приём телеметрии от FC (батарея, attitude, режим, линк, GPS)
 *        по тому же UART, что и отправка RC-каналов.
 */
#pragma once
#include <Arduino.h>
#include <ArduinoJson.h>
#include "config.h"
#include "crsf_protocol.h"

namespace telemetry {

#define CRSF_FRAMETYPE_GPS 0x02
#define CRSF_FRAMETYPE_BATTERY 0x08
#define CRSF_FRAMETYPE_LINK_STATISTICS 0x14
#define CRSF_FRAMETYPE_ATTITUDE 0x1E
#define CRSF_FRAMETYPE_FLIGHT_MODE 0x21

#define VOLTAGE_DIV 10.0f
#define CURRENT_DIV 10.0f
#define ATTITUDE_DIV 10000.0f
#define GPS_COORD_DIV 1e7
#define GPS_SPEED_DIV 10.0f
#define GPS_HEADING_DIV 100.0f
#define GPS_ALT_OFFSET 1000

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

    bool has_gps = false;
    double lat_deg = 0, lon_deg = 0;
    float ground_speed_kmh = 0;
    float heading_deg = 0;
    int16_t altitude_m = 0;
    uint8_t satellites = 0;
};

static State state;

inline int16_t read_i16(const uint8_t* p) { return (int16_t)((p[0] << 8) | p[1]); }
inline uint16_t read_u16(const uint8_t* p) { return (uint16_t)((p[0] << 8) | p[1]); }
inline uint32_t read_u24(const uint8_t* p) { return ((uint32_t)p[0] << 16) | ((uint32_t)p[1] << 8) | p[2]; }
inline int32_t read_i32(const uint8_t* p) {
    return (int32_t)(((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | p[3]);
}

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
        case CRSF_FRAMETYPE_GPS:
            if (len >= 15) {
                state.lat_deg = read_i32(payload) / GPS_COORD_DIV;
                state.lon_deg = read_i32(payload + 4) / GPS_COORD_DIV;
                state.ground_speed_kmh = read_u16(payload + 8) / GPS_SPEED_DIV;
                state.heading_deg = read_u16(payload + 10) / GPS_HEADING_DIV;
                state.altitude_m = (int16_t)read_u16(payload + 12) - GPS_ALT_OFFSET;
                state.satellites = payload[14];
                state.has_gps = true;
            }
            break;
        default:
            break;
    }
}

inline void poll() {
    static uint8_t buf[64];
    static size_t idx = 0;
    while (CRSF_SERIAL.available()) {
        uint8_t b = CRSF_SERIAL.read();
        if (idx == 0) {
            if (b != CRSF_SYNC_BYTE) continue;
            buf[idx++] = b;
            continue;
        }
        buf[idx++] = b;
        if (idx == 2) continue;
        uint8_t length = buf[1];
        if (length < 2 || length > sizeof(buf) - 2) {
            idx = 0;
            continue;
        }
        if (idx == (size_t)(length + 2)) {
            uint8_t type = buf[2];
            uint8_t payload_len = length - 2;
            uint8_t crc_received = buf[length + 1];
            uint8_t crc_calc = crsf::crc8_dvb_s2(&buf[2], length - 1);
            if (crc_received == crc_calc) {
                handle_frame(type, &buf[3], payload_len);
            }
            idx = 0;
        }
    }
}

inline String build_json() {
    StaticJsonDocument<1024> doc;
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
    if (state.has_gps) {
        JsonObject gps = t.createNestedObject("gps");
        gps["lat"] = state.lat_deg;
        gps["lon"] = state.lon_deg;
        gps["speed_kmh"] = state.ground_speed_kmh;
        gps["heading_deg"] = state.heading_deg;
        gps["altitude_m"] = state.altitude_m;
        gps["satellites"] = state.satellites;
    }
    String out;
    serializeJson(doc, out);
    return out;
}

}  // namespace telemetry
