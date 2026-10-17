#!/usr/bin/env python3
"""Enable routing without replacing an existing user's keys or calendar IDs."""
import getpass
import json
from display_service import ROOT, atomic_write
from routing import DEFAULT_ORIGIN

path = ROOT / "config.json"
config = json.loads(path.read_text())
routing = config.setdefault("routing", {})
defaults = {"enabled": True, "api_key": "", "origin_address": DEFAULT_ORIGIN,
            "origin_coordinates": None, "cache_seconds": 86400,
            "cache_file": "state/routes.json", "address_overrides": {}}
for name, value in defaults.items():
    routing.setdefault(name, value)
routing["enabled"] = True
key = getpass.getpass("OpenRouteService API key (hidden; Enter keeps the existing key): ").strip()
if key:
    routing["api_key"] = key
atomic_write(ROOT / "config.before-routing.json", path.read_bytes(), private=True)
atomic_write(path, (json.dumps(config, indent=2) + "\n").encode(), private=True)
print("Routing configured. Existing Last.fm, Google calendars, port, and timezone preserved.")
print("Origin:", routing["origin_address"])
print("Restart the display service to apply.")
