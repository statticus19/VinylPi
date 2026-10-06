#pragma once
// Use a 2.4 GHz Wi-Fi network that can reach your Raspberry Pi.
const char* WIFI_SSID = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
// Run hostname -I on the Pi; use its LAN IP and the port in config.json.
const char* BMP_URL = "http://YOUR_PI_LAN_IP:5001/display.bmp";
