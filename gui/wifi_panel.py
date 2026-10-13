"""wifi_panel.py - блок «Настройка Wi-Fi сети ESP32» (SSID, логин, пароль -> ESP32 по WebSocket).

ФУНКЦИИ И КЛАССЫ ФАЙЛА
----------------------
class WifiPanel
    Группа полей Wi-Fi и кнопка отправки сети на ESP32.
  WifiPanel.__init__(self, ws_client, parent=None)
    Создаёт поля: тип сети (WPA2-Enterprise или обычная), SSID, identity, логин, пароль.
  WifiPanel._send(self)
    Проверяет соединение и SSID, просит подтверждение и отправляет настройки на ESP32.
"""
from PyQt6.QtWidgets import QCheckBox, QFormLayout, QGroupBox, QLabel, QLineEdit, QMessageBox, QPushButton


class WifiPanel(QGroupBox):
    def __init__(self, ws_client, parent=None):
        super().__init__("Настройка Wi-Fi сети ESP32", parent)
        self._ws = ws_client
        form = QFormLayout(self)
        self.enterprise_check = QCheckBox("WPA2-Enterprise (логин/пароль)")
        self.enterprise_check.setChecked(True)
        form.addRow(self.enterprise_check)
        self.ssid_input = QLineEdit()
        self.ssid_input.setPlaceholderText("Введите название сети")
        form.addRow("SSID сети:", self.ssid_input)
        self.identity_input = QLineEdit()
        self.identity_input.setPlaceholderText("Можно оставить пустым (= логин)")
        form.addRow("EAP identity (только Enterprise):", self.identity_input)
        self.username_input = QLineEdit()
        form.addRow("Логин (только Enterprise):", self.username_input)
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Пароль:", self.password_input)
        self.send_btn = QPushButton("Отправить сеть на ESP32")
        self.send_btn.clicked.connect(self._send)
        form.addRow(self.send_btn)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        form.addRow(self.status_label)

    def _send(self):
        if not self._ws.is_connected():
            QMessageBox.warning(self, "Нет соединения",
                                "Сначала подключитесь к точке доступа ESP32 и нажмите «Подключиться» выше.")
            return
        ssid = self.ssid_input.text().strip()
        if not ssid:
            QMessageBox.warning(self, "Нет SSID", "Введите имя сети (SSID).")
            return
        confirm = QMessageBox.question(
            self, "Подтверждение",
            f"Отправить на ESP32 новые данные сети \"{ssid}\"? "
            "ESP32 попробует переподключиться, точка доступа для GUI не отключится.")
        if confirm != QMessageBox.StandardButton.Yes:
            return
        sent = self._ws.send_wifi_config(
            ssid, self.identity_input.text().strip(), self.username_input.text().strip(),
            self.password_input.text(), self.enterprise_check.isChecked())
        if sent:
            self.status_label.setText(f"Отправлено на ESP32: SSID=\"{ssid}\".")
        else:
            self.status_label.setText("Не удалось отправить - нет соединения с ESP32.")
