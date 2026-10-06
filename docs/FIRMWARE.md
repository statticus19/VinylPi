# CrowPanel firmware

Use the Elecrow CrowPanel ESP32-S3 4.2-inch e-paper display, 400 × 300 pixels.
Open `firmware/CrowPanel_Remote_BMP/CrowPanel_Remote_BMP.ino` in Arduino IDE;
keep all sibling `EPD*` files in that sketch folder.

1. Install the **esp32 by Espressif Systems** board package through Boards Manager.
2. Copy `secrets.example.h` to `secrets.h` in the sketch folder. Fill in a 2.4 GHz
   Wi-Fi network and `http://YOUR_PI_LAN_IP:5001/display.bmp`. Use the Pi's
   configured port if changed; find its IP with `hostname -I`.
3. Under Tools select **ESP32S3 Dev Module**, **Huge APP (3MB No OTA/1MB SPIFFS)**,
   and **OPI PSRAM**, as in [Elecrow's tutorial](https://media-cdn.elecrow.com/wiki/CrowPanel_ESP32_E-Paper_4.2-inch_Arduino_Tutorial.html).
   Select the connected serial port and upload.
4. Open Serial Monitor at **115200 baud**. A boot clear, Wi-Fi connection,
   HTTP 200, and `Received 15662 bytes` should be followed by a decoded frame
   and display refresh. Unchanged images skip refresh.

The decoder accepts uncompressed 400 × 300 BMPs at 1, 24, or 32 bits per pixel.
It does not decode PNG. The server must send a Content-Length header.
The Pi supplies a monochrome BMP, including row padding and palette.

The firmware retains the working panel power-enable pins and reset/init/fast
display/sleep delays. It polls every 30 seconds; adjust `POLL_MS` if desired.
Do not mix drivers for another panel size. A blank boot clear followed by an HTTP
error points to the URL/server/network; a valid decoded frame with no panel update
requires checking board settings, drivers, power, and refresh initialization.

`secrets.h` and compiled `.bin` files are ignored. Compiled firmware can contain
Wi-Fi credentials, so distribute source and placeholders rather than your flashed image.
Driver source reference: [Elecrow's 4.2-inch repository](https://github.com/Elecrow-RD/CrowPanel-ESP32-4.2-E-paper-HMI-Display-with-400-300).
