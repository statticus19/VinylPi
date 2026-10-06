"""Cached, asynchronous data for the idle screen's rotating corner panel."""
import json
import base64
import logging
import os
import queue
import random
import re
import threading
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

LOG = logging.getLogger("display.corner")
UK_SCHEDULE = "https://ukathletics.com/sports/mbball/schedule/"
UK_REFRESH_SECONDS = 7*86400
UK_MAX_AGE_SECONDS = 9*86400


class ScheduleScripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.parts = []
        self.documents = []

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("type") == "application/ld+json":
            self.active, self.parts = True, []

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.active:
            try:
                self.documents.append(json.loads("".join(self.parts)))
            except ValueError:
                pass
            self.active = False


def opponent_key(name):
    return re.sub(r"[^a-z0-9]", "", re.sub(r"\(exh\)", "", name.lower()))


def network_from_logo(source):
    """Read a recognized network wordmark, including UK imgproxy source URLs."""
    if "/imgproxy/" in source:
        encoded = urlsplit(source).path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        try:
            source = base64.urlsafe_b64decode(encoded+"="*((-len(encoded)) % 4)).decode("utf-8")
        except (ValueError, UnicodeError):
            return ""
    filename = urlsplit(source).path.rsplit("/", 1)[-1].lower()
    # An event/sponsor logo is not a station listing.
    if "classic" in filename or "espn_events" in filename:
        return ""
    patterns = [(r"espn[_-]?(?:plus|\+)", "ESPN+"), (r"espn[_-]?2", "ESPN2"),
                (r"espn[_-]?u", "ESPNU"), (r"espn[_-]?news", "ESPNews"),
                (r"sec[_-]?(?:network|n)[_-]?(?:plus|\+)", "SECN+"),
                (r"sec[_-]?network|secn", "SEC Network"),
                (r"(?<![a-z])espn(?![a-z0-9])", "ESPN"),
                (r"cbs[_-]?sports[_-]?network|cbssn", "CBS Sports"),
                (r"(?<![a-z])cbs(?![a-z])", "CBS"), (r"(?<![a-z])abc(?![a-z])", "ABC"),
                (r"fox[_-]?sports[_-]?1|(?<![a-z])fs1(?![a-z])", "FS1"),
                (r"(?<![a-z])fox(?![a-z])", "FOX"), (r"(?<![a-z])nbc(?![a-z])", "NBC")]
    for pattern, label in patterns:
        if re.search(pattern, filename):
            return label
    return ""


class ScheduleNetworks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.in_title = self.in_time = False
        self.title, self.date, self.networks = [], [], []
        self.by_game = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "div":
            if not self.depth and "schedule-item" in attrs.get("class", "").split():
                self.title, self.date, self.networks = [], [], []
                self.depth = 1
            elif self.depth:
                self.depth += 1
        if not self.depth:
            return
        if tag == "h3":
            self.in_title = True
        elif tag == "time":
            self.in_time = True
        elif tag == "img":
            source = attrs.get("data-src") or attrs.get("src", "")
            network = network_from_logo(source)
            if network and network not in self.networks:
                self.networks.append(network)

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)
        if self.in_time:
            self.date.append(data)

    def handle_endtag(self, tag):
        if tag == "h3":
            self.in_title = False
        elif tag == "time":
            self.in_time = False
        elif tag == "div" and self.depth:
            self.depth -= 1
            if self.depth == 0:
                date = re.search(r"([A-Za-z]{3})\.?\s+(\d{1,2})", " ".join(self.date))
                if date and self.networks:
                    try:
                        parsed = datetime.strptime("2000 "+" ".join(date.groups()), "%Y %b %d")
                        key = (opponent_key(" ".join(self.title)), parsed.month, parsed.day)
                        self.by_game[key] = " / ".join(self.networks)
                    except ValueError:
                        pass


def parse_uk_schedule(html):
    parser = ScheduleScripts()
    parser.feed(html)
    networks = ScheduleNetworks()
    networks.feed(html)
    games = []
    def walk(value):
        if isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, dict):
            if value.get("@type") in ("Event", "SportsEvent"):
                name = " ".join(str(value.get("name", "")).split())
                status = str(value.get("eventStatus", "")).lower()
                if any(x in status for x in ("cancel", "postpon")):
                    return
                # Festivities and internal scrimmages are not opponent games.
                if re.search(r"big blue madness|blue[- ]white|pro day", name, re.I):
                    return
                match = re.match(r"^(vs\.?|at)\s+(.+)", name, re.I)
                if not match or not value.get("startDate"):
                    return
                raw = value["startDate"]
                try:
                    date = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                except ValueError:
                    return
                if date.tzinfo is None:
                    date = date.replace(tzinfo=ZoneInfo("America/New_York"))
                location = value.get("location") or {}
                site = " ".join(str(location.get("name", "Site TBD")).split())
                venue, _, city = site.partition("|")
                venue = venue.strip().replace("Rupp Arena at Central Bank Center", "Rupp Arena")
                designation = "Away" if match[1].lower() == "at" else (
                    "Home" if "rupp arena" in venue.lower() else "Neutral")
                games.append({"start": date.isoformat(), "time_tbd": "T" not in raw,
                              "opponent": match[2].strip(), "venue": venue or "Site TBD",
                              "city": city.strip(), "site": designation,
                              "tv": networks.by_game.get((opponent_key(match[2]), date.month, date.day), "")})
            elif "@graph" in value:
                walk(value["@graph"])
    for document in parser.documents:
        walk(document)
    if not games:
        raise ValueError("No recognizable opponent schedule in source")
    return sorted(games, key=lambda g: g["start"])


def next_uk_game(games, now):
    candidates = []
    for game in games:
        try:
            start = datetime.fromisoformat(game["start"]).astimezone(now.tzinfo)
        except (ValueError, KeyError):
            continue
        if game.get("time_tbd"):
            eligible = now.date() <= start.date() <= (now+timedelta(days=7)).date()
        else:
            eligible = now <= start < now+timedelta(days=7)
        if eligible:
            candidates.append((start, game))
    return dict(min(candidates, key=lambda pair: pair[0])[1]) if candidates else None


def sun_card(weather, now):
    if not weather:
        return None
    days = [weather, weather.get("tomorrow") or {}]
    for day in days:
        times = {}
        for label in ("sunrise", "sunset"):
            try:
                value = datetime.fromisoformat(day[label])
                if value.tzinfo is None:
                    value = value.replace(tzinfo=now.tzinfo)
                times[label] = value.astimezone(now.tzinfo)
            except (KeyError, ValueError, TypeError):
                pass
        future = [(value, label) for label, value in times.items() if value > now]
        if future:
            next_time, next_label = min(future)
            return {"kind": "sun", "day": "Today" if next_time.date() == now.date() else "Tomorrow",
                    "sunrise": times.get("sunrise"), "sunset": times.get("sunset"),
                    "next": next_label.title()}
    return None


class CornerRotation:
    def __init__(self, config, root, get_json):
        self.config = config
        self.options = config.get("corner", {})
        self.root, self.get_json = Path(root), get_json
        self.zone = ZoneInfo(config["timezone"])
        self.path = self.root / "state/corner.json"
        try:
            self.cache = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.cache = {}
        self.results = queue.Queue()
        self.running = False
        self.next_fetch = {kind: self.cache.get(kind, {}).get("fetched", 0)+interval
                           if self.cache.get(kind) else 0
                           for kind, interval in (("uk", UK_REFRESH_SECONDS), ("stats", 900))}
        self.stats_day = self.cache.get("stats", {}).get("day")
        if self.cache.get("uk") and not self.cache["uk"].get("tv_checked"):
            self.next_fetch["uk"] = 0
        self.note = ""
        self.token, self.pokemon_id = None, None
        try:
            self.names = json.loads((self.root / "assets/pokemon_names.json").read_text())
        except (OSError, ValueError):
            self.names = {}
        self.pokemon_ids = [int(n) for n in self.names if (self.root / "assets/pokemon" / ("%03d.png" % int(n))).is_file()]

    def enabled(self, kind=None):
        return self.options.get("enabled", True) and (kind is None or self.options.get(kind, True))

    def fetch_uk(self, now):
        with urlopen(Request(UK_SCHEDULE, headers={"User-Agent": "PiEpaperDashboard/1.0"}), timeout=12) as response:
            html = response.read(4_000_000).decode("utf-8")
        return {"games": parse_uk_schedule(html), "fetched": now, "tv_checked": True}

    def fetch_stats(self, now):
        cfg = self.config["lastfm"]
        key = os.environ.get("LASTFM_API_KEY") or cfg.get("api_key")
        if not key:
            raise ValueError("Missing Last.fm key")
        local = datetime.fromtimestamp(now, self.zone)
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
        params = {"user": cfg["username"], "api_key": key, "format": "json", "limit": 1}
        recent = self.get_json("https://ws.audioscrobbler.com/2.0/", {
            **params, "method": "user.getRecentTracks", "from": int(midnight.timestamp()), "to": int(now)})
        top = self.get_json("https://ws.audioscrobbler.com/2.0/", {
            **params, "method": "user.getTopArtists", "period": "7day"})
        if "error" in recent or "error" in top:
            raise ValueError("Last.fm stats unavailable")
        total = int(recent["recenttracks"]["@attr"]["total"])
        artists = top["topartists"].get("artist", [])
        if isinstance(artists, dict):
            artists = [artists]
        artist = artists[0] if artists else {}
        return {"day": local.date().isoformat(), "tracks_today": total,
                "artist": artist.get("name", "No plays yet"),
                "artist_plays": int(artist.get("playcount", 0)), "fetched": now}

    def refresh(self, now):
        if not self.enabled():
            return
        changed = False
        while True:
            try:
                kind, value = self.results.get_nowait()
            except queue.Empty:
                break
            if kind == "done":
                self.running = False
            elif value is not None:
                self.cache[kind] = value
                changed = True
            else:
                self.next_fetch[kind] = now + (3600 if kind == "uk" else 300)
        if changed:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.path.with_suffix(".tmp")
                temporary.write_text(json.dumps(self.cache))
                temporary.chmod(0o600)
                os.replace(temporary, self.path)
            except OSError:
                LOG.warning("Corner cache could not be saved")
        if self.running:
            return
        tasks = []
        local_day = datetime.fromtimestamp(now, self.zone).date().isoformat()
        if local_day != self.stats_day:
            self.next_fetch["stats"] = 0
            self.stats_day = local_day
        for kind, interval in (("uk", UK_REFRESH_SECONDS), ("stats", 900)):
            if not self.enabled(kind):
                continue
            if now >= self.next_fetch[kind]:
                tasks.append(kind)
                self.next_fetch[kind] = now + interval
        if not tasks:
            return
        self.running = True
        def work():
            for kind in tasks:
                try:
                    value = getattr(self, "fetch_"+kind)(now)
                except Exception as exc:
                    LOG.warning("Corner %s unavailable (%s)", kind, type(exc).__name__)
                    value = None
                self.results.put((kind, value))
            self.results.put(("done", None))
        threading.Thread(target=work, daemon=True).start()

    def card(self, now, weather):
        if not self.enabled():
            return None
        local = datetime.fromtimestamp(now, self.zone)
        cards = []
        schedule = self.cache.get("uk", {})
        if self.enabled("uk") and now - schedule.get("fetched", 0) < UK_MAX_AGE_SECONDS:
            game = next_uk_game(schedule.get("games", []), local)
            if game:
                cards.append({"kind": "uk", **game, "logo": str(self.root / "assets/uk_logo.png"),
                              "cached": now-schedule["fetched"] >= UK_REFRESH_SECONDS})
        stats = self.cache.get("stats", {})
        if self.enabled("stats") and stats.get("day") == local.date().isoformat() and now-stats.get("fetched", 0) < 86400:
            cards.append({"kind": "stats", **stats, "cached": now-stats["fetched"] > 1800})
        solar = sun_card(weather, local) if self.enabled("sun") else None
        if solar:
            cards.append(solar)
        if self.enabled("pokemon") and self.pokemon_ids:
            cards.append({"kind": "pokemon"})
        if not cards:
            return None
        seconds = max(60, int(self.options.get("seconds", 900)))
        slot = int(now // seconds)
        selected = dict(cards[slot % len(cards)])
        token = (slot, selected["kind"])
        if selected["kind"] == "pokemon":
            if token != self.token:
                choices = [n for n in self.pokemon_ids if n != self.pokemon_id] or self.pokemon_ids
                self.pokemon_id = random.SystemRandom().choice(choices)
            n = self.pokemon_id
            selected.update(number=n, name=self.names[str(n)].replace("-", " ").title(),
                            sprite=str(self.root / "assets/pokemon" / ("%03d.png" % n)))
        self.token = token
        return selected
