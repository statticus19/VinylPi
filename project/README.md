# VinylPi — Public source review

VinylPi connects a Raspberry Pi to a turntable via a USB audio interface, recognizes tracks, scrobbles to Last.fm, streams audio over Icecast, generates a 400×300 BMP for an Elecrow CrowPanel ESP32-S3, and provides a web dashboard and playlist integrations.

**Status:** Offline-reviewed source snapshot, not a turnkey release or installation-tested package. See [`docs/PUBLICATION-DECISION.md`](docs/PUBLICATION-DECISION.md) and [`docs/OFFLINE-VALIDATION-AND-LIMITATIONS.md`](docs/OFFLINE-VALIDATION-AND-LIMITATIONS.md). No live installation test was requested or performed. No Synology NAS management components are included in the public dashboard.

## Components

- `pi_epaper/`: recognition integration, display renderer, weather, calendar, Last.fm, learning, and BMP services.
- `vinyl_orchestrator/`: original track-recognition orchestration source. **Do not run alongside the active `pi_epaper/orchestrator_display_ready.py` without deciding which systemd override applies.**
- `firmware/`: the owner-confirmed current CrowPanel remote-BMP v3 sketch and companion driver source; Wi-Fi/BMP URL are configured locally.
- `music-bridge/`: playlist conversion, MusicLink sharing, quota/notification services.
- `vinylpi-dashboard/`: NAS-free authenticated dashboard; stream, playlists, display, notifications, learning, service health.
- `vinylpi-guest/`: limited guest stream player.
- `support/`, `systemd-snapshot/`: service-control broker and **site-specific**, non-installable service references.
- `config-examples/`: generic examples that must be adapted to local installation.
- `docs/`: installation/service map, optional assets, security review and limitations.

## Before using

1. Read [`docs/INSTALLATION_AND_SERVICE_MAP.md`](docs/INSTALLATION_AND_SERVICE_MAP.md), [`docs/FRESH_INSTALL_GAPS.md`](docs/FRESH_INSTALL_GAPS.md), and [`docs/OPTIONAL-EPAPER-ASSETS.md`](docs/OPTIONAL-EPAPER-ASSETS.md).
2. Inspect public configuration examples and supply your own Last.fm, Google, YouTube, Discord, weather and routing credentials. Never commit credentials, access tokens, local paths or home addresses.
3. Install the appropriate system packages, Python environments and firmware libraries before enabling any service. The bundled `systemd-snapshot/` files describe an existing Pi and must not be installed verbatim.
4. Run read-only checks: `python3 tools/install_preflight.py` and `python3 tools/prepublish_check.py`. The regression tests are `python3 -m pytest -q tests/` in an isolated environment with testing dependencies installed.
5. Treat NAS management as intentionally absent. Preserve personal NAS integrations in a separate, encrypted private backup.

## Offline tests and limitations

The test suite exercises NAS-free dashboard login/authenticated route boundaries, broker control constraints, and configuration helpers. Passing tests does not establish that audio hardware, Shazam, Icecast, Plex/Threadfin, OAuth, Discord, or the CrowPanel behave correctly when freshly deployed. End users are responsible for adapting and securing their installations. See the documented installation gaps.

## Third-party artwork

Pokemon sprites and university logos are not bundled. Use the optional asset installer/documentation, or disable the corresponding panels via `corner.pokemon: false` and `corner.uk: false`. Install DejaVu fonts from your OS package manager. Dashboard artwork from the original Pi snapshot needs provenance/permission verification before a public repo is released.

## Publication security

The original repository history requires full secret and license scanning before changing repo visibility. A partial content-screening report is in [`docs/HISTORY-CONTENT-AUDIT.md`](docs/HISTORY-CONTENT-AUDIT.md). Change passwords/tokens if they were ever embedded in other files or backups. This package is not an encrypted private backup.
