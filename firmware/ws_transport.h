/*
  ws_transport.h — весь низкоуровневый WebSocket-сервер (RFC 6455) в
  одном модуле, полностью отделенном от протокола CRSF.

  Раньше base64, SHA1-handshake, разбор входящих кадров и сборка JSON
  состояния были перемешаны в одном .ino вместе с CRC8 и отправкой
  CRSF-кадров. Теперь этот файл отвечает только за "сырой" транспорт:
  принять TCP-клиента, сделать handshake, прочитать текстовый кадр,
  отправить текстовый кадр. Он ничего не знает про каналы/CRSF — им
  занимается channel_state, который вызывается из .ino как callback.

  ТРЕБУЕМАЯ БИБЛИОТЕКА: ArduinoJson (Benoit Blanchon) — используется
  только здесь, для build_state_json() и разбора входящих сообщений.
*/

#pragma once

#include <Arduino.h>
#include <WiFi.h>
#include <mbedtls/sha1.h>
#include <ArduinoJson.h>
#include "config.h"

namespace ws {

// ---------- Base64 (только для ответа на handshake) ----------
inline String base64_encode(const uint8_t* data, size_t len) {
    static const char* chars =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    String out;
    size_t i = 0;
    for (; i + 2 < len; i += 3) {
        out += chars[(data[i] >> 2) & 0x3F];
        out += chars[((data[i] & 0x03) << 4) | ((data[i + 1] & 0xF0) >> 4)];
        out += chars[((data[i + 1] & 0x0F) << 2) | ((data[i + 2] & 0xC0) >> 6)];
        out += chars[data[i + 2] & 0x3F];
    }
    if (i < len) {
        out += chars[(data[i] >> 2) & 0x3F];
        if (i + 1 < len) {
            out += chars[((data[i] & 0x03) << 4) | ((data[i + 1] & 0xF0) >> 4)];
            out += chars[(data[i + 1] & 0x0F) << 2];
            out += '=';
        } else {
            out += chars[(data[i] & 0x03) << 4];
            out += "==";
        }
    }
    return out;
}

// ---------- Sec-WebSocket-Accept по ключу клиента (RFC 6455) ----------
inline String compute_ws_accept(const String& client_key) {
    const char* GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";
    String combined = client_key + GUID;

    uint8_t sha1_result[20];
    mbedtls_sha1_context ctx;
    mbedtls_sha1_init(&ctx);
    mbedtls_sha1_starts(&ctx);
    mbedtls_sha1_update(&ctx, (const uint8_t*)combined.c_str(), combined.length());
    mbedtls_sha1_finish(&ctx, sha1_result);
    mbedtls_sha1_free(&ctx);

    return base64_encode(sha1_result, 20);
}

// ---------- Отправка текстового кадра серверу -> клиенту (без маски) ----------
inline void send_text(WiFiClient& client, const String& text) {
    if (!client.connected()) return;

    size_t len = text.length();
    uint8_t header[4];
    size_t header_len = 0;

    header[0] = 0x81;  // FIN=1, opcode=0x1 (текст)

    if (len < 126) {
        header[1] = (uint8_t)len;
        header_len = 2;
    } else if (len < 65536) {
        header[1] = 126;
        header[2] = (uint8_t)((len >> 8) & 0xFF);
        header[3] = (uint8_t)(len & 0xFF);
        header_len = 4;
    } else {
        return;  // не ожидаем таких больших сообщений в этом протоколе
    }

    client.write(header, header_len);
    client.write((const uint8_t*)text.c_str(), len);
}

// ---------- HTTP -> WebSocket handshake с новым клиентом ----------
inline bool try_handshake(WiFiClient& client) {
    String request;
    uint32_t start = millis();

    while (millis() - start < 2000) {
        while (client.available()) {
            char c = client.read();
            request += c;
            if (request.endsWith("\r\n\r\n")) goto headers_done;
        }
        if (!client.connected()) return false;
    }
    return false;  // таймаут

headers_done:
    int key_idx = request.indexOf("Sec-WebSocket-Key:");
    if (key_idx < 0) return false;

    int key_start = key_idx + strlen("Sec-WebSocket-Key:");
    while (request[key_start] == ' ') key_start++;
    int key_end = request.indexOf("\r\n", key_start);
    String client_key = request.substring(key_start, key_end);

    String accept = compute_ws_accept(client_key);

    String response =
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Accept: " + accept + "\r\n\r\n";

    client.print(response);
    return true;
}

// ---------- Разбор одного входящего кадра от клиента (с маской) ----------
inline bool read_frame(WiFiClient& client, String& out_text) {
    if (client.available() < 2) return false;

    uint8_t b0 = client.read();
    uint8_t b1 = client.read();

    uint8_t opcode = b0 & 0x0F;
    bool masked = (b1 & 0x80) != 0;
    uint64_t payload_len = b1 & 0x7F;

    if (payload_len == 126) {
        while (client.available() < 2) delay(1);
        uint8_t ext[2];
        client.readBytes(ext, 2);
        payload_len = ((uint16_t)ext[0] << 8) | ext[1];
    } else if (payload_len == 127) {
        while (client.available() < 8) delay(1);
        uint8_t ext[8];
        client.readBytes(ext, 8);
        return false;  // такие огромные кадры этому протоколу не нужны
    }

    uint8_t mask[4] = {0, 0, 0, 0};
    if (masked) {
        while (client.available() < 4) delay(1);
        client.readBytes(mask, 4);
    }

    if (payload_len > WS_MAX_PAYLOAD) {
        while (payload_len-- > 0) client.read();
        return false;
    }

    uint8_t payload[WS_MAX_PAYLOAD];
    size_t received = 0;
    uint32_t start = millis();
    while (received < payload_len && millis() - start < 1000) {
        if (client.available()) {
            payload[received++] = client.read();
        }
    }

    if (masked) {
        for (size_t i = 0; i < received; i++) payload[i] ^= mask[i % 4];
    }

    if (opcode == 0x8) return false;  // close
    if (opcode != 0x1) return false;  // обрабатываем только текстовые кадры

    out_text = String((char*)payload, received);
    return true;
}

}  // namespace ws
