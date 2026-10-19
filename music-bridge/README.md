# Music Link Bridge — milestone 0.2

Private Raspberry Pi API, with iOS playlist creation and a Spotify Soundiiz handoff.
The API is independent of the triggering device so Android/Windows/browser clients can be added later.

## What is implemented

- Song/album link resolver from 0.1 (legacy Songlink; anonymous request returned 401, so this remains blocked pending provider access).
- Apple video matching from a supplied track list, using the public iTunes search endpoint. Strict artist/title metadata checks; prefer a standard music video, then fall back to a video explicitly labeled Lyric Video or Lyrics when no standard match is found. Audio results and labeled alternate performances are skipped. If several entries in the preferred category pass the same exact artist/title checks, choose the lowest numeric Store ID consistently, regardless of search order. Matching metadata does not prove the video content is identical or establish official provenance beyond catalog attribution.
- Shortcuts may supply `artists` as text or a list of strings; text is normalized to a one-item list.
- Persistent background jobs, ordered results, repeated-song preservation, progress polling, and idempotent submission.
- Spotify-only Soundiiz import handoff, 1–200 tracks. The user completes the actual transfer on Soundiiz; the service never claims that creating an import page created a Spotify playlist.
- Apple creation acknowledgement. After native Shortcuts creates the playlist, its name and actual video IDs can be recorded. Apple share URL extraction is deliberately omitted.
- Compact SQLite history with 90-day/1,000-record retention, at most 20 pending jobs, and a 32 MiB main database ceiling. DELETE journal mode avoids unbounded WAL growth; main DB plus journal fits within the intended 75 MiB budget. Permanent mapping rows are preserved. When essential state fills the database, pause writes rather than prune mappings.
- Private HTTPS access through Tailscale Serve, plus local bearer authentication.

## 1. Pi readiness check

Copy the archive to the Pi and extract it in your home folder. For the existing `vinyl` account:

```bash
cd /home/vinyl
unzip music-bridge-starter.zip
cd music-bridge
python3 scripts/check_environment.py
```

This read-only script reports Python, the Pi's own Tailscale hostname, existing Serve configuration, and an Apple video search probe. It does not display tokens or peer lists.
Requires Python 3.10 or newer. If a project folder already exists, extract into a temporary directory and copy code files across; retain `.env` and the database.

## 2. Install

```bash
cd /home/vinyl/music-bridge
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
python3 - <<'PY'
import os, secrets
if not os.path.exists('.env'):
    with open('.env', 'x') as f:
        f.write('MUSIC_BRIDGE_TOKEN=' + secrets.token_urlsafe(32) + '\n')
    os.chmod('.env', 0o600)
PY
sudo install -m 644 systemd/music-bridge.service /etc/systemd/system/music-bridge.service
sudo systemctl daemon-reload
sudo systemctl enable --now music-bridge
curl http://127.0.0.1:8765/health
```

If upgrading an already running service, run `sudo systemctl restart music-bridge` after installing the new files.
The service assumes `User=vinyl` and `/home/vinyl/music-bridge`; adjust these if your user/path differs. SQLite lives in `/var/lib/music-bridge` via systemd StateDirectory. Running manually uses `~/.local/share/music-bridge/jobs.db` unless MUSIC_BRIDGE_DB is set.

Read `.env` locally to enter the token in your Shortcut. Do not send it in chat. The service's restart behavior handles process exits; an interrupted job is reported rather than silently repeating an uncertain remote request.

## 3. Tailscale-only exposure

Check `tailscale serve status` first. If HTTPS port 8766 is unused:

```bash
sudo tailscale serve --bg --https=8766 http://127.0.0.1:8765
```

Use the exact HTTPS URL Tailscale prints, including `:8766`. It may ask you to enable tailnet HTTPS. Keep existing Serve configuration; do not reset it. This creates a private tailnet endpoint, with no router forwarding or Funnel. Your iPhone must be allowed to reach this Pi/port under tailnet policy.

## API contracts

All `/v1/` routes require `Authorization: Bearer YOUR_LOCAL_TOKEN`.

### Regular links

`POST /v1/share` body `{"url":"APPLE_MUSIC_URL"}` returns `share_text` and normalized links. Artist/playlist input here is rejected explicitly; playlist workflows use the job API. The legacy resolver may require authorized access (`SONGLINK_API_KEY` optionally supported) or replacement. Do not buy a subscription or key before checking actual provider availability.

### Start playlist job

`POST /v1/playlist/jobs`, with `Idempotency-Key` header (16–128 letters, numbers, underscores, or hyphens). Generate it once per intended run; reuse it on network retries. A new intentional transfer gets a new key.

```json
{
  "mode": "apple_video_plan",
  "title": "Road Trip",
  "country": "US",
  "tracks": [
    {"title":"Still Into You","artists":["Paramore"],"album":"Paramore"}
  ]
}
```

Optional `source_url` must be a full Apple Music playlist URL. It is stored as identity metadata, not fetched. Optional track `isrc` must be a valid uppercase ISRC. Use `mode: spotify_handoff` for Soundiiz; requests over 200 tracks are rejected without truncation. Apple plans support up to 1,000 tracks.

### Poll or resume

`GET /v1/playlist/jobs/JOB_ID` returns `queued`, `running`, `completed`, `failed`, or `interrupted`, plus `processed`, `total`, `result`, and `error`. Persist the job ID on the client so it can resume polling if iOS suspends the Shortcut. Do not resubmit or create another native playlist merely because the phone timed out.

Apple result: `playlist_name`, `matches` in source order (`source_index`, `store_id`, `query`, `title`, `artist`), `skipped`, counts, and `requires_ios_creation:true`. Names include `<Original> + Music Videos`, a UTC timestamp and job suffix for uniqueness. The backend cannot inspect existing iPhone playlist names.

Spotify result: `handoff_url`, `expires_at`, `accepted_tracks`, `playlist_created:false`. Open the Soundiiz page to finish. The link lasts about 24 hours. This handoff supplies no finished destination ID or transfer callback.

### Record native Apple completion

After Create Playlist succeeds, `POST /v1/playlist/jobs/JOB_ID/apple-created`:

```json
{"account_alias":"my-apple-music","store_ids":["1021726093"]}
```

IDs are strings and must be an ordered subset of the planned IDs. Preserve repeats. Report only IDs actually added by iOS. A stable account alias distinguishes your accounts; it is a local label, not an Apple credential. The same acknowledgement can be retried safely. This records a client-reported completion, not independent verification by Apple. No Apple share URL is required or stored. Future Apple updates still require a reliable native lookup by identity/name.

### Diagnostics

`GET /health` reports token configuration and worker readiness without secrets. `GET /v1/capabilities` reports implemented modes. View startup failures with:

```bash
systemctl status music-bridge --no-pager
journalctl -u music-bridge -n 40 --no-pager
```

Avoid sharing `.env`. Access logging is disabled in the service. SQLite stores compact job inputs/results and IDs, not artwork or full provider responses. SQLite max-page enforcement pauses admission when full; service logs are managed separately by systemd journald.

## iOS regular-link Shortcut

Receive one URL from the Share Sheet. Tailscale Get Status → Connect if disconnected → wait/check connection. POST to `/v1/share` with JSON `url` and authorization. If `share_text` exists, Share it directly; otherwise show the error. Plain text goes to the standard Share Sheet, but the receiving app controls URL display and preview styling.

## Validation

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## Sources

- https://performance-partners.apple.com/search-api
- https://support.apple.com/guide/shortcuts/navigate-the-action-list-apdc33e4f4da/ios
- https://docs.scpl.dev/actions/getplaylist
- https://docs.scpl.dev/actions/getdetailsofitunesproduct
- https://tailscale.com/docs/features/mac-ios-shortcuts
- https://tailscale.com/docs/reference/tailscale-cli/serve
- https://support.soundiiz.com/hc/en-us/articles/36613501259922--API-Let-your-users-import-any-tracklist-to-Soundiiz
- https://tidal-music.github.io/tidal-api-reference/tidal-api-oas.json
- https://developers.google.com/youtube/v3/docs/playlists/insert
