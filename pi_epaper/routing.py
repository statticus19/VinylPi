"""Cached OpenRouteService driving estimates. No live traffic; no guessed origins."""
import hashlib
import json
import logging
import math
import os
import re
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

LOG = logging.getLogger("routing")
DEFAULT_ORIGIN = "202 McCrae Ln, Covington, KY 41011"


def request_json(url, params=None, headers=None, body=None):
    if params:
        url += "?" + urlencode(params)
    headers = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    with urlopen(Request(url, data=data, headers=headers), timeout=12) as response:
        return json.loads(response.read(2_000_000))


def normalize_street(value):
    value = re.sub(r"[^a-z0-9 ]", "", value.lower())
    suffixes = {"ln": "lane", "st": "street", "rd": "road", "ave": "avenue", "dr": "drive",
                "blvd": "boulevard", "ct": "court", "pl": "place", "pkwy": "parkway",
                "cir": "circle", "ter": "terrace", "hwy": "highway", "n": "north", "s": "south",
                "e": "east", "w": "west"}
    return " ".join(suffixes.get(word, word) for word in value.split())


def coordinates(value):
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise ValueError("Coordinates must be [longitude, latitude]")
    lon, lat = map(float, value)
    if not math.isfinite(lon) or not math.isfinite(lat) or abs(lon)>180 or abs(lat)>90:
        raise ValueError("Coordinates out of range")
    return [lon, lat]


class DriveTimes:
    def __init__(self, config, root):
        self.config = config
        self.root = Path(root)
        self.path = self.root / config.get("cache_file", "state/routes.json")
        self.key = os.environ.get("ORS_API_KEY") or config.get("api_key", "")
        try:
            self.cache = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.cache = {}

    def available(self):
        return bool(self.config.get("enabled", True) and self.key)

    def cache_key(self, prefix, value):
        return prefix + hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    def save(self):
        # Include coordinates in a private local cache, never in HTTP status or logs.
        self.cache = {k: v for k, v in self.cache.items() if time.time()-v.get("at", 0)<30*86400}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.cache))
        tmp.chmod(0o600)
        os.replace(tmp, self.path)

    def geocode(self, address):
        override = self.config.get("address_overrides", {}).get(address)
        if isinstance(override, list):
            return coordinates(override)
        if isinstance(override, str):
            address = override
        key = self.cache_key("geo:", address)
        cached = self.cache.get(key)
        if cached and time.time()-cached["at"]<7*86400:
            return coordinates(cached["coordinates"])
        result = request_json("https://api.heigit.org/pelias/v1/search", {
            "text": address, "size": 3, "layers": "address", "boundary.country": "USA"},
            {"Authorization": self.key})
        number = re.match(r"\s*(\d+[A-Za-z]?)\b", address)
        if not number:
            raise ValueError("Address needs a house number or coordinate override")
        postal = re.search(r"\b\d{5}(?:-\d{4})?\b", address[number.end():])
        street_query = normalize_street(address[number.end():].split(",")[0])
        matches = []
        for feature in result.get("features", []):
            props = feature.get("properties", {})
            street = normalize_street(props.get("street", ""))
            if str(props.get("housenumber", "")).lower() != number.group(1).lower():
                continue
            if not street or not (street_query == street or street_query.startswith(street + " ")):
                continue
            if float(props.get("confidence", 0))<0.8:
                continue
            if postal and str(props.get("postalcode", ""))[:5]!=postal.group()[:5]:
                continue
            matches.append(coordinates(feature["geometry"]["coordinates"]))
        if not matches:
            raise ValueError("Address couldn't be confidently resolved")
        # Multiple matching points are acceptable only when they are effectively the same address.
        if any(abs(point[0]-matches[0][0])+abs(point[1]-matches[0][1])>0.001 for point in matches[1:]):
            raise ValueError("Ambiguous address; configure an explicit coordinate override")
        point = matches[0]
        self.cache[key] = {"at": time.time(), "coordinates": point}
        self.save()
        return point

    def estimate(self, address):
        if not self.available():
            return {"drive_minutes": None, "drive_status": "key_missing"}
        origin = self.config.get("origin_address", DEFAULT_ORIGIN)
        origin_override = self.config.get("origin_coordinates")
        key = self.cache_key("route:", [origin, origin_override, address,
                                       self.config.get("address_overrides", {}).get(address),
                                       self.config.get("address_overrides", {}).get(origin),
                                       hashlib.sha256(self.key.encode()).hexdigest()[:12]])
        cached = self.cache.get(key)
        if cached and cached.get("status") == "ok" and time.time()-cached["at"]<self.config.get("cache_seconds", 86400):
            return {"drive_minutes": cached.get("minutes"), "drive_status": cached.get("status", "ok")}
        if cached and cached.get("retry_after", 0)>time.time():
            return {"drive_minutes": cached.get("minutes"), "drive_status": cached.get("status", "unavailable")}
        try:
            start = coordinates(origin_override) if origin_override is not None else self.geocode(origin)
            end = self.geocode(address)
            value = request_json("https://api.heigit.org/openrouteservice/v2/directions/driving-car/json",
                headers={"Authorization": self.key}, body={"coordinates": [start, end],
                                                          "instructions": False, "geometry": False})
            seconds = float(value["routes"][0]["summary"]["duration"])
            if not math.isfinite(seconds) or seconds<0:
                raise ValueError("Invalid route duration")
            minutes = max(1, math.ceil(seconds/60))
            self.cache[key] = {"at": time.time(), "minutes": minutes, "status": "ok"}
            status = "ok"
        except Exception as exc:
            LOG.warning("Drive estimate unavailable (%s)", type(exc).__name__)
            minutes = cached.get("minutes") if cached else None
            status = "cached" if minutes is not None else "unavailable"
            # Retry failures after one hour, not after every calendar or Last.fm poll.
            self.cache[key] = {"at": time.time(), "minutes": minutes, "status": status,
                               "retry_after": time.time()+3600}
        self.save()
        return {"drive_minutes": minutes, "drive_status": status}

    def annotate(self, rows):
        return [dict(row, **self.estimate(row["physical_address"])) if row.get("physical_address")
                else dict(row) for row in rows]
