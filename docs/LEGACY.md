# The older vinyl_master.py

`legacy/vinyl_master.py` is a sanitized reference copy of an earlier script. It:

- Records ten seconds directly from an ALSA capture device and asks Shazam for a match.
- Submits a Last.fm scrobble when its in-memory track identity changes.
- Sends `artist|title|album` text to a USB serial port.
- Edits one Plex library item selected by its `ratingKey`.

It does not serve images, publish audio to Icecast, update Icecast metadata,
or call Last.fm's now-playing method. Editing a Plex library item is different
from updating live tuner stream metadata. The current CrowPanel firmware downloads
BMPs over Wi-Fi and does not consume this serial text protocol.

The modern pipeline uses `pi_epaper/orchestrator_display_ready.py`, the existing
Icecast audio source, and `pi_epaper/display_service.py`. The legacy script is not
needed by that pipeline. Running both recognizers can compete for capture resources
and create duplicate scrobbles; the modern script's lock cannot stop this old script.

## Check the Pi

The source file alone does not prove a process is still running. On the Pi:

```bash
ps -eo pid,ppid,args | grep '[v]inyl_master.py'
```

If it prints a running process, identify how it starts. For a systemd-managed PID:

```bash
systemctl status ACTUAL_PID --no-pager
```

Once the modern recognizer is confirmed working, stop and disable the specific
legacy service found by that command:

```bash
sudo systemctl disable --now ACTUAL_LEGACY_SERVICE_NAME
```

For a manual process, stop it in its terminal or send `kill ACTUAL_PID`. Also check
`crontab -l` and any configured startup entry if systemd does not identify it.
Keep your active Icecast encoder running: this legacy script is not that encoder.

## Reference source

Credentials, account defaults, the ALSA device, and Plex item selection have been
moved to environment variables. The copy exits before connecting to any service
unless `ALLOW_LEGACY_VINYL_MASTER=1` is explicitly set. There is no installed service
for it. `legacy.env.example` and `requirements.txt` document its historical inputs.
Last.fm keys are obtained at https://www.last.fm/api/account/create; use
`tools/password_hash.py` for the password hash. For Plex tokens, see
https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/.

If deliberately testing the old protocol, stop the modern recognizer first,
install the legacy requirements from the legacy directory, fill a private
`legacy.env`, and source it before running the script. Do not enable this as part
of the dashboard installation. It lacks the current persistent duplicate guard,
silence gate, album resolver, and BMP rendering pipeline.


## Unused speaker passthrough

`vinyl-passthrough.service.example` and `vinyl-passthrough.env.example` are
configurable reference templates for an unused ALSA software loop. They are not
needed by the tested Behringer RCA speaker setup: the separate working
`alsaloop.service` remained running when the old unit was stopped. The working
service is included under `audio/` and documented in [AUDIO.md](AUDIO.md).
These legacy references are not installed by the main setup instructions.
