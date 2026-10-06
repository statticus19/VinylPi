#!/usr/bin/env python3
"""Enable routing without replacing an existing user's keys or calendar IDs."""
import getpass
import json
from display_service import ROOT, atomic_write

path = ROOT / "config.json"
config = json.loads(path.read_text())
routing = config.setdefault("routing", {})
defaults = {"enabled": False, "api_key": "", "origin_address": "",
            "origin_coordinates": None, "cache_seconds": 86400,
            "cache_file": "state/routes.json", "address_overrides": {}}
for name, value in defaults.items():
    routing.setdefault(name, value)
# Obtain a routing/geocoding key through https://openrouteservice.org/.
key = getpass.getpass("OpenRouteService API key (hidden; Enter keeps the existing key): ").strip()
if key:
    routing["api_key"] = key
address = input("Starting street address (Enter keeps existing address/coordinates): ").strip()
if address:
    routing["origin_address"] = address
    routing["origin_coordinates"] = None
if not routing["origin_address"].strip() and routing["origin_coordinates"] is None:
    raise SystemExit("Set a starting address or origin_coordinates before enabling routing.")
routing["enabled"] = True
atomic_write(ROOT / "config.before-routing.json", path.read_bytes(), private=True)
atomic_write(path, (json.dumps(config, indent=2) + "\n").encode(), private=True)
print("Routing configured. Existing Last.fm, Google calendars, port, and timezone preserved.")
print("Restart the display service to apply.")
