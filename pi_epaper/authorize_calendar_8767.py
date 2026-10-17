#!/usr/bin/env python3
"""One-time interactive read-only Calendar authorization; never run in systemd."""
import argparse
import json
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import AuthorizedSession
from display_service import ROOT, SCOPES, atomic_write

parser = argparse.ArgumentParser()
parser.add_argument("--headless", action="store_true", help="Use SSH tunnel on localhost:8765")
parser.add_argument("--config", default=str(ROOT / "config.json"))
args = parser.parse_args()
cfg = json.loads(Path(args.config).read_text())
flow = InstalledAppFlow.from_client_secrets_file(str(ROOT / "credentials.json"), SCOPES)
creds = flow.run_local_server(host="localhost", port=8767, open_browser=not args.headless,
                              authorization_prompt_message="Open this URL in your browser:\n{url}",
                              access_type="offline", prompt="consent", timeout_seconds=300)
atomic_write(ROOT / cfg["google"]["token_file"], creds.to_json().encode(), private=True)
print("Saved read-only Calendar authorization. Available calendars:")
session = AuthorizedSession(creds)
params = {"showHidden": "true"}
while True:
    response = session.get("https://www.googleapis.com/calendar/v3/users/me/calendarList",
                           params=params, timeout=15)
    response.raise_for_status()
    data = response.json()
    for item in data.get("items", []):
        print(item.get("summary", "Calendar") + "  ->  " + item["id"])
    if not data.get("nextPageToken"):
        break
    params["pageToken"] = data["nextPageToken"]
