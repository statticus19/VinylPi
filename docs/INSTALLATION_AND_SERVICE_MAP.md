# Fresh installation and service map — review draft

This guide maps **actual collected unit snapshots** to source directories. It is not a one-command installer and should not be executed on the working Pi.

## Recommended installation sequence

1. Start with Debian on a **spare SD card / spare Pi**. Install Python 3 venv, ALSA utilities, ffmpeg, DarkIce and Icecast from your distribution repositories. Check package versions and enable SSH securely.
2. Connect Behringer UFO202. Run `arecord -l`, `aplay -l`, `arecord -L` and `aplay -L`. The original device currently advertises `hw:CARD=CODEC,DEV=0`, `plughw:CARD=CODEC,DEV=0`, and `dsnoop:CARD=CODEC,DEV=0`. Do not hardcode the numeric card index.
3. Create separate Python virtual environments for `pi_epaper`, `music-bridge`, `vinylpi-dashboard`, and a separate `.venv-learning` for the learning service. Install each directory's own requirements files; validate Python/package compatibility before enabling services.
4. Create credentials from **examples** on the new machine. Never use credentials copied from a public archive. OAuth consent, callback URL, Last.fm, Discord, Google/YouTube and external API enrollment require manual setup. Verify security, authentication and access restrictions.
5. Configure and manually test Icecast and DarkIce **before** starting transcription, recognition or passthrough services. Make sure the Icecast source password/mount and DarkIce output match. Use `alsaloop` with stable ALSA names after verifying speakers.
6. Start `pi-epaper` and test the 400×300 BMP endpoint. Configure and upload the **current** CrowPanel sketch locally, with a private `secrets.h`. Do not upload a public default Wi-Fi password.
7. Test Music Bridge and dashboard independently on loopback before enabling a reverse proxy or Tailnet listener. Configure state directories and access rights. Run the smoke tests.
8. Add optional integrations (learning, guest, HLS, Plex, Threadfin, control broker, notification bot) **one at a time**, only after verifying both the dependency and its permissions. The restricted control broker runs as root and requires special security review; do not enable it by default.
9. Only after all local tests pass, configure remote access and systemd auto-start. Never expose private admin endpoints to the public Internet.

## Service-unit inventory (collected snapshots)

The files under `systemd-snapshot/` mirror the original system and include machine-specific paths; they are **not safe drop-in service files**. They are examples for mapping/install work. Some override files replace `ExecStart`, so reading the base file alone can be misleading. Source file availability does not establish integration readiness.

| Unit | Working directory | Executable (source snapshot) | Credential file references |
|---|---|---|---|
| alsaloop.service | — | /usr/bin/alsaloop -C dsnoop:CARD=CODEC,DEV=0 -P hw:CARD=CODEC,DEV=0 -t 50000 -r 48000 | — |
| darkice.service | — | /usr/bin/darkice -c /etc/darkice.cfg | — |
| music-bridge.service | /home/vinyl/music-bridge | /home/vinyl/music-bridge/.venv/bin/uvicorn music_bridge.app:app --host 127.0.0.1 --port 8765 --no-access-log | /home/vinyl/music-bridge/.env |
| pi-epaper.service | /home/vinyl/pi_epaper | /home/vinyl/pi_epaper/.venv/bin/python /home/vinyl/pi_epaper/display_service.py | — |
| plex-vinyl-pipe.service | — | /usr/bin/ffmpeg -re -i http://127.0.0.1:8000/vinyl -c copy -f mp3 -y "/home/vinyl/Music/Vinyl/Vinyl Stream/Vinyl Stream/01 - Vinyl Stream.mp3" | — |
| threadfin.service | — | /opt/threadfin/threadfin | — |
| vinyl-hls.service | — | /usr/bin/ffmpeg -hide_banner -loglevel error -f lavfi -i color=c=black:s=640x360:r=5 -i http://127.0.0.1:8000/vinyl -map 0:v -map 1:a -c:v libx264 -preset ultrafast -tune zerolatency -c:a copy -f hls -hls_time 2 -hls_list_size 5 -hls_flags delete_segments /var/www/html/vinyl/index.m3u8 | — |
| vinyl-learning.service | /home/vinyl/pi_epaper | /home/vinyl/pi_epaper/.venv-learning/bin/python /home/vinyl/pi_epaper/learning_service.py | /home/vinyl/pi_epaper/learning.env |
| vinyl-orchestrator.service | /home/vinyl/vinyl_orchestrator | /usr/bin/python3 /home/vinyl/vinyl_orchestrator/orchestrator.py | — |
| vinyl-passthrough.service | — | /usr/bin/alsaloop -C hw:1,0 -P hw:1,0 -t 30000 | — |
| vinyl-stream.service | — | /usr/bin/darkice -c /etc/darkice.cfg | — |
| vinylpi-boot-notify.service | /home/vinyl/music-bridge | /home/vinyl/music-bridge/.venv/bin/python /home/vinyl/music-bridge/scripts/vinylpi_boot_notify.py | /etc/music-bridge-discord-bot.env |
| vinylpi-control.service | — | /usr/bin/python3 /usr/local/libexec/vinylpi-control-server | — |
| vinylpi-dashboard.service | /home/vinyl/vinylpi-dashboard | /home/vinyl/vinylpi-dashboard/.venv/bin/uvicorn dashboard.main:app --host 127.0.0.1 --port 8780 | /etc/vinylpi-dashboard.env |
| vinylpi-guest-player.service | /home/vinyl/vinylpi-guest | /usr/bin/python3 /home/vinyl/vinylpi-guest/guest_player.py | — |
| vinylpi-health-monitor.service | /home/vinyl/music-bridge | /home/vinyl/music-bridge/.venv/bin/python /home/vinyl/music-bridge/scripts/vinylpi_health_monitor.py | -/etc/music-bridge-discord-bot.env |

## Optional end-user validation on their own installation

From the extracted repository root, an end user may run `python3 tools/install_preflight.py`. It is read-only, lists requirements and external tools, returns nonzero on missing items, and never loads configuration values. Run Python compilation and `pytest` in isolated virtual environments. Next, check all service `EnvironmentFile`, `WorkingDirectory`, `ExecStart`, socket access controls, mounts, and overrides **against that test installation**. Validate audio signal, Last.fm writes (with a throwaway test), BMP updates, auth/guest separation, and recovery after reboot.
