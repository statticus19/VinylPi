#!/usr/bin/env python3
"""Last.fm -> 400x300 BMP; ten-minute idle weather/calendar dashboard."""
import argparse
import hashlib
import io
import json
import logging
import os
import queue
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode, quote, urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont, ImageOps
from dashboard import render_dashboard, event_when, draw_date_header
from event_details import meeting_platforms, physical_address, excluded_event
from routing import DriveTimes
from corner_rotation import CornerRotation
from music_metadata import enrich_from_vinyl, useful_art, recognition_status

ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger("display")
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


def atomic_write(path, data, private=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    if private:
        tmp.chmod(0o600)
    os.replace(tmp, path)


def get_json(url, params=None, headers=None):
    if params:
        url += "?" + urlencode(params)
    with urlopen(Request(url, headers=headers or {}), timeout=12) as response:
        return json.loads(response.read(2_000_000))


def text_value(value):
    return str(value.get("#text", "")) if isinstance(value, dict) else str(value or "")


def parse_now_playing(payload):
    if "error" in payload:
        raise ValueError("Last.fm API error %s" % payload["error"])
    tracks = payload["recenttracks"].get("track", [])
    if isinstance(tracks, dict):
        tracks = [tracks]
    for track in tracks:
        if str(track.get("@attr", {}).get("nowplaying", "")).lower() == "true":
            images = track.get("image", [])
            return {"title": track.get("name", "Unknown track"),
                    "artist": text_value(track.get("artist")),
                    "album": text_value(track.get("album")),
                    "art_url": next((i.get("#text") for i in reversed(images)
                                     if useful_art(i.get("#text"))), "")}
    return None


class Playback:
    """Only a successful no-playing response starts the idle timer."""
    def __init__(self, data=None):
        self.data = data or {"track": None, "playing": False, "stopped_at": None,
                             "last_success": None}

    def observe(self, track, now):
        if track:
            self.data.update(track=track, playing=True, stopped_at=None)
        else:
            if self.data.get("playing"):
                self.data["stopped_at"] = now
            self.data["playing"] = False
        self.data["last_success"] = now

    def mode(self, now, idle_seconds=600):
        if self.data.get("playing") and self.data.get("track"):
            return "playing"
        stop = self.data.get("stopped_at")
        if self.data.get("track") and stop is not None and now - stop < idle_seconds:
            return "recent"
        return "idle"


def font(size, bold=False, italic=False):
    style = "-BoldOblique" if bold and italic else "-Bold" if bold else "-Oblique" if italic else ""
    name = "DejaVuSans%s.ttf" % style
    for path in (ROOT / "assets" / "fonts" / name, name,
                 "/usr/share/fonts/truetype/dejavu/" + name):
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            continue
    raise OSError("Font unavailable: %s; copy the bundled assets/fonts folder" % name)


def fit(draw, value, f, width):
    value = " ".join(str(value).split())
    while value and draw.textlength(value, font=f) > width:
        value = value[:-1]
        if draw.textlength(value + "…", font=f) <= width:
            return value + "…"
    return value


def wrapped(draw, text, f, width, count):
    words, lines, line = str(text).split(), [], ""
    for word in words:
        candidate = (line + " " + word).strip()
        if draw.textlength(candidate, font=f) <= width:
            line = candidate
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    result = [fit(draw, line, f, width) for line in lines[:count]]
    if len(lines) > count and result:
        result[-1] = fit(draw, result[-1] + " …", f, width)
    return result


def weather_description(code):
    labels = {0: "Clear sky", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast",
              45: "Fog", 48: "Freezing fog", 51: "Light drizzle", 53: "Drizzle",
              55: "Heavy drizzle", 56: "Freezing drizzle", 57: "Freezing drizzle",
              61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain",
              67: "Freezing rain", 71: "Light snow", 73: "Snow", 75: "Heavy snow",
              77: "Snow grains", 80: "Rain showers", 81: "Rain showers",
              82: "Heavy showers", 85: "Snow showers", 86: "Snow showers",
              95: "Thunderstorm", 96: "Thunderstorm / hail", 99: "Thunderstorm / hail"}
    return labels.get(code, "Weather")


def render_music(track, recent=False, art=None, unavailable=False, now=None, weather=None, weather_note=""):
    image = Image.new("L", (400, 300), 255)
    d = ImageDraw.Draw(image)
    now = now or datetime.now(ZoneInfo("America/New_York"))
    draw_date_header(d, now, weather, weather_note)
    d.rectangle((0, 262, 399, 299), fill=0)
    d.text((14, 270), "LAST PLAYED" if recent else "NOW PLAYING", font=font(19, True), fill=255)
    status = "cached · last.fm" if unavailable else "last.fm"
    d.text((385-d.textlength(status, font=font(14)), 273), status, font=font(14), fill=255)
    if art:
        cover = ImageOps.fit(art.convert("L"), (180, 180)).convert("1")
    else:
        cover = Image.new("L", (180, 180), 255)
        placeholder = ImageDraw.Draw(cover)
        placeholder.rectangle((0, 0, 179, 179), outline=0, width=2)
        placeholder.ellipse((30, 30, 149, 149), fill=0)
        placeholder.ellipse((75, 75, 104, 104), fill=255)
    image.paste(cover, (14, 58))
    text_x, text_width = 210, 176
    y = 57
    for line in wrapped(d, track.get("title", "Unknown track"), font(23, True), text_width, 3):
        d.text((text_x, y), line, font=font(23, True), fill=0)
        y += 29
    y += 9
    for line in wrapped(d, track.get("artist", ""), font(18), text_width, 2):
        d.text((text_x, y), line, font=font(18), fill=0)
        y += 23
    y += 6
    album_face = font(16, italic=True)
    for line in wrapped(d, track.get("album") or "", album_face, text_width, 2):
        d.text((text_x, y), line, font=album_face, fill=0)
        y += 20
    if recent:
        d.text((14, 245), "Weather + calendar after 10 minutes idle", font=font(11), fill=0)
    return image.convert("1", dither=Image.Dither.NONE)




class FrameStore:
    def __init__(self):
        self.lock = threading.Lock()
        self.bmp = self.png = b""
        self.etag = ""
        self.status = {}

    def publish(self, image, status):
        bmp, png = io.BytesIO(), io.BytesIO()
        image.save(bmp, "BMP")
        image.save(png, "PNG")
        data = bmp.getvalue()
        tag = '"' + hashlib.sha256(data).hexdigest() + '"'
        with self.lock:
            changed = self.bmp != data
            self.bmp, self.png, self.etag, self.status = data, png.getvalue(), tag, dict(status)
        if changed:
            LOG.info("Published %s screen: %d-byte monochrome BMP", status["mode"], len(data))


def handler_for(store):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            route = urlsplit(self.path).path
            with store.lock:
                if route == "/display.bmp":
                    data, mime, tag = store.bmp, "image/bmp", store.etag
                elif route == "/preview.png":
                    data, mime, tag = store.png, "image/png", ""
                elif route == "/status":
                    data, mime, tag = json.dumps(store.status).encode(), "application/json", ""
                elif route == "/credits":
                    data, mime, tag = (ROOT / "CREDITS.txt").read_bytes(), "text/plain; charset=utf-8", ""
                else:
                    self.send_error(404)
                    return
            if not data:
                self.send_error(503)
                return
            tags = [v.strip() for v in self.headers.get("If-None-Match", "").split(",")]
            unchanged = tag and any(v == "*" or v.removeprefix("W/") == tag for v in tags)
            self.send_response(304 if unchanged else 200)
            self.send_header("Content-Type", mime)
            self.send_header("Cache-Control", "no-cache")
            if tag:
                self.send_header("ETag", tag)
            if not unchanged:
                self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if not unchanged:
                self.wfile.write(data)

        def log_message(self, fmt, *args):
            LOG.debug(fmt, *args)
    return Handler


class Providers:
    def __init__(self, config):
        self.config = config
        self.zone = ZoneInfo(config["timezone"])
        self.weather = None
        self.events = []
        self.weather_note = "Weather not configured"
        self.calendar_note = "Authorize Google Calendar to show events"
        self.next_weather = self.next_calendar = 0
        self.credentials = None
        self.routing = DriveTimes(config.get("routing", {}), ROOT)
        self.route_results = queue.Queue()
        self.route_running = False
        self.next_routes = 0
        self.routing_note = "Drive times need a routing API key" if not self.routing.available() else ""
        self.corner = CornerRotation(config, ROOT, get_json)

    def lastfm(self):
        cfg = self.config["lastfm"]
        key = os.environ.get("LASTFM_API_KEY") or cfg.get("api_key")
        if not key:
            raise ValueError("Configure LASTFM_API_KEY or lastfm.api_key")
        track = parse_now_playing(get_json("https://ws.audioscrobbler.com/2.0/", {
            "method": "user.getRecentTracks", "user": cfg["username"],
            "api_key": key, "format": "json", "limit": 1}))
        return enrich_from_vinyl(track, ROOT / "state" / "vinyl_metadata.json")

    def fetch_weather(self):
        cfg = self.config["weather"]
        lat, lon = cfg.get("latitude"), cfg.get("longitude")
        if lat is None or lon is None:
            raise ValueError("Set weather.latitude and weather.longitude")
        value = get_json("https://api.open-meteo.com/v1/forecast", {
            "latitude": lat, "longitude": lon, "timezone": self.config["timezone"],
            "current": "temperature_2m,apparent_temperature,weather_code,is_day,uv_index",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum,sunrise,sunset",
            "forecast_days": 2, "precipitation_unit": "inch",
            "temperature_unit": cfg["temperature_unit"]})
        current, daily = value["current"], value["daily"]
        def forecast(index):
            return {"date": daily["time"][index], "code": daily["weather_code"][index],
                    "high": daily["temperature_2m_max"][index], "low": daily["temperature_2m_min"][index],
                    "precip_probability": daily["precipitation_probability_max"][index],
                    "precip_inches": daily["precipitation_sum"][index],
                    "sunrise": (daily.get("sunrise") or [None, None])[index],
                    "sunset": (daily.get("sunset") or [None, None])[index]}
        today, tomorrow = forecast(0), forecast(1)
        self.weather = {**today, "tomorrow": tomorrow, "temperature": current["temperature_2m"],
                        "precip_code": today["code"],
                        "feels": current["apparent_temperature"], "code": current["weather_code"],
                        "is_day": bool(current["is_day"]),
                        "uv_index": current.get("uv_index"),
                        "high": daily["temperature_2m_max"][0], "low": daily["temperature_2m_min"][0],
                        "unit": "F" if cfg["temperature_unit"] == "fahrenheit" else "C",
                        "location": cfg.get("label", "Local weather")}
        self.weather_note = ""

    def fetch_calendar(self):
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request as GoogleRequest
        cfg = self.config["google"]
        path = ROOT / cfg["token_file"]
        if self.credentials is None:
            self.credentials = Credentials.from_authorized_user_file(str(path), SCOPES)
        creds = self.credentials
        if not creds.valid:
            transport = GoogleRequest()
            def refresh_request(*args, **kwargs):
                kwargs["timeout"] = 12
                return transport(*args, **kwargs)
            creds.refresh(refresh_request)
            atomic_write(path, creds.to_json().encode(), private=True)
        now = datetime.now(timezone.utc)
        rows, failed = [], 0
        previous_routes = {e.get("route_key"): {k: e.get(k) for k in ("drive_minutes", "drive_status")}
                           for e in self.events}
        for calendar in cfg["calendars"]:
            try:
                params = {"timeMin": now.isoformat(), "timeMax": (now + timedelta(days=7)).isoformat(),
                          "singleEvents": "true", "orderBy": "startTime", "maxResults": 10,
                          "timeZone": self.config["timezone"]}
                items = []
                while True:
                    result = get_json("https://www.googleapis.com/calendar/v3/calendars/" +
                                      quote(calendar["id"], safe="") + "/events", params,
                                      {"Authorization": "Bearer " + creds.token})
                    items += [e for e in result.get("items", [])
                              if e.get("status") != "cancelled" and not excluded_event(e)]
                    if len(items) >= 3 or not result.get("nextPageToken"):
                        break
                    params["pageToken"] = result["nextPageToken"]
                for event in items[:3]:
                    start = event["start"]
                    if "dateTime" in start:
                        dt = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00")).astimezone(self.zone)
                    else:
                        dt = datetime.fromisoformat(start["date"]).replace(tzinfo=self.zone)
                    timing = {"start": dt.isoformat(), "all_day": "dateTime" not in start}
                    when = event_when(timing, now.astimezone(self.zone))
                    address = physical_address(event)
                    route_key = hashlib.sha256(json.dumps([calendar["id"], event.get("id", event.get("summary")),
                                                           dt.timestamp(), address]).encode()).hexdigest()
                    rows.append({"sort": dt.timestamp(), "when": when, **timing,
                                 "calendar": calendar.get("label", "Calendar"),
                                 "summary": event.get("summary", "(No title)"),
                                 "meeting": meeting_platforms(event), "physical_address": address,
                                 "route_key": route_key, **previous_routes.get(route_key, {})})
            except Exception as exc:
                # Exception strings can include private URLs or query parameters.
                LOG.warning("Calendar fetch failed (%s)", type(exc).__name__)
                failed += 1
        if failed == len(cfg["calendars"]) and cfg["calendars"]:
            raise RuntimeError("No calendars could be fetched")
        self.events = sorted(rows, key=lambda e: e["sort"])[:3]
        self.calendar_note = "Some calendars unavailable" if failed else ""

    def refresh_routes(self, now):
        try:
            completed = self.route_results.get_nowait()
        except queue.Empty:
            completed = None
        if completed is not None:
            self.route_running = False
            by_key = {e["route_key"]: e for e in completed}
            for event in self.events:
                if event.get("route_key") in by_key:
                    result = by_key[event["route_key"]]
                    event.update({k: result.get(k) for k in ("drive_minutes", "drive_status")})
            self.routing_note = "Some drive estimates unavailable" if any(
                e.get("drive_status") in ("unavailable", "cached") for e in self.events) else ""
        if not self.routing.available():
            self.routing_note = "Drive times need a routing API key" if self.routing.config.get("enabled", True) else "Drive times disabled"
            for event in self.events:
                if event.get("physical_address"):
                    event.update(drive_minutes=None, drive_status="key_missing")
            return
        if self.route_running or now < self.next_routes:
            return
        batch = [dict(e) for e in self.events if e.get("physical_address")]
        if not batch:
            return
        self.route_running = True
        self.next_routes = now + 300
        for event in self.events:
            if event.get("physical_address") and event.get("drive_minutes") is None:
                event["drive_status"] = "pending"
        def work():
            try:
                rows = self.routing.annotate(batch)
            except Exception as exc:
                LOG.warning("Routing update failed (%s)", type(exc).__name__)
                rows = [dict(e, drive_minutes=None, drive_status="unavailable") for e in batch]
            self.route_results.put(rows)
        threading.Thread(target=work, daemon=True).start()

    def refresh_weather(self, now):
        today = datetime.fromtimestamp(now, self.zone).date().isoformat()
        if self.weather and self.weather.get("date") != today and self.next_weather > now + 60:
            self.next_weather = 0
        if now < self.next_weather:
            return
        try:
            self.fetch_weather()
            self.next_weather = now + 900
        except Exception as exc:
            LOG.warning("weather unavailable (%s)", type(exc).__name__)
            self.weather_note = "Weather unavailable"
            self.next_weather = now + 60

    def refresh_idle(self, now):
        self.refresh_weather(now)
        if now >= self.next_calendar:
            try:
                self.fetch_calendar()
                self.next_calendar = now + 300
            except Exception as exc:
                LOG.warning("calendar unavailable (%s)", type(exc).__name__)
                self.calendar_note = "Calendar unavailable"
                self.next_calendar = now + 60
        self.refresh_routes(time.time())
        self.corner.refresh(now)


class DisplayWorker:
    def __init__(self, config, store):
        self.config, self.store = config, store
        self.providers = Providers(config)
        self.state_path = ROOT / config.get("state_file", "state/playback.json")
        try:
            self.playback = Playback(json.loads(self.state_path.read_text()))
        except (OSError, ValueError):
            self.playback = Playback()
        self.art_key, self.art, self.next_art = "", None, 0
        self.music_error = False

    def render(self, now):
        mode = self.playback.mode(now, self.config["idle_seconds"])
        # Keep both date/time headers on the same five-minute cadence.
        dt = datetime.fromtimestamp(now, self.providers.zone)
        dt = dt.replace(minute=(dt.minute // 5) * 5, second=0, microsecond=0)
        corner = None
        audio_status = recognition_status(ROOT / "state" / "vinyl_recognition.json", now=now)
        if mode != "idle":
            track = self.playback.data["track"]
            key = track.get("art_url", "")
            if key != self.art_key:
                self.art_key, self.art, self.next_art = key, None, 0
            if key and self.art is None and now >= self.next_art:
                try:
                    if not key.startswith("https://"):
                        raise ValueError("Album art must use HTTPS")
                    with urlopen(key, timeout=8) as response:
                        self.art = Image.open(io.BytesIO(response.read(3_000_000))).convert("L")
                except Exception:
                    self.next_art = now + 300
            image = render_music(track, mode == "recent", self.art, self.music_error, now=dt,
                                 weather=self.providers.weather, weather_note=self.providers.weather_note)
        else:
            corner = self.providers.corner.card(now, self.providers.weather)
            image = render_dashboard(self.providers.weather, self.providers.events, dt,
                                     self.providers.weather_note, self.providers.calendar_note,
                                     "Last.fm offline" if self.music_error else "", corner=corner,
                                     no_match=audio_status == "no_match")
        self.store.publish(image, {"mode": mode, "lastfm_available": not self.music_error,
                                  "recognition_status": audio_status,
                                  "lastfm_last_success": self.playback.data.get("last_success"),
                                  "weather_note": self.providers.weather_note,
                                  "calendar_note": self.providers.calendar_note,
                                  "routing_note": self.providers.routing_note,
                                  "corner_widget": corner["kind"] if corner else None,
                                  "drive_times": [{"calendar": e["calendar"],
                                                   "status": e.get("drive_status", "no_address"),
                                                   "minutes": e.get("drive_minutes")}
                                                  for e in self.providers.events]})

    def tick(self):
        try:
            track = self.providers.lastfm()
            self.playback.observe(track, time.time())
            self.music_error = False
            atomic_write(self.state_path, json.dumps(self.playback.data).encode(), private=True)
        except Exception as exc:
            LOG.warning("Last.fm unavailable (%s); preserving playback state", type(exc).__name__)
            self.music_error = True
        now = time.time()
        if self.playback.mode(now, self.config["idle_seconds"]) == "idle":
            self.providers.refresh_idle(now)
        else:
            self.providers.refresh_weather(now)
        self.render(time.time())

    def run(self):
        while True:
            start = time.monotonic()
            try:
                self.tick()
            except Exception as exc:
                LOG.error("Display update failed (%s); retaining current bitmap", type(exc).__name__)
            time.sleep(max(1, self.config["lastfm_poll_seconds"] - (time.monotonic() - start)))


def demo():
    output = ROOT / "demo"
    output.mkdir(exist_ok=True)
    track = {"title": "Beyond the Pines", "artist": "Thrice", "album": "Palms"}
    weather = {"temperature": 72, "feels": 71, "high": 75, "low": 56, "code": 2,
               "unit": "F", "is_day": True, "uv_index": 4.2, "precip_probability": 65, "precip_inches": 0.12,
               "tomorrow": {"code": 61, "high": 68, "low": 52, "precip_probability": 80,
                            "precip_inches": 0.24}}
    events = [{"start": "2026-10-05T15:00:00-04:00", "calendar": "Personal", "meeting": "Zoom", "summary": "Afternoon appointment"},
              {"start": "2026-10-06T07:00:00-04:00", "calendar": "Work", "summary": "Morning shift",
               "drive_minutes": 18, "drive_status": "ok"},
              {"start": "2026-10-07T11:00:00-04:00", "calendar": "Shared", "meeting": "Teams", "summary": "Planning meeting"}]
    snow_weather = {**weather, "temperature": 29, "feels": 23, "high": 32, "low": 19,
                    "code": 0, "precip_code": 73, "uv_index": 1.2,
                    "precip_probability": 85, "precip_inches": 0.18,
                    "tomorrow": {"code": 71, "high": 30, "low": 17,
                                 "precip_probability": 70, "precip_inches": 0.10}}
    for name, image in (("now_playing", render_music(track, now=datetime(2026, 10, 5, 14, 30, tzinfo=ZoneInfo("America/New_York")), weather=weather)),
                        ("idle", render_dashboard(weather, events,
                         datetime(2026, 10, 5, 14, 30, tzinfo=ZoneInfo("America/New_York")))),
                        ("idle_snow", render_dashboard(snow_weather, events,
                         datetime(2026, 10, 5, 14, 30, tzinfo=ZoneInfo("America/New_York"))))):
        image.save(output / (name + ".png"))
        image.save(output / (name + ".bmp"))
    example_now = datetime(2026, 11, 7, 14, 30, tzinfo=ZoneInfo("America/New_York"))
    example_events = [dict(event, start=event["start"].replace("2026-10-05", "2026-11-07")
                          .replace("2026-10-06", "2026-11-08").replace("2026-10-07", "2026-11-09")
                          .replace("-04:00", "-05:00"))
                      for event in events]
    examples = [
        ("rotation_uk", {"kind": "uk", "start": "2026-11-10T21:30:00-05:00", "opponent": "Kansas",
                         "venue": "United Center", "site": "Neutral", "city": "Chicago",
                         "tv": "ESPN",
                         "logo": str(ROOT / "assets/uk_logo.png")}),
        ("rotation_lastfm", {"kind": "stats", "tracks_today": 37, "artist": "Thrice", "artist_plays": 42}),
        ("rotation_sun", {"kind": "sun", "day": "Today", "sunrise": example_now.replace(hour=7, minute=10),
                          "sunset": example_now.replace(hour=17, minute=35), "next": "Sunset"}),
        ("rotation_pokemon", {"kind": "pokemon", "number": 25, "name": "Pikachu",
                              "sprite": str(ROOT / "assets/pokemon/025.png")})]
    for name, card in examples:
        image = render_dashboard(weather, example_events, example_now, corner=card)
        image.save(output / (name + ".png"))
        image.save(output / (name + ".bmp"))
    no_match = render_dashboard(weather, example_events, example_now, corner=examples[1][1], no_match=True)
    no_match.save(output / "idle_no_match.png")
    no_match.save(output / "idle_no_match.bmp")
    crowded = [dict(example_events[0], start="2026-11-08T23:59:00-05:00", calendar="Work",
                    summary="Quarterly team planning and project review", meeting="Zoom/Teams", drive_minutes=128),
               dict(example_events[1], start="2026-11-09T07:00:00-05:00"),
               dict(example_events[2], start="2026-11-10T11:00:00-05:00")]
    wrapped = render_dashboard(weather, crowded, example_now, corner=examples[1][1])
    wrapped.save(output / "rotation_calendar_wrap.png")
    wrapped.save(output / "rotation_calendar_wrap.bmp")
    print("Offline examples written to", output)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(ROOT / "config.json"))
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.demo:
        demo()
        return
    config = json.loads(Path(args.config).read_text())
    if config["idle_seconds"] < 0 or config["lastfm_poll_seconds"] < 5:
        parser.error("Use nonnegative idle_seconds and lastfm_poll_seconds >= 5")
    store = FrameStore()
    worker = DisplayWorker(config, store)
    worker.render(time.time())
    server = ThreadingHTTPServer((config["host"], config["port"]), handler_for(store))
    threading.Thread(target=worker.run, daemon=True).start()
    LOG.info("Serving 400x300 bitmap on port %d at /display.bmp", config["port"])
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
