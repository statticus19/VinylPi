# CrowPanel Remote BMP v3 (review package)

Source provided from the user’s CrowPanel_Remote_BMP ZIP. The bundled panel drivers and v3 refresh sequence are retained; only configuration literals were moved to an untracked `secrets.h`.

1. Copy `secrets.example.h` to `secrets.h` in this sketch directory.
2. Set local Wi-Fi settings and the Raspberry Pi `/display.bmp` URL.
3. Select the ESP32-S3 CrowPanel board settings used by your working device; verify those settings before flashing.
4. Compile/upload using the Arduino setup and dependencies documented in the main project; this package has not been hardware-tested.

The display expects a 400×300 monochrome BMP and polls every 30 seconds. Do not commit `secrets.h`.

**Important:** This is the provided v3 source, not a readback of flashed firmware. No heartbeat code is visibly present in the supplied main sketch. Validate the dashboard's physical-display online indicator against the firmware deployed in use.
