/*
 * ДОКУМЕНТАЦИЯ ФАЙЛА crsf_emulator_esp32_modular.ino
 * Точка входа прошивки ESP32: эмуляция приёмника ELRS (CRSF), WebSocket-сервер для GUI, Wi-Fi и мост к полётному контроллеру.
 *
 * ФУНКЦИИ:
 * start_local_access_point()
 *     Поднимает собственную точку доступа ESP32 (SSID и пароль из config.h): к ней подключается
 *     ноутбук с GUI, не зависящая от внешней сети.
 * connect_sta(const netcfg::Credentials& c)
 *     Подключает STA-интерфейс к внешней сети (WPA2-Personal или WPA2-Enterprise) и включает NAT
 *     для раздачи интернета; таймаут 20 с без перезагрузки платы. Возвращает true при успехе.
 * poll_websocket()
 *     Принимает нового TCP-клиента, выполняет WebSocket-рукопожатие, читает по одному кадру от GUI
 *     и передаёт сообщение в state::apply_client_message; при изменении каналов отвечает
 *     актуальным состоянием.
 * push_telemetry_if_due()
 *     Раз в 200 мс отправляет в GUI телеметрию CRSF (батарея, углы, режим, линк, GPS).
 * handle_network_reconnect_if_requested()
 *     Если GUI прислал новые данные Wi-Fi, переподключает STA с ними (блокирует цикл на время
 *     попытки).
 * setup()
 *     Инициализация: UART0 для отладки, UART1 (GPIO25/26) для CLI к FC, UART2 для CRSF, таблица
 *     CRC8, точка доступа, STA, WebSocket-сервер.
 * loop()
 *     Основной цикл: каждые 4 мс отправляет кадр RC-каналов в FC, читает телеметрию CRSF,
 *     обслуживает WebSocket, MSP/CLI-мост (msp::poll), телеметрию для GUI и переподключение Wi-Fi.
 */

/**
 * @file crsf_emulator_esp32_modular.ino
 * @brief Точка входа прошивки.
 */

#include <WiFi.h>
#include <esp_wifi.h>

#if ESP_ARDUINO_VERSION_MAJOR >= 3
#include "esp_eap_client.h"
#define EAP_SET_IDENTITY(v, l) esp_eap_client_set_identity((const uint8_t*)(v), (l))
#define EAP_SET_USERNAME(v, l) esp_eap_client_set_username((const uint8_t*)(v), (l))
#define EAP_SET_PASSWORD(v, l) esp_eap_client_set_password((const uint8_t*)(v), (l))
#define EAP_ENABLE() esp_wifi_sta_enterprise_enable()
#define EAP_DISABLE() esp_wifi_sta_enterprise_disable()
#else
#include "esp_wpa2.h"
#define EAP_SET_IDENTITY(v, l) esp_wifi_sta_wpa2_ent_set_identity((const uint8_t*)(v), (l))
#define EAP_SET_USERNAME(v, l) esp_wifi_sta_wpa2_ent_set_username((const uint8_t*)(v), (l))
#define EAP_SET_PASSWORD(v, l) esp_wifi_sta_wpa2_ent_set_password((const uint8_t*)(v), (l))
#define EAP_ENABLE() esp_wifi_sta_wpa2_ent_enable()
#define EAP_DISABLE() esp_wifi_sta_wpa2_ent_disable()
#endif

#include "config.h"
#include "netcfg.h"
#include "channel_state.h"
#include "crsf_protocol.h"
#include "ws_transport.h"
#include "telemetry.h"
#include "msp_bridge.h"

#define TELEMETRY_SEND_PERIOD_MS 200
#define WIFI_CONNECT_TIMEOUT_MS 20000

WiFiServer wsServer(WS_PORT);
WiFiClient wsClient;
bool wsHandshakeDone = false;
uint32_t last_frame_ms = 0;
uint32_t last_telemetry_send_ms = 0;
bool sta_connected_once = false;

/**
 * @brief Поднять собственную точку доступа ESP32 для ноутбука с GUI.
 *
 * Работает независимо от KFU.NET/любой другой STA-сети — ноутбук
 * подключается сюда, а не в внешнюю сеть, и поэтому не подпадает под её
 * изоляцию клиентов.
 */
void start_local_access_point() {
  IPAddress ap_ip(AP_IP_1, AP_IP_2, AP_IP_3, AP_IP_4);
  IPAddress ap_mask(255, 255, 255, 0);
  IPAddress ap_lease_start(AP_IP_1, AP_IP_2, AP_IP_3, AP_IP_4 + 1);
  IPAddress ap_dns(8, 8, 8, 8);

  WiFi.AP.config(ap_ip, ap_ip, ap_mask, ap_lease_start, ap_dns);
  if (!WiFi.AP.create(AP_SSID, AP_PASS)) {
    Serial.println("Не удалось создать точку доступа ESP32!");
    return;
  }
  Serial.print("Точка доступа для GUI создана: SSID=");
  Serial.print(AP_SSID);
  Serial.print(", IP=");
  Serial.println(ap_ip);
  Serial.println("Подключите ноутбук с GUI именно к этой сети (не к внешней сети напрямую).");
}

/**
 * @brief Подключить STA-интерфейс к сети, заданной в credentials
 *        (не трогая точку доступа AP, которая работает независимо).
 *
 * Аргументы:
 *     c (const netcfg::Credentials&): сетевые данные — SSID и, если
 *         c.enterprise == true, ещё identity/username/password для
 *         WPA2-Enterprise (802.1X/PEAP+MSCHAPv2); если c.enterprise ==
 *         false, используется обычный WiFi.begin(ssid, password)
 *         (WPA2-Personal).
 *
 * Возвращает:
 *     bool: true при успешном подключении в течение
 *         WIFI_CONNECT_TIMEOUT_MS, false при таймауте (плата НЕ
 *         перезагружается - см. комментарий в начале файла).
 */
bool connect_sta(const netcfg::Credentials& c) {
  WiFi.disconnect(false, true);  // сбросить STA, но не трогать AP (false = keepAP)
  delay(200);

  if (c.enterprise) {
    EAP_SET_IDENTITY(c.identity.c_str(), c.identity.length());
    EAP_SET_USERNAME(c.username.c_str(), c.username.length());
    EAP_SET_PASSWORD(c.password.c_str(), c.password.length());
    EAP_ENABLE();
    delay(200);
    WiFi.begin(c.ssid.c_str());
  } else {
    EAP_DISABLE();
    delay(100);
    WiFi.begin(c.ssid.c_str(), c.password.c_str());
  }

  Serial.printf("Подключение к сети \"%s\"%s", c.ssid.c_str(),
                c.enterprise ? " (WPA2-Enterprise)" : " (WPA2-Personal)");
  uint32_t start = millis();
  while (WiFi.status() != WL_CONNECTED) {
    if (millis() - start > WIFI_CONNECT_TIMEOUT_MS) {
      Serial.printf("\nТаймаут подключения к \"%s\" (статус: %d). "
                    "Точка доступа для GUI продолжает работать.\n",
                    c.ssid.c_str(), WiFi.status());
      return false;
    }
    delay(400);
    Serial.print(".");
  }
  Serial.println();
  Serial.print("STA IP в этой сети: ");
  Serial.println(WiFi.localIP());

  if (!sta_connected_once) {
    // Отключаем автопереподключение только после ПЕРВОГО успешного коннекта,
    // чтобы кратковременные сбои STA не заставляли радио сканировать каналы
    // и не "подвешивали" точку доступа для GUI на секунды.
    WiFi.setAutoReconnect(false);
    sta_connected_once = true;
  }

  if (WiFi.AP.enableNAPT(true)) {
    Serial.println("NAT включён: точка доступа ESP32 раздаёт интернет из этой сети.");
  } else {
    Serial.println("Не удалось включить NAT (NAPT) - интернета на точке доступа не будет.");
  }
  return true;
}

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

void push_telemetry_if_due() {
  uint32_t now = millis();
  if (now - last_telemetry_send_ms < TELEMETRY_SEND_PERIOD_MS) return;
  last_telemetry_send_ms = now;

  if (wsClient && wsClient.connected() && wsHandshakeDone) {
    ws::send_text(wsClient, telemetry::build_json());
  }
}

/**
 * @brief Проверить, не пришёл ли по WS запрос на смену сети (netcfg.h),
 *        и если да - переподключить STA новыми данными.
 *
 * Вызывается из loop(). Блокирует основной цикл на время попытки
 * подключения (до WIFI_CONNECT_TIMEOUT_MS при неудаче) - это приемлемо,
 * т.к. происходит только по явному запросу пользователя из GUI, а не в
 * штатном режиме полёта.
 */
void handle_network_reconnect_if_requested() {
  if (netcfg::consume_reconnect_request()) {
    connect_sta(netcfg::current);
  }
}

void setup() {
  Serial.begin(115200);

  // UART1 на GPIO25/26 - CLI полётного контроллера (UART3 FC). MSP идёт по линии CRSF (Serial2).
  // Буфер приёма увеличен: dump CLI приходит быстрее, чем loop() успевает его отправлять.
  FC_SERIAL.setRxBufferSize(4096);
  FC_SERIAL.begin(FC_BAUD, SERIAL_8N1, FC_RX_PIN, FC_TX_PIN);

  crsf::build_crc8_table();
  state::init_defaults();

  CRSF_SERIAL.begin(CRSF_BAUDRATE, SERIAL_8N1, CRSF_RX_PIN, CRSF_TX_PIN);

  WiFi.mode(WIFI_MODE_APSTA);
  start_local_access_point();

  netcfg::current = netcfg::load_saved();
  if (!connect_sta(netcfg::current)) {
    Serial.println("STA не подключился при старте. GUI всё равно доступен через точку "
                    "доступа ESP32 - можно прислать другие сетевые настройки через "
                    "сообщение {\"wifi\": {...}} по WebSocket.");
  }

  Serial.print("IP адрес точки доступа для GUI: ");
  Serial.println(WiFi.softAPIP());
  Serial.printf("WebSocket сервер на порту %d - в GUI укажите IP точки доступа выше.\n", WS_PORT);

  wsServer.begin();
  last_frame_ms = millis();
}

void loop() {
  uint32_t now = millis();
  if (now - last_frame_ms >= FRAME_PERIOD_MS) {
    last_frame_ms = now;
    crsf::send_crsf_frame(state::rc_channels_us);
  }

  telemetry::poll();
  poll_websocket();

  bool ws_ready = wsClient && wsClient.connected() && wsHandshakeDone;
  msp::poll(wsClient, ws_ready);  // MSP-опрос FC и проброс CLI

  push_telemetry_if_due();
  handle_network_reconnect_if_requested();
}
