# CYDView — прошивка платы

Тонкая прошивка для ESP32-2432S028 («Cheap Yellow Display»). Плата ничего не рисует сама: она получает от CYD Hub изменившиеся плитки кадра, показывает их и отправляет касания. Всё остальное — на сервере.

Что умеет:

- первичная настройка с телефона: точка доступа **CYD-Setup** → `192.168.4.1` → Wi-Fi, адрес хаба и токен;
- калибровка тача при первом запуске; меню «Калибровка / Wi-Fi и сервер» — если держать **BOOT** при включении;
- звук, RGB-светодиод, яркость подсветки по командам сервера, датчик света;
- обновление прошивки по Wi-Fi из `/admin`.

## Сборка

| | Версия |
|---|---|
| Arduino IDE | 2.x (или `arduino-cli`) |
| Платы | esp32 by Espressif 3.0.7 |
| Библиотеки | LovyanGFX 1.2.34, WiFiManager 2.0.17 |

Плата **ESP32 Dev Module**, Partition Scheme **Minimal SPIFFS (1.9MB APP with OTA)** — без OTA-раздела обновление по Wi-Fi не заработает.

```bash
arduino-cli compile -b "esp32:esp32:esp32:PartitionScheme=min_spiffs" firmware/CYDView
arduino-cli upload  -b "esp32:esp32:esp32:PartitionScheme=min_spiffs" -p COM5 firmware/CYDView
```

## Настройки в `config.h`

- `PANEL_ST7789 1` — для версии CYD с двумя USB (micro-USB + USB-C), если экран белый или с искажениями. `PANEL_INVERT` — если цвета негативные. `SCREEN_ROTATION 3` — перевернуть на 180°.
- `DEFAULT_HUB` / `DEFAULT_TOKEN` — можно вписать адрес хаба и токен, тогда портал настройки при первом запуске не понадобится (Wi-Fi всё равно задаётся через него).
- Пины подсветки, динамика, светодиода, датчика света и тача — для стандартной CYD менять не нужно.
