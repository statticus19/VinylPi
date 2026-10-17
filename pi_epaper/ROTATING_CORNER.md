# Rotating idle-screen corner

The weather/calendar bitmap now reserves a narrower 128×108-pixel panel at the
bottom right. Calendar details use an enlarged 11-pixel font, with 239 pixels
of usable width. Long event titles receive a measured-width ellipsis. Crowded
details split across two lines: date/time first, then the calendar, Zoom/Teams,
and car time together. Removing the Upcoming header gives each event more
vertical space. Three events still fit without overlapping the panel.
The now-playing screen still fills the display while music is active.

## Install using WinSCP and PuTTY

1. Download and extract the latest ZIP on Windows.
2. Stop the foreground display script with **Ctrl+C** in PuTTY. If using the
   installed service, run `sudo systemctl stop pi-epaper.service` instead.
3. In WinSCP, copy the contents of the ZIP's `pi_epaper` folder into your
   existing `~/pi_epaper` folder, replacing matching files. Include the new
   `corner_rotation.py`, `corner_render.py`, `test_corner_rotation.py`, and the
   **entire `assets` directory**. Also replace `dashboard.py`,
   `display_service.py`, and the updated test files.
4. Keep your existing `config.json`, `credentials.json`, `token.json`, and
   `orchestrator.env`. Do not rename `config.example.json` over your configuration.
5. In PuTTY:

```bash
cd ~/pi_epaper
.venv/bin/python -m unittest -v
.venv/bin/python display_service.py --demo
.venv/bin/python display_service.py
```

For systemd, use `sudo systemctl start pi-epaper.service` after the checks.
Refresh `http://YOUR_PI_IP:5001/preview.png`, using your configured port.
The panel appears in idle mode after the existing ten-minute music timeout.
No new Python dependencies, API keys, or Google authorization are required.
The previously configured Last.fm API key supplies listening statistics.

## Rotation and availability

Each eligible card appears for **15 minutes**. The order is UK basketball,
Last.fm, sunrise/sunset, and Pokémon, skipping unavailable items. Requests run
in a background thread; a slow schedule or stats request does not block the
bitmap server or music polling. Rotation timing uses the Pi's clock. It is
not a fifteen-minute animation or rapid e-paper refresh.

* **UK men's basketball:** reads the current official UK Athletics schedule
  at https://ukathletics.com/sports/mbball/schedule/ **once a week**. The saved
  last-success time is reused after restarts, so restarting does not fetch an
  otherwise fresh schedule again. No season year is hardcoded: future seasons
  automatically work when UK publishes them on this same page. A change to
  the page's structured data format may need a parser update. The nearest
  upcoming opponent game inside the next seven days is shown, with date,
  local time, opponent, Home/Away/Neutral site, venue/city, the UK logo, and a
  drawn basketball. A **TV:** line appears when the official schedule includes
  a recognized station logo, including its encoded imgproxy source. The
  station is matched by opponent and date, not inferred from an event sponsor
  or a generic Watch link. Missing/unrecognized station listings are omitted.
  This update performs one initial schedule refresh to add TV fields to older
  cached data; subsequent successful refreshes remain weekly.
  Real opponent exhibitions count; Big Blue Madness,
  internal Blue-White scrimmages, cancelled games, and postponed games do not.
  Date-only listings display **Time TBD**, including an unannounced-time game
  on today's date. For such games, dates through the seventh local day are
  eligible; confirmed times use a strict 168-hour window. Timestamps without
  an offset are interpreted as Eastern time, then converted to the configured
  display timezone. Started games disappear. With no eligible game,
  basketball is absent from the rotation. Schedule changes are picked up at
  the next weekly refresh.
* **Last.fm:** displays today's completed scrobble count since local midnight
  and the top artist/artist play count over Last.fm's rolling seven-day period.
  The large count is labelled **Scrobbles Today**. The artist is labelled
  **Top Weekly Artist**, and its count reads **42 plays** (example), using
  an enlarged 11-pixel font for clarity.
  Now-playing status does not add an unfinished track to the count. Data
  refreshes every fifteen minutes and shortly after the local date changes.
* **Sunrise/sunset:** uses the existing weather coordinates and requests both
  times in the normal weather update. Shows today's sun times. After sunset it
  shows tomorrow's times. The former **Next:** line has been removed.
* **Pokémon:** selects a random original Red/Blue front sprite from the first
  **151 Pokémon**, with its name and Pokédex number. A sprite stays fixed during
  each visit; the following Pokémon visit selects a different one. All images
  and names are bundled locally, so this card works without internet access.
  Sprites are cropped to remove transparent padding and rendered as
  monochrome pixel art. Pokémon are not animated.

Failed schedule requests retry after one hour; failed stats requests after
five minutes. Schedule data remains usable for nine days following a successful
fetch, allowing a two-day grace period after the weekly update is due. It is
marked cached after seven days and hidden after nine without a successful
fetch. Cached Last.fm stats are marked after 30 minutes and only shown for the
same local day; they are hidden after 24 hours without a successful fetch.
Game eligibility is rechecked on each render, even when source data is cached.
Unpopulated cards are omitted, rather than displaying an invented game or
zero listening statistics. `state/corner.json` stores the cache privately.
`/status` includes the current card type as `corner_widget`.

## Optional settings

Your existing config works without edits. To change the defaults, add this
top-level object to `config.json`, leaving your other settings intact:

```json
"corner": {
  "enabled": true,
  "seconds": 900,
  "uk": true,
  "stats": true,
  "sun": true,
  "pokemon": true
}
```

Set an individual item to `false` to omit it; set `enabled` to `false` to restore
full-width calendar rows. `seconds` controls how long each card remains visible,
with a minimum of 60 seconds. Fifteen minutes suits the e-paper display.

## Example images

`display_service.py --demo` generates four examples:

* `demo/rotation_uk.png` and `.bmp`
* `demo/rotation_lastfm.png` and `.bmp`
* `demo/rotation_sun.png` and `.bmp`
* `demo/rotation_pokemon.png` and `.bmp`

`demo/rotation_calendar_wrap.png` and `.bmp` show a long title ending in an
ellipsis and a crowded hybrid event with date/time on one line, then calendar,
Zoom/Teams, and driving time on the next.

These are layout examples set to **November 7, 2026**, not live account/weather
screens. Weather, calendar events, drive time, listening stats, and sun times
are illustrative. The basketball example uses the published November 10
Kansas game at the United Center on ESPN as of this update; the runtime always fetches
the current schedule. Pikachu is one example of the random sprite selection.
All BMPs remain uncompressed, one-bit, 400×300 images for the existing display
download method. View the PNGs through WinSCP or locally after extracting the ZIP.

Source links and image credits are in `CREDITS.txt` and the existing `/credits`
page. Tests cover game windows/TBD times, source parsing, future dates, DST
midnight stats, omitted cards, sunset transitions, stable/random sprite visits,
weekly refresh persistence, TV-logo matching, readable/wrapped event details,
failure backoff, cache persistence, and bitmap layouts.
