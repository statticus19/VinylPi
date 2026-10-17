// CrowPanel Remote BMP v3 - exact refresh sequence from known-good weather sketch
#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <esp_heap_caps.h>
#include "EPD.h"
#include "EPD_GUI.h"

// Create secrets.h from secrets.example.h; never commit secrets.h.
#include "secrets.h"
constexpr uint32_t POLL_MS = 30000;
constexpr size_t FRAME_BYTES = 15000;
constexpr size_t MAX_BMP_BYTES = 500000;

uint8_t Image_BW[FRAME_BYTES];
uint8_t previousFrame[FRAME_BYTES];
bool havePrevious = false;
uint32_t lastPoll = 0;

uint16_t le16(const uint8_t* p) {
  return uint16_t(p[0]) | (uint16_t(p[1]) << 8);
}
uint32_t le32(const uint8_t* p) {
  return uint32_t(p[0]) | (uint32_t(p[1]) << 8) |
         (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24);
}
void pixel(int x, int y, bool white) {
  size_t offset = size_t(y) * 50 + x / 8;
  uint8_t mask = 0x80 >> (x % 8);
  if (white) Image_BW[offset] |= mask;
  else Image_BW[offset] &= ~mask;
}
bool decodeBMP(const uint8_t* bmp, size_t length) {
  if (length < 54 || bmp[0] != 'B' || bmp[1] != 'M') return false;
  uint32_t dib = le32(bmp + 14);
  if (dib < 40 || uint64_t(14) + dib > length) return false;
  int32_t w = int32_t(le32(bmp + 18));
  int32_t h = int32_t(le32(bmp + 22));
  uint16_t bpp = le16(bmp + 28);
  uint32_t offset = le32(bmp + 10);
  if (w != 400 || (h != 300 && h != -300) ||
      le16(bmp + 26) != 1 || le32(bmp + 30) != 0 ||
      (bpp != 1 && bpp != 24 && bpp != 32)) {
    Serial.println("BMP must be uncompressed 400x300, 1/24/32 bpp");
    return false;
  }
  uint32_t rowBytes = ((uint32_t(w) * bpp + 31) / 32) * 4;
  if (uint64_t(offset) + uint64_t(rowBytes) * 300 > length) {
    Serial.println("Truncated BMP pixel data"); return false;
  }
  bool paletteWhite[2] = {false, true};
  if (bpp == 1) {
    size_t palette = 14 + dib;
    if (palette + 8 > length || palette + 8 > offset) return false;
    for (int i = 0; i < 2; i++) {
      const uint8_t* c = bmp + palette + i * 4;
      paletteWhite[i] = (uint32_t(c[2]) * 299 +
                         uint32_t(c[1]) * 587 +
                         uint32_t(c[0]) * 114) >= 128000;
    }
  }
  memset(Image_BW, 0xff, FRAME_BYTES);
  for (int y = 0; y < 300; y++) {
    int srcY = h > 0 ? 299 - y : y;
    const uint8_t* row = bmp + offset + size_t(srcY) * rowBytes;
    for (int x = 0; x < 400; x++) {
      bool white;
      if (bpp == 1) {
        int paletteIndex = (row[x / 8] >> (7 - x % 8)) & 1;
        white = paletteWhite[paletteIndex];
      } else {
        const uint8_t* c = row + size_t(x) * (bpp / 8);
        white = (uint32_t(c[2]) * 299 + uint32_t(c[1]) * 587 +
                 uint32_t(c[0]) * 114) >= 128000;
      }
      pixel(x, y, white);
    }
  }
  return true;
}
bool fetchBMP() {
  WiFiClient client;
  HTTPClient http;
  http.setConnectTimeout(5000);
  http.setTimeout(10000);
  if (!http.begin(client, IMAGE_URL)) return false;
  int status = http.GET();
  Serial.printf("HTTP status: %d\n", status);
  if (status != HTTP_CODE_OK) { http.end(); return false; }
  int contentLength = http.getSize();
  if (contentLength > int(MAX_BMP_BYTES)) {
    Serial.println("BMP too large"); http.end(); return false;
  }
  Serial.printf("PSRAM found: %s; free PSRAM: %u; free heap: %u\n",
                psramFound() ? "yes" : "no",
                unsigned(ESP.getFreePsram()), unsigned(ESP.getFreeHeap()));
  if (contentLength <= 0) {
    Serial.println("Server must send Content-Length for this test build");
    http.end(); return false;
  }
  const size_t capacity = size_t(contentLength);
  uint8_t* bmp = (uint8_t*)heap_caps_malloc(capacity, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  if (!bmp) bmp = (uint8_t*)malloc(capacity);
  if (!bmp) {
    Serial.printf("BMP allocation failed: requested %u bytes. Enable OPI PSRAM in Arduino Tools.\n", unsigned(capacity));
    http.end(); return false;
  }
  WiFiClient* stream = http.getStreamPtr();
  size_t received = 0;
  uint32_t lastData = millis();
  bool complete = false;
  while (millis() - lastData < 10000) {
    int avail = stream->available();
    if (avail > 0) {
      size_t count = min(size_t(avail), capacity - received);
      if (!count) break;
      int n = stream->read(bmp + received, count);
      if (n > 0) { received += size_t(n); lastData = millis(); }
    } else if (contentLength >= 0 && received == size_t(contentLength)) {
      complete = true; break;
    } else if (!http.connected()) {
      complete = (contentLength < 0 || received == size_t(contentLength));
      break;
    } else delay(1);
    if (contentLength >= 0 && received == size_t(contentLength)) {
      complete = true; break;
    }
  }
  http.end();
  Serial.printf("Received %u bytes\n", unsigned(received));
  bool ok = complete && decodeBMP(bmp, received);
  free(bmp);
  if (!ok) Serial.println("Download incomplete or BMP invalid");
  return ok;
}
void connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Connecting");
  uint32_t started = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - started < 15000) {
    Serial.print("."); delay(500);
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("IP: "); Serial.println(WiFi.localIP());
  } else Serial.println("WiFi not connected; will retry");
}

uint32_t countBlackPixels(const uint8_t* frame) {
  uint32_t whitePixels = 0;
  for (size_t i = 0; i < FRAME_BYTES; i++) {
    whitePixels += __builtin_popcount((unsigned)frame[i]);
  }
  return uint32_t(EPD_W) * uint32_t(EPD_H) - whitePixels;
}

uint32_t frameHash(const uint8_t* frame) {
  uint32_t hash = 2166136261UL; // FNV-1a
  for (size_t i = 0; i < FRAME_BYTES; i++) {
    hash ^= frame[i];
    hash *= 16777619UL;
  }
  return hash;
}

void checkImage() {
  connectWiFi();
  if (WiFi.status() != WL_CONNECTED || !fetchBMP()) return;

  uint32_t blackPixels = countBlackPixels(Image_BW);
  uint32_t hash = frameHash(Image_BW);
  Serial.printf("Decoded frame: %lu black pixels / 120000; hash: %08lX\n",
                (unsigned long)blackPixels, (unsigned long)hash);

  if (havePrevious && memcmp(Image_BW, previousFrame, FRAME_BYTES) == 0) {
    Serial.println("Image unchanged; skipping refresh");
    return;
  }

  Serial.println("New image; using the SAME refresh path as the known-good weather sketch");

  // Paint_NewImage only tells the GUI library which framebuffer is active.
  // It does NOT erase or alter Image_BW.
  Paint_NewImage(Image_BW, EPD_W, EPD_H, 0, WHITE);

  // This sequence intentionally matches UI_weather_forecast() from the
  // user's known-good Elecrow weather project.
  Serial.println("EPD: reset");
  EPD_RESET();
  delay(100);

  Serial.println("EPD: init");
  EPD_Init();
  delay(300);

  Serial.println("EPD: EPD_Display_Fast()");
  EPD_Display_Fast(Image_BW);   // Known-good project uses this, not EPD_Display()

  // The known-good project waits before entering deep sleep.
  delay(1000);
  Serial.println("EPD: sleep");
  EPD_Sleep();

  memcpy(previousFrame, Image_BW, FRAME_BYTES);
  havePrevious = true;
  Serial.println("Display refresh sequence finished");
}
void setup() {
  Serial.begin(115200);
  delay(500);

  // Preserve the exact power/enable setup from the known-good weather sketch.
  pinMode(41, OUTPUT);
  digitalWrite(41, HIGH);

  pinMode(7, OUTPUT);
  digitalWrite(7, HIGH);

  EPD_GPIOInit();

  // Diagnostic clear on boot. This deliberately follows the successful
  // weather project so we can prove the panel is physically refreshing
  // before the network image is sent.
  Serial.println("EPD boot test: clearing display to white...");
  EPD_RESET();
  delay(100);
  EPD_Init();
  delay(500);
  EPD_Clear();
  delay(500);
  Serial.println("EPD boot test clear finished");

  connectWiFi();
  checkImage();
  lastPoll = millis();
}
void loop() {
  if (millis() - lastPoll >= POLL_MS) {
    lastPoll = millis();
    checkImage();
  }
  delay(100);
}
