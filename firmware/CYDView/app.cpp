// =====================================================================
//  CYD View — плата как «удалённый экран» для CYD Hub
//
//  Задача кадров (ядро 0): долгий опрос GET /api/frame — как только картинка поменялась,
//  сервер присылает изменившиеся плитки экрана без потерь (RGB565 + zlib).
//  В заголовках — светодиод, писк, пробуждение.
//  Основной цикл: касания -> POST /api/touch, вывод плиток, подсветка, звук.
// =====================================================================
#include <Arduino.h>
#include <Preferences.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <WiFiManager.h>
#include <Update.h>
#include "miniz.h"          // распаковщик zlib, встроен в ROM ESP32
#include "config.h"
#include "lgfx_cyd.h"

LGFX lcd;
Preferences prefs;
char hubUrl[96] = "";
char token[16] = "";

// ---- общий кадр между задачей и основным циклом ------------------------------
uint8_t* frameBuf;
volatile size_t frameLen = 0;
volatile bool frameReady = false;      // задача положила кадр, цикл ещё не нарисовал
portMUX_TYPE sigMux = portMUX_INITIALIZER_UNLOCKED;
char sigLed[16] = "off";
uint32_t sigBeepSeq = 0, sigWakeSeq = 0;
char sigBeep[12] = "";
uint32_t sigDimS = 0, sigOffS = 0;
volatile uint8_t sigBright = 255;       // яркость решает сервер (максимум / расписание / датчик)
volatile bool online = false;
volatile uint32_t lastOkMs = 0;
volatile int lux = -1;                  // датчик света (GPIO34): больше = темнее
char fwMd5[33] = "";                    // md5 прошивки, которую поставили по воздуху
volatile int otaPct = -1;               // идёт обновление: 0..100

// ---- звук -------------------------------------------------------------------
struct Note { uint16_t f, ms; };
static const Note SND_PERM[] = {{1320, 70}, {1, 40}, {1760, 90}, {1, 40}, {1320, 70}, {1, 40}, {1760, 120}, {0, 0}};
static const Note SND_DOWN[] = {{1800, 120}, {1, 60}, {1200, 120}, {1, 60}, {1800, 120}, {1, 60}, {1200, 250}, {0, 0}};
static const Note SND_UP[]   = {{880, 80}, {1175, 80}, {1568, 140}, {0, 0}};
static const Note SND_DONE[] = {{1500, 40}, {1, 30}, {2000, 60}, {0, 0}};
static const Note SND_FAIL[] = {{420, 120}, {330, 200}, {0, 0}};
static const Note SND_TAP[]  = {{2200, 15}, {0, 0}};
const Note* sndSeq = nullptr;
uint8_t sndIdx = 0;
uint32_t sndNext = 0;

static void play(const Note* s) { sndSeq = s; sndIdx = 0; sndNext = 0; }
static void soundUpdate() {
  if (!sndSeq || millis() < sndNext) return;
  Note n = sndSeq[sndIdx++];
  if (n.ms == 0) { noTone(PIN_SPEAKER); sndSeq = nullptr; return; }
  if (n.f > 1) tone(PIN_SPEAKER, n.f); else noTone(PIN_SPEAKER);
  sndNext = millis() + n.ms;
}
static const Note* patternSound(const char* p) {
  if (!strcmp(p, "perm")) return SND_PERM;
  if (!strcmp(p, "down")) return SND_DOWN;
  if (!strcmp(p, "up"))   return SND_UP;
  if (!strcmp(p, "done")) return SND_DONE;
  if (!strcmp(p, "fail")) return SND_FAIL;
  return nullptr;
}

static void led(bool r, bool g, bool b) {
  digitalWrite(PIN_LED_R, !r);
  digitalWrite(PIN_LED_G, !g);
  digitalWrite(PIN_LED_B, !b);
}
// режимы приходят с сервера в заголовке X-Led
static void ledUpdate(const char* mode, bool connected) {
  uint32_t t = millis();
  if (!connected)                          led(0, 0, (t % 2000) < 80);
  else if (!strcmp(mode, "purple-blink"))  { bool on = (t % 600) < 300; led(on, 0, on); }
  else if (!strcmp(mode, "red-blink"))     led((t % 500) < 250, 0, 0);
  else if (!strcmp(mode, "red-slow"))      led((t % 1500) < 120, 0, 0);
  else if (!strcmp(mode, "yellow-flash"))  { bool on = (t % 3000) < 80; led(on, on, 0); }
  else if (!strcmp(mode, "green-flash"))   led(0, (t % 10000) < 40, 0);
  else                                     led(0, 0, 0);
}

// ---- сообщения на экране, пока нет кадров с сервера ----------------------------
static void message(const char* title, const char* l1 = "", const char* l2 = "", const char* l3 = "") {
  lcd.fillScreen(lcd.color565(13, 16, 22));
  lcd.setTextDatum(textdatum_t::middle_center);
  lcd.setTextColor(TFT_WHITE);
  lcd.setFont(&fonts::efontJA_12);
  lcd.drawString(title, 160, 70);
  lcd.setFont(&fonts::efontJA_12);
  lcd.setTextColor(lcd.color565(140, 148, 165));
  lcd.drawString(l1, 160, 110);
  lcd.drawString(l2, 160, 130);
  lcd.drawString(l3, 160, 150);
}

// ---- калибровка сенсора --------------------------------------------------------
static void runCalibration() {
  uint16_t cal[8];
  message("Калибровка экрана", "Нажимайте на стрелки в углах");
  delay(800);
  lcd.fillScreen(TFT_BLACK);
  lcd.calibrateTouch(cal, TFT_WHITE, TFT_BLACK, 15);
  lcd.setTouchCalibrate(cal);
  prefs.putBytes("cal", cal, sizeof(cal));
}

// ---- настройка Wi-Fi / сервера с телефона ------------------------------------------
static void setupPortal(bool force) {
  WiFiManager wm;
  WiFiManagerParameter pHub("hub", "Адрес CYD Hub (http://IP:8770)", hubUrl, sizeof hubUrl - 1);
  WiFiManagerParameter pTok("tok", "Токен (на странице /admin)", token, sizeof token - 1);
  wm.addParameter(&pHub);
  wm.addParameter(&pTok);
  wm.setTitle("CYD View");
  wm.setConnectTimeout(20);
  bool saved = false;
  wm.setSaveParamsCallback([&] { saved = true; });
  wm.setAPCallback([](WiFiManager*) {
    message("Настройка", "Подключитесь к Wi-Fi «" SETUP_AP "»",
            "и откройте 192.168.4.1", "укажите сеть, адрес сервера и токен");
  });
  bool ok = force ? wm.startConfigPortal(SETUP_AP) : wm.autoConnect(SETUP_AP);
  if (saved || force) {
    strlcpy(hubUrl, pHub.getValue(), sizeof hubUrl);
    strlcpy(token, pTok.getValue(), sizeof token);
    size_t n = strlen(hubUrl);
    while (n && hubUrl[n - 1] == '/') hubUrl[--n] = 0;   // без "/" в конце
    prefs.putString("hub", hubUrl);
    prefs.putString("tok", token);
  }
  if (!ok) ESP.restart();
}

// Меню при включении с зажатой BOOT
static void bootMenu() {
  message("Меню", "", "");
  lcd.fillRoundRect(20, 100, 130, 60, 10, lcd.color565(52, 60, 80));
  lcd.fillRoundRect(170, 100, 130, 60, 10, lcd.color565(52, 60, 80));
  lcd.setTextColor(TFT_WHITE);
  lcd.setFont(&fonts::efontJA_12);
  lcd.drawString("Калибровка", 85, 130);
  lcd.drawString("Wi-Fi и сервер", 235, 130);
  lcd.drawString("(или подождите 10 с)", 160, 190);
  while (digitalRead(PIN_BOOT) == LOW) delay(10);
  uint32_t until = millis() + 10000;
  int32_t x, y;
  while (millis() < until) {
    if (lcd.getTouch(&x, &y) && y > 90 && y < 170) {
      while (lcd.getTouch(&x, &y)) delay(10);
      if (x < 160) runCalibration();
      else setupPortal(true);
      return;
    }
    delay(20);
  }
}

// ---- обновление прошивки по воздуху ------------------------------------------------
// Сервер присылает md5 прошивки, загруженной в /admin. Не совпадает с нашей —
// скачиваем, проверяем md5 и перезагружаемся. Неудачную версию второй раз не пробуем.
static void otaUpdate(const char* md5) {
  static char failed[33] = "";
  if (!strcmp(md5, failed)) return;
  char url[160];
  snprintf(url, sizeof url, "%s/api/firmware?t=%s", hubUrl, token);
  HTTPClient http;
  WiFiClient client;
  http.begin(client, url);
  http.setTimeout(20000);
  int code = http.GET();
  int len = http.getSize();
  bool ok = false;
  if (code == 200 && len > 0 && Update.begin(len)) {
    Update.setMD5(md5);
    WiFiClient* s = http.getStreamPtr();
    uint8_t buf[1024];
    int got = 0;
    otaPct = 0;
    uint32_t t0 = millis();
    while (got < len && millis() - t0 < 120000) {
      size_t n = s->available();
      if (!n) { vTaskDelay(1); continue; }
      n = s->readBytes(buf, min(n, sizeof buf));
      if (Update.write(buf, n) != n) break;
      got += n;
      otaPct = got * 100 / len;
    }
    ok = got == len && Update.end(true);
    if (!ok) Update.abort();
  }
  http.end();
  if (ok) {
    prefs.putString("fwmd5", md5);
    Serial.println("OTA ok, перезагрузка");
    delay(300);
    ESP.restart();
  }
  Serial.printf("OTA не удалось: HTTP %d, %s\n", code, Update.errorString());
  strlcpy(failed, md5, sizeof failed);
  otaPct = -1;
}

// ---- задача кадров ---------------------------------------------------------------
static void frameTask(void*) {
  HTTPClient http;
  WiFiClient client;
  uint32_t have = 0xFFFFFFFF, beepSeen = 0xFFFFFFFF;
  const char* hdrs[] = {"X-V", "X-Led", "X-Beep", "X-Wake", "X-Dim", "X-Off", "X-Bright", "X-Fw"};
  char url[192];
  while (true) {
    if (WiFi.status() != WL_CONNECTED) { online = false; vTaskDelay(pdMS_TO_TICKS(500)); continue; }
    while (frameReady) vTaskDelay(pdMS_TO_TICKS(5));        // прошлый кадр ещё не нарисован
    snprintf(url, sizeof url, "%s/api/frame?v=%ld&beep=%ld&lux=%d&fw=%s&t=%s", hubUrl,
             (long)(int32_t)have, (long)(int32_t)beepSeen, lux, fwMd5[0] ? fwMd5 : "usb", token);
    http.begin(client, url);
    http.setTimeout(20000);
    http.collectHeaders(hdrs, 8);
    int code = http.GET();
    if (code == 200 || code == 204) {
      online = true;
      lastOkMs = millis();
      have = http.header("X-V").toInt();
      String beep = http.header("X-Beep");     // "seq:pattern"
      int colon = beep.indexOf(':');
      portENTER_CRITICAL(&sigMux);
      strlcpy(sigLed, http.header("X-Led").c_str(), sizeof sigLed);
      if (colon > 0) {
        sigBeepSeq = beep.substring(0, colon).toInt();
        strlcpy(sigBeep, beep.substring(colon + 1).c_str(), sizeof sigBeep);
      }
      sigWakeSeq = http.header("X-Wake").toInt();
      sigDimS = http.header("X-Dim").toInt();
      sigOffS = http.header("X-Off").toInt();
      if (http.header("X-Bright").length()) sigBright = constrain(http.header("X-Bright").toInt(), 1, 255);
      portEXIT_CRITICAL(&sigMux);
      String fw = http.header("X-Fw");
      if (fw.length() == 32 && strcmp(fw.c_str(), fwMd5)) {
        http.end();
        otaUpdate(fw.c_str());
        continue;
      }
      beepSeen = sigBeepSeq;
      if (code == 200) {
        int len = http.getSize();
        if (len > 0 && len <= FRAME_MAX) {
          WiFiClient* s = http.getStreamPtr();
          size_t got = 0;
          uint32_t t0 = millis();
          while (got < (size_t)len && millis() - t0 < 5000) {
            size_t n = s->available();
            if (n) got += s->readBytes(frameBuf + got, min(n, (size_t)len - got));
            else vTaskDelay(1);
          }
          if (got == (size_t)len) { frameLen = got; frameReady = true; }
        }
      }
    } else {
      online = false;
      Serial.printf("frame: HTTP %d\n", code);
      if (code == 401) Serial.println("неверный токен");
      http.end();
      vTaskDelay(pdMS_TO_TICKS(2000));
      continue;
    }
    http.end();
  }
}

// ---- вывод кадра: плитки RGB565, сжатые zlib (формат — Screen.payload на сервере) ----
static tinfl_decompressor inflator;     // ~11 КБ — статически, не на стеке
static uint16_t tileBuf[64 * 64];

static bool drawTiles(const uint8_t* p, size_t len) {
  if (len < 6 || memcmp(p, "CYD2", 4)) return false;
  uint16_t n = p[4] | p[5] << 8;
  const uint8_t* q = p + 6;
  const uint8_t* end = p + len;
  lcd.startWrite();
  for (uint16_t i = 0; i < n; i++) {
    if (q + 12 > end) break;
    uint16_t x = q[0] | q[1] << 8, y = q[2] | q[3] << 8, w = q[4] | q[5] << 8, h = q[6] | q[7] << 8;
    uint32_t zl = q[8] | q[9] << 8 | q[10] << 16 | (uint32_t)q[11] << 24;
    q += 12;
    if (q + zl > end || (size_t)w * h > sizeof tileBuf / 2) break;
    size_t inLen = zl, outLen = (size_t)w * h * 2;
    tinfl_init(&inflator);
    tinfl_status st = tinfl_decompress(&inflator, q, &inLen, (uint8_t*)tileBuf, (uint8_t*)tileBuf, &outLen,
                                       TINFL_FLAG_PARSE_ZLIB_HEADER | TINFL_FLAG_USING_NON_WRAPPING_OUTPUT_BUF);
    if (st == TINFL_STATUS_DONE && outLen == (size_t)w * h * 2)
      lcd.pushImage(x, y, w, h, (const lgfx::rgb565_t*)tileBuf);
    q += zl;
  }
  lcd.endWrite();
  return true;
}

// ---- касания ---------------------------------------------------------------------
HTTPClient touchHttp;
WiFiClient touchClient;

static void sendTouch(char kind, int x, int y) {
  if (!online) return;
  char url[160], body[24];
  snprintf(url, sizeof url, "%s/api/touch?t=%s", hubUrl, token);
  snprintf(body, sizeof body, "%c %d %d", kind, x, y);
  touchHttp.begin(touchClient, url);
  touchHttp.setTimeout(1500);
  touchHttp.POST((uint8_t*)body, strlen(body));
  touchHttp.end();
}

// ---- Arduino ----------------------------------------------------------------------
uint8_t backlight = 2;          // 2 ярко, 1 приглушено, 0 выключено
uint32_t lastTouchMs = 0;

uint8_t appliedBright = 0;
static void setBacklight(uint8_t lvl) {
  backlight = lvl;
  uint8_t b = sigBright;
  appliedBright = lvl == 2 ? b : (lvl == 1 ? (b < 30 ? b : 30) : 0);
  lcd.setBrightness(appliedBright);
}

void setup() {
  pinMode(PIN_LED_R, OUTPUT); pinMode(PIN_LED_G, OUTPUT); pinMode(PIN_LED_B, OUTPUT);
  led(0, 0, 0);
  pinMode(PIN_BOOT, INPUT_PULLUP);
  setToneChannel(4);
  Serial.begin(115200);
  prefs.begin("cydview", false);
  strlcpy(hubUrl, prefs.getString("hub", DEFAULT_HUB).c_str(), sizeof hubUrl);
  strlcpy(token, prefs.getString("tok", DEFAULT_TOKEN).c_str(), sizeof token);
  strlcpy(fwMd5, prefs.getString("fwmd5", "").c_str(), sizeof fwMd5);
  pinMode(PIN_LDR, INPUT);

  lcd.init();
  lcd.setRotation(SCREEN_ROTATION);
  lcd.setBrightness(255);
  uint16_t cal[8];
  if (prefs.getBytesLength("cal") == sizeof(cal)) {
    prefs.getBytes("cal", cal, sizeof(cal));
    lcd.setTouchCalibrate(cal);
  } else {
    runCalibration();
  }
  if (digitalRead(PIN_BOOT) == LOW) bootMenu();

  frameBuf = (uint8_t*)malloc(FRAME_MAX);
  if (!frameBuf) { message("Нет памяти под кадр"); while (true) delay(1000); }

  message("CYD View", "Подключаюсь к Wi-Fi…");
  WiFi.mode(WIFI_STA);
  setupPortal(!hubUrl[0] || !token[0]);
  WiFi.setAutoReconnect(true);
  Serial.printf("hub %s, ip %s, heap %lu\n", hubUrl, WiFi.localIP().toString().c_str(), (unsigned long)ESP.getFreeHeap());
  message("CYD View", "Связываюсь с сервером", hubUrl);

  xTaskCreatePinnedToCore(frameTask, "frame", 8192, nullptr, 1, nullptr, 0);
  lastTouchMs = millis();
}

void loop() {
  static bool held = false, waking = false;
  static int32_t lx, ly, sx, sy;
  static uint32_t lastMove = 0, lastWake = 0, lastBeep = 0, beepInit = 0;
  static bool shownOffline = false;
  uint32_t now = millis();

  // --- касания: down / move / up
  int32_t x, y;
  bool pressed = lcd.getTouch(&x, &y);
  if (pressed && !held) {
    lastTouchMs = now;
    if (backlight < 2) { setBacklight(2); waking = true; }   // этим касанием только будим
    else {
      sx = lx = x; sy = ly = y;
      lcd.drawCircle(x, y, 14, TFT_WHITE);                    // мгновенный отклик до нового кадра
      play(SND_TAP);
      sendTouch('d', x, y);
    }
  } else if (pressed && held && !waking) {
    lastTouchMs = now;
    if ((abs(x - lx) > 3 || abs(y - ly) > 3) && now - lastMove > 40) {
      lx = x; ly = y; lastMove = now;
      sendTouch('m', x, y);
    }
  } else if (!pressed && held) {
    if (!waking) sendTouch('u', lx, ly);
    waking = false;
  }
  held = pressed;

  // --- обновление прошивки: прогресс вместо кадров
  static int shownPct = -2;
  if (otaPct >= 0) {
    if (otaPct / 5 != shownPct) {
      shownPct = otaPct / 5;
      char b[32];
      snprintf(b, sizeof b, "%d%%", otaPct);
      message("Обновление прошивки", b, "не выключайте плату");
    }
    delay(20);
    return;
  }

  // --- датчик света: усредняем, раз в секунду
  static uint32_t lastLux = 0;
  static float luxAvg = -1;
  if (now - lastLux > 1000) {
    lastLux = now;
    int raw = analogRead(PIN_LDR);
    luxAvg = luxAvg < 0 ? raw : luxAvg * 0.8f + raw * 0.2f;
    lux = (int)luxAvg;
  }
  // яркость с сервера (поменялась — применяем, если экран сейчас «ярко»)
  if (backlight == 2 && appliedBright != sigBright) setBacklight(2);

  // --- кадр
  if (frameReady) {
    drawTiles(frameBuf, frameLen);
    frameReady = false;
    shownOffline = false;
  }
  if (!online && !shownOffline && now - lastOkMs > 6000) {
    shownOffline = true;
    message("Нет связи с сервером", hubUrl, WiFi.status() == WL_CONNECTED ? "Wi-Fi есть, сервер молчит" : "нет Wi-Fi",
            "BOOT при включении — настройка");
  }

  // --- сигналы с сервера
  char ledMode[16], beep[12];
  uint32_t bseq, wseq, dimS, offS;
  portENTER_CRITICAL(&sigMux);
  strlcpy(ledMode, sigLed, sizeof ledMode);
  strlcpy(beep, sigBeep, sizeof beep);
  bseq = sigBeepSeq; wseq = sigWakeSeq; dimS = sigDimS; offS = sigOffS;
  portEXIT_CRITICAL(&sigMux);
  if (!beepInit && online) { beepInit = 1; lastBeep = bseq; lastWake = wseq; }   // старые сигналы не играем
  if (beepInit && bseq != lastBeep) {
    lastBeep = bseq;
    const Note* s = patternSound(beep);
    if (s) play(s);
  }
  if (beepInit && wseq != lastWake) {
    lastWake = wseq;
    setBacklight(2);
    lastTouchMs = now;
  }
  ledUpdate(ledMode, online);
  soundUpdate();

  // --- подсветка
  uint32_t idle = (now - lastTouchMs) / 1000;
  if (backlight == 2 && dimS && idle > dimS) setBacklight(1);
  if (backlight == 1 && offS && idle > offS) setBacklight(0);
  delay(5);
}
