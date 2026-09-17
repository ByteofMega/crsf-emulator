# CRSF Emulator: ESP32 + PyQt6 GUI

Эмулятор ELRS/CRSF-приёмника на ESP32: PyQt6-интерфейс на управляющем
устройстве отправляет значения RC-каналов по WebSocket на ESP32, тот
кодирует их в кадры CRSF и шлёт по UART на полётный контроллер
(Betaflight). Обратно ESP32 читает телеметрию с FC (батарея, углы, режим
полёта, качество линка) и передаёт её в GUI — так что приложение видит и
то, что само отправило, и реальное состояние дрона.

GUI написан на PyQt6 и не привязан к конкретному типу устройства — это
может быть ноутбук, стационарный ПК, мини-ПК на борту наземной станции
или любое другое устройство с Python и Wi-Fi, способное подключиться к
ESP32 по локальной сети.

## Архитектура

```
Управляющее устройство (PyQt6, WebSocket-клиент)
   <--  {"telemetry": {...}}  --\
   -->  {"channel":N,"value":V} --> ws://<IP_ESP32>:81/  <-->  ESP32
                                            |
                                     UART2, 420000 бод
                                     TX2 (GPIO17) -> RX FC
                                     RX2 (GPIO16) <- TX FC
                                            |
                                   Полётный контроллер (Betaflight, CRSF)
```

Схема кадра CRSF, парсинг WebSocket (RFC 6455) вручную на WiFiServer/
WiFiClient — без сторонних библиотек AsyncTCP/ESPAsyncWebServer.

## Структура проекта

```
.
├── firmware/                       # прошивка ESP32 (Arduino IDE)
│   ├── crsf_emulator_esp32_modular.ino   # setup()/loop(), точка входа
│   ├── config.h                          # Wi-Fi, пины, константы каналов
│   ├── channel_state.h                   # модель данных каналов + JSON
│   ├── crsf_protocol.h                   # кодирование CRSF-кадра, CRC8
│   ├── ws_transport.h                    # WebSocket-сервер (handshake, кадры)
│   └── telemetry.h                       # приём телеметрии от FC
├── gui/                             # PyQt6-интерфейс (Python)
│   ├── main.py                           # MainWindow, точка входа
│   ├── config.py                         # константы, QSS-стиль
│   ├── ws_client.py                      # обёртка над QWebSocket
│   ├── channel_widgets.py                # слайдеры каналов
│   ├── telemetry_panel.py                # панель телеметрии
│   └── requirements.txt
├── .gitignore
├── LICENSE
└── README.md
```

## Быстрый старт

### 1. Прошивка ESP32
1. Установите Arduino IDE и плату **ESP32** через Board Manager.
2. Установите библиотеку **ArduinoJson** (Benoit Blanchon) через Library Manager.
3. Откройте `firmware/crsf_emulator_esp32_modular.ino` — остальные `.h`
   файлы подхватятся автоматически (лежат в той же папке).
4. В `config.h` впишите `ваш_SSID` и `ваш_пароль`.
5. Загрузите скетч, откройте Serial Monitor (115200) — там появится IP
   адрес ESP32.

### 2. Настройка Betaflight
```
feature RX_SERIAL
set serialrx_provider = CRSF
save
```
Во вкладке **Ports** включите Serial RX на UART, к которому подключен
ESP32, и убедитесь, что Half-Duplex выключен (нужен полный дуплекс для
приёма телеметрии).

Подключение: `TX2 ESP32 -> RX FC`, `RX2 ESP32 -> TX FC`, `GND-GND`.

### 3. GUI на управляющем устройстве
Запускается на любом устройстве с Python 3.10+ и Wi-Fi-доступом в ту же
локальную сеть, что и ESP32 (ноутбук, ПК, мини-ПК и т.д.):

```bash
cd gui
pip install -r requirements.txt
python main.py
```
Введите IP адрес ESP32 из Serial Monitor и порт `81`, нажмите
«Подключиться».

## Требования

- ESP32 (любая плата с Wi-Fi)
- Arduino IDE 2.x + библиотека ArduinoJson
- Устройство с Python 3.10+ и Wi-Fi для запуска GUI
- PyQt6, PyQt6-WebSockets

## Известные ограничения

- Единицы измерения телеметрии батареи (`VOLTAGE_DIV`, `CURRENT_DIV` в
  `telemetry.h`) подобраны под типовую реализацию CRSF — при необходимости
  скорректируйте под вашу прошивку FC.
- WebSocket-сервер на ESP32 поддерживает одного клиента одновременно.

