# Update the Pi before step 9

For the latest rotating corner update, also follow
[ROTATING_CORNER.md](ROTATING_CORNER.md) and copy its new modules and the
complete `assets` folder. Existing configuration and credentials remain in use.

This update gives weather more space, removes the location/provider footer from
the bitmap, adds code-drawn condition icons, today's precipitation forecast,
tomorrow's forecast, smaller calendar titles, Zoom/Teams badges, and car/time
indicators. Events titled "Out of Office" or "GROW TRAINIG" are omitted, ignoring capitalization
and extra spaces. The next three eligible events are still merged across calendars.

The latest refinement puts each event title above its details. Dates show
`Today` for events starting on the display's current local day and otherwise
use `Mon 10/5` format, followed by the event time (or `all day`). These labels
update across midnight even while calendar data is cached. Calendar labels,
Zoom/Teams badges, and the car/drive time share the details line. Tomorrow is
now a smaller, unboxed strip, giving the calendar rows more breathing room.

The precipitation indicator now switches from a raindrop to a snowflake when
today's daily forecast calls for snow, snow grains, or snow showers. It uses
the daily forecast rather than current conditions, so a clear morning can
still show snow expected later today. Rain and freezing rain use the droplet.
`demo/idle_snow.png` demonstrates this with sample data. Precipitation amounts
remain liquid-equivalent inches, not inches of accumulated snow.

The current modeled UV index appears beside `Feels like`, with the requested
three-level labels: **Low** below 3, **Medium** from 3 to below 6, and **High**
at 6 or above. This simplified High category includes the standard Very High
and Extreme bands; the numeric index stays visible. Missing UV data shows
`UV —`. UV uses current conditions, not the day's maximum.

If you already installed the previous weather/routing update, this refinement
only requires replacing `dashboard.py` and `display_service.py`. Also replace
`test_dashboard_features.py` and `test_display_service.py` to run the latest
checks. Use your existing configuration and authorization files.

## Transfer the updated code

1. Download and extract the updated project ZIP on Windows.
2. Stop the foreground display service with Ctrl+C in its PuTTY window. If you
   already installed its systemd service, use `sudo systemctl stop pi-epaper.service`.
3. In WinSCP, open your existing user home directory, then its `pi_epaper` folder.
   Copy the **contents** of the ZIP's `pi_epaper` folder into that existing folder,
   allowing replacement of matching code files. This includes the new
   `dashboard.py`, `event_details.py`, `routing.py`, `configure_routing.py`,
   `test_dashboard_features.py`, and `CREDITS.txt`, plus the updated
   `display_service.py` and `test_display_service.py`.
4. Keep your existing `config.json`, `credentials.json`, `token.json`, and
   `orchestrator.env`. The archive contains no filled copies of these files.
   Do **not** copy `config.example.json` over your `config.json`.

No new Python dependencies are required beyond those installed for v1.

## Try the new layout

In PuTTY:

```bash
cd ~/pi_epaper
.venv/bin/python -m unittest -v
.venv/bin/python display_service.py --demo
.venv/bin/python display_service.py
```

Open your existing `http://YOUR_PI_IP:5001/preview.png` address on Windows and
refresh the page. If you configured a different port, keep using that port.
The new idle layout appears when music is inactive. The files in `demo/` show
sample data immediately, including an illustrative **18-minute** drive time;
that number is not a computed route from your home or an actual calendar event.

If running under systemd instead, start with:

```bash
sudo systemctl start pi-epaper.service
journalctl -u pi-epaper.service -f
```

## Activate free driving estimates

1. Create a free account and API key through the official HeiGIT/OpenRouteService
   account page: https://account.heigit.org/
   The key must have access to both **routing/directions** and **geocoding**.
2. Stop the display service again and run:

```bash
cd ~/pi_epaper
.venv/bin/python configure_routing.py
```

Paste the new routing key at the hidden prompt and press Enter. The helper adds
the routing settings to your **existing** configuration and keeps your Last.fm
key, calendar IDs, port, and timezone. It saves a private backup as
`config.before-routing.json`. This key is different from your Last.fm API key
and Google OAuth credentials.

Restart the foreground service:

```bash
.venv/bin/python display_service.py
```

Or, for systemd:

```bash
sudo systemctl start pi-epaper.service
```

The origin is configured as **202 McCrae Ln, Covington, KY 41011**. No origin
address is shown on the screen. When a displayed event includes an explicit
physical street address, the service geocodes the home and event address and
requests a driving route. Results may take a minute to populate initially.
The BMP remains available while route requests run in the background.

The car indicator shows `~18 min` when a route succeeds, `…` while it is pending,
and `—` when the key is missing or the address/route cannot be resolved. Times
are rounded up to whole minutes and **exclude live traffic**, parking, walking,
and preparation time. They are travel estimates from home, not leave-by times.

Successful routes are cached for 24 hours; geocoded coordinates for seven days.
Failures retry after one hour to avoid repeatedly using the free API quota.
Cached results persist through a service restart. Changing the API key or an
explicit coordinate override invalidates the relevant route lookup automatically.

## Meeting links and physical locations

Zoom/Teams badges are based on actual URLs in event locations, descriptions,
or conference metadata. Outlook Safe Links are unwrapped. Events with both a
meeting link and physical address can display both a platform badge and a car time.
Simply mentioning "Zoom" or "Teams" without a link does not trigger a badge.

Physical addresses are recognized in the event's `location` or `description`.
Numbered US street addresses work best. Names such as "Office", a room number,
or an address present only inside a maps hyperlink are not guessed. For an
ambiguous or unrecognized address, use a full street/city/state/ZIP address in
the event or configure an exact coordinate override in `config.json`:

```json
"address_overrides": {
  "EXACT_ADDRESS_TEXT": [-84.5000, 39.1000]
}
```

Those numbers are **illustrative**. Replace them with verified coordinates;
the order is **longitude, latitude**. An optional `origin_coordinates` value
uses the same order and bypasses home-address geocoding. The script deliberately
rejects uncertain house-number/street/postcode matches instead of displaying an
estimate from a nearby or guessed origin.

Google's subscribed Outlook feed may omit description or conference metadata
that Outlook itself shows. Badges and drive times can only use fields delivered
by Google's API. Your existing read-only Calendar authorization still suffices.

## Weather and source credits

Weather now requests two local days. Today's precipitation line shows the day's
maximum probability and, when at least 0.01 inch is forecast, its liquid-equivalent
precipitation amount (including rain, showers, and snow). A snowflake or filled droplet and
bold "Precip expected" appear if probability is at least 50% or the predicted
amount is at least 0.01 inch. Lower probabilities say "Precip chance"; missing
probability is shown as `—`, never invented as zero. These are full-day forecasts,
not forecasts only for the remaining hours today. Tomorrow includes conditions,
high/low, and precipitation probability/amount. All temperatures remain Fahrenheit.

Provider names and location are removed from the bitmap. Source attribution is
in `CREDITS.txt` and at `http://YOUR_PI_IP:5001/credits`. Weather/model licence:
https://open-meteo.com/en/terms. Driving API/geocoder endpoints use HeiGIT's
current domains:
https://ask.openrouteservice.org/t/deprecating-api-openrouteservice-org-in-favour-of-api-heigit-org/7912

`/status` reports calendar availability and routing status for displayed events;
it does not include your home address, event destinations, meeting links, or keys.
Your home and destination addresses are sent to HeiGIT's geocoder to compute
the estimates. The coordinate cache is stored privately on your Pi.

## Validation

Offline tests cover the original playback/BMP delivery behavior and the expanded
forecast, event filtering, meeting links, address extraction, route caching,
geocoder mismatch rejection, failures, and asynchronous result handling.
The updated layout has been visually checked at 400×300. Live Google/Outlook
metadata and routing still need testing on your Pi with your accounts and key.
