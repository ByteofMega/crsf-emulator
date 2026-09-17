/*
  crsf_emulator_esp32_modular.ino — точка входа прошивки.

  Модули:
    config.h         — константы и пины
    channel_state.h  — модель данных каналов (GUI -> FC) + JSON
    crsf_protocol.h  — кодирование CRSF-кадра и отправка каналов по UART
    ws_transport.h   — низкоуровневый WebSocket-сервер
    telemetry.h      — прием телеметрии (FC -> GUI) по тому же UART

*/

#include <WiFi.h>

#include "config.h"
#include "channel_state.h"
#include "crsf_protocol.h"
#include "ws_transport.h"
#include "telemetry.h"

#define TELEMETRY_SEND_PERIOD_MS 200  // ~5 Гц в GUI, кадры от FC читаются каждый loop()

WiFiServer wsServer(WS_PORT);
WiFiClient wsClient;
bool wsHandshakeDone = false;
uint32_t last_frame_ms = 0;
uint32_t last_telemetry_send_ms = 0;

void poll_websocket() {
    if (!wsClient || !wsClient.connected()) {
        WiFiClient newClient = wsServer.available();
        if (newClient) {
            wsClient = newClient;
            wsHandshakeDone = false;
            Serial.println("Новый TCP-клиент подключился");
        }
    }

    if (wsClient && wsClient.connected() && !wsHandshakeDone) {
        if (ws::try_handshake(wsClient)) {
            wsHandshakeDone = true;
            Serial.println("WebSocket handshake выполнен - GUI подключен");
            ws::send_text(wsClient, state::build_state_json());
        } else {
            wsClient.stop();
        }
    }

    if (wsClient && wsClient.connected() && wsHandshakeDone) {
        String text;
        if (ws::read_frame(wsClient, text)) {
            if (state::apply_client_message(text)) {
                ws::send_text(wsClient, state::build_state_json());
            }
        }
    }
}

// Раз в TELEMETRY_SEND_PERIOD_MS шлет накопленную телеметрию в GUI,
// если клиент подключен. Отдельное сообщение с ключом "telemetry",
// не путается с {"channels":[...]} на стороне Python.
void push_telemetry_if_due() {
    uint32_t now = millis();
    if (now - last_telemetry_send_ms < TELEMETRY_SEND_PERIOD_MS) return;
    last_telemetry_send_ms = now;

    if (wsClient && wsClient.connected() && wsHandshakeDone) {
        ws::send_text(wsClient, telemetry::build_json());
    }
}

void setup() {
    Serial.begin(115200);

    crsf::build_crc8_table();
    state::init_defaults();

    CRSF_SERIAL.begin(CRSF_BAUDRATE, SERIAL_8N1, CRSF_RX_PIN, CRSF_TX_PIN);

    WiFi.mode(WIFI_STA);
    WiFi.setTxPower(WIFI_TX_POWER);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    Serial.print("Подключение к Wi-Fi");
    while (WiFi.status() != WL_CONNECTED) {
        delay(300);
        Serial.print(".");
    }
    Serial.println();
    Serial.print("IP адрес ESP32: ");
    Serial.println(WiFi.localIP());
    Serial.printf("WebSocket сервер на порту %d - введите этот IP и порт в GUI.\n", WS_PORT);

    wsServer.begin();
    last_frame_ms = millis();
}

void loop() {
    uint32_t now = millis();
    if (now - last_frame_ms >= FRAME_PERIOD_MS) {
        last_frame_ms = now;
        crsf::send_crsf_frame(state::rc_channels_us);
    }

    telemetry::poll();       // читаем входящие кадры телеметрии от FC
    poll_websocket();        // обслуживаем GUI-клиента (каналы туда-обратно)
    push_telemetry_if_due(); // периодически шлем накопленную телеметрию в GUI
}
