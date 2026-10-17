# Raspberry Pi Last.fm e-paper display

**Rotating corner:** See [ROTATING_CORNER.md](ROTATING_CORNER.md) for installation
and optional settings. The idle screen rotates UK men's basketball (only if a
game is in the next week), Last.fm stats, sun times, and original Pokémon sprites
every 15 minutes. The UK schedule refreshes weekly and supports future seasons
automatically. Images and Pokémon names are bundled in `assets/`.

The corner is now narrower to restore larger calendar detail text. Calendar
details use an 11-pixel font; the Upcoming header is removed to allow more space
between events. Crowded details wrap, and long titles end with an ellipsis.
UK cards include published TV station information; Last.fm uses Scrobbles Today
and Top Weekly Artist, with a larger artist plays line. Sun cards show the two
times without the former Next line.

**Updated dashboard:** If upgrading an installed copy, follow
[UPDATE_FROM_V1.md](UPDATE_FROM_V1.md) to keep your existing credentials and calendar IDs.
The weather screen now includes today/tomorrow forecasts, precipitation, and
condition icons and current UV; calendar titles sit above details with Today
or weekday month/day dates, Zoom/Teams, and inline driving estimates. Tomorrow's
forecast is a smaller strip beneath today's weather.

This companion service draws a 400×300, uncompressed, 1-bit BMP and serves it
at `/display.bmp` on port 5000. Your existing Arduino BMP client can download it
the same way it downloaded the test image. The Pi handles Last.fm, weather,
Google authorization, and drawing; the ESP32 only handles HTTP and display refresh.

Your supplied `orchestrator.py` currently samples the local Icecast `/vinyl`
stream with FFmpeg, uses Shazam to identify audio, and sends tracks to Last.fm.
Keep that audio pipeline running. This service reads the account independently,
so other music players scrobbling to the same Last.fm account also appear.
It does not open the Behringer USB interface or change its audio configuration.

## Display behavior

The music screen uses the same date/time header as the idle screen and a
Now Playing (or Last Played) bar at the bottom. The music header also shows
the current weather icon, temperature and condition. The album name sits below the
artist in italics. The artwork is 180×180 pixels. Artwork supplied by Last.fm
is shown in monochrome; a record graphic
is used when artwork is missing or cannot be downloaded.
The italic font and its license are bundled in `assets/fonts`; include this
folder when upgrading the music layout.

* Poll Last.fm every 15 seconds; show only tracks explicitly marked now playing.
* When a successful Last.fm response first reports no playing track, show
  **Last played** for 600 seconds, then switch to weather and upcoming events.
* Resume the music screen at the next successful active poll.
* Combine the next three events across the configured calendars, with labels.
  Include recurring instances, all-day events, and events already in progress.
  Exclude titles equal to "Out of Office" or "GROW TRAINIG", ignoring case and
  extra spaces. Detect Zoom/Teams
  URLs and explicit physical addresses; optional routing adds estimated car times.
* Fetch calendar data every five minutes while idle and weather every 15 minutes
  in both music and idle modes.
  The idle clock is rounded down to five minutes to reduce e-paper refreshes.
* A Last.fm outage preserves the last known playback state and labels it cached;
  it does not count an API failure as silence. Cached weather/events are labelled.
  With no previous music state, startup goes directly to the idle dashboard.
* The BMP changes only when the rendered pixels change. The server supports
  ETag / 304, but your current Arduino client can keep downloading and comparing
  the 15,662-byte BMP without any firmware change.

**Timing limit:** this follows Last.fm status, not physical silence at the USB
interface. Last.fm may continue reporting a song after the player stops, so
the ten minutes starts when the API first confirms inactivity. The Last.fm poll
and Arduino poll each add their own polling delay. Do not infer silence from a
scrobble timestamp. Exact audio-based stop timing would need an activity signal
from your audio pipeline; that is not enabled in this account-wide version.

## 1. Install on the Raspberry Pi

Use Raspberry Pi OS with Python 3.10 or newer. Extract this directory as
`~/pi_epaper`. These commands run on the Pi, not in Arduino IDE:

```bash
cd ~/pi_epaper
sudo apt update
sudo apt install python3-venv fonts-dejavu-core
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.example.json config.json
chmod 600 config.json
```

Edit `config.json`:

* Configure `lastfm.username` with your own Last.fm account name.
* Fill in `lastfm.api_key` using your existing Last.fm API key. The reader needs
  no Last.fm password or API secret. Alternatively use the `LASTFM_API_KEY`
  environment variable. No real credentials are included in this download.
* Set your actual `timezone` (currently `America/New_York`).
* Weather is configured for ZIP 41011 (Covington, Kentucky), using approximate
  ZIP-centroid coordinates 39.06802, -84.53065 and Fahrenheit. You can adjust
  these coordinates to a more specific location if desired.
* Replace each Google calendar ID placeholder with a real ID; add or remove
  entries as needed. Each calendar may have a short display label.

Your configured weather block:

```json
"weather": {
  "label": "Covington, KY 41011",
  "latitude": 39.06802,
  "longitude": -84.53065,
  "temperature_unit": "fahrenheit"
}
```

Open-Meteo is used for weather, with no weather API key required for its free
non-commercial endpoint. See its current terms at https://open-meteo.com/en/terms.

## 2. Authorize Google Calendar once

The service uses Google Calendar API with read-only access. It does not require
connecting your Google account to ChatGPT. Authorization happens on your Pi.

1. Follow Google's setup guide to create a Google Cloud project, enable Google
   Calendar API, configure OAuth consent, and create a **Desktop app** OAuth client:
   https://developers.google.com/workspace/calendar/api/quickstart/python
2. For a personal Google account, use an External audience and add your account
   as a test user while testing. Download the OAuth client JSON as
   `~/pi_epaper/credentials.json`. Keep `credentials.json` and `token.json` private.
3. On a Pi with a browser, run:

```bash
cd ~/pi_epaper
.venv/bin/python authorize_calendar.py
```

For a headless Pi, open a new terminal on your desktop and connect with a tunnel:

```bash
ssh -L 8765:localhost:8765 YOUR_PI_USER@YOUR_PI_IP
```

Inside that SSH session run:

```bash
cd ~/pi_epaper
.venv/bin/python authorize_calendar.py --headless
```

Open the printed URL in your desktop browser while leaving SSH connected.
After consent, the browser redirects to `localhost:8765` through the tunnel.
The script saves `token.json` and prints available calendar names and IDs.
Copy the desired IDs into `config.json`; no event-editing permission is requested.
You can also find IDs in Google Calendar settings under **Integrate calendar**.

If the OAuth app remains in External **Testing**, Google may expire its refresh
token after seven days. For unattended use, configure its publishing status
appropriately or rerun authorization when needed; do not assume a test token is
permanent. See https://developers.google.com/identity/protocols/oauth2.

## 3. Test and connect the e-paper

```bash
cd ~/pi_epaper
.venv/bin/python -m unittest -v
.venv/bin/python display_service.py --demo
.venv/bin/python display_service.py
```

Open `http://YOUR_PI_IP:5000/preview.png` in a browser to see the live screen;
`http://YOUR_PI_IP:5000/status` gives provider availability and display mode;
`http://YOUR_PI_IP:5000/credits` provides source attribution.
`http://YOUR_PI_IP:5000/display.bmp` is the URL for Arduino's `IMAGE_URL`.
Use the Pi address assigned on your network and update the configured URL accordingly.
Give the Pi a DHCP reservation so its address stays consistent.

**Port conflict:** your current bitmap server may already own port 5000.
Run this first on another port (edit `port` to 5001) or stop the old image server
before this one takes its place. Do not stop Icecast or the audio orchestrator.

To shorten the idle test, temporarily set `idle_seconds` to 30, play music, stop
the player, and watch `/status`. Once Last.fm no longer reports active playback,
the display should go from `playing` to `recent` to `idle`. Restore 600 afterward.
Each ESP32 refresh still depends on the working panel driver and its polling
interval. This package has offline tests and layout checks, not hardware validation.

The server only serves its four fixed routes; it never serves the project
directory or OAuth files. It binds on the LAN by default. Calendar previews
are visible to devices that can access that port, so keep it on your private LAN.

## 4. Start automatically

```bash
cd ~/pi_epaper
cp pi-epaper.service.example pi-epaper.service
nano pi-epaper.service
```

Replace `YOUR_PI_USER` and all three path occurrences with the real Pi account
and install directory. Complete Google authorization interactively first. Then:

```bash
sudo cp pi-epaper.service /etc/systemd/system/pi-epaper.service
sudo systemctl daemon-reload
sudo systemctl enable --now pi-epaper.service
journalctl -u pi-epaper.service -f
```

If you prefer an environment API key instead of putting it in config.json, add
`EnvironmentFile=/home/YOUR_PI_USER/pi_epaper/display.env` to the service and
create a private file containing `LASTFM_API_KEY=your_actual_key`.

## Optional update to your audio orchestrator

`orchestrator_display_ready.py` is an optional separate copy of your uploaded
script. It renews Last.fm now-playing on each successful Shazam match instead
of only on a track change. It retains the original immediate scrobble-on-change
behavior; it is not a redesign of scrobble timing. It also forces the 16-bit
PCM WAV format its RMS code expects and bounds FFmpeg capture to 40 seconds.

The copy reads Last.fm credentials from environment variables. Your uploaded
script embeds a password and API secret; these values are deliberately excluded
from this package. Migrate to local configuration and change any credentials
you no longer consider private.

Your existing orchestrator environment already needs `pylast`, `shazamio`, and
FFmpeg. These are not required by the display service and are not in its
requirements file. To test the optional copy in that existing audio environment:

```bash
cp orchestrator.env.example orchestrator.env
chmod 600 orchestrator.env
nano orchestrator.env
set -a
. ./orchestrator.env
set +a
python orchestrator_display_ready.py
```

Stop the old orchestrator before running this copy to avoid duplicate scrobbles.
For systemd, set `EnvironmentFile` to this filled file in the audio service and
point its `ExecStart` at the optional copy. Its existing audio behavior is retained;
repeated tracks after silence and recognition failures have the same limitations
as the original. Last.fm status renewal only occurs on successful identification.

## Files and validation

`demo/now_playing.png` and `demo/idle.png` are sample layouts, not live data.
Matching 1-bit BMPs are provided for direct display tests. The account reader,
ten-minute transition, restart state, outage handling, BMP headers/pixels,
ETags, and endpoint behavior are covered by offline tests. Live Last.fm,
Google OAuth, weather, Raspberry Pi audio, and physical panel behavior require
testing on your devices with your configuration.

API references:

* https://www.last.fm/api/show/user.getRecentTracks
* https://developers.google.com/workspace/calendar/api/v3/reference/events/list
* https://open-meteo.com/en/docs
* ZIP 41011 coordinates: https://simplemaps.com/us-zips/41011/
