# DarkIce, Icecast, and stream metadata

The running audio path is USB interface → ALSA shared capture → DarkIce →
Icecast's `/vinyl` mount. The recognizer reads that mount with FFmpeg; VLC and
the optional Plex bridges read the same stream. A separate `alsaloop.service`
reads the shared USB capture device and feeds the Behringer playback device,
whose RCA outputs connect to the local speakers. Both services were running
together in the supplied setup and enabled at boot.

The provided installation used DarkIce 1.5-1 and Icecast2 2.4.4-4+b3. Capture was
stereo, 16-bit, 48 kHz through `dsnoop`, encoded as 320 kbit/s constant-bitrate MP3.
The templates preserve these settings while removing credentials and local account defaults.

## 1. Install and identify the capture device

```bash
sudo apt install darkice icecast2 alsa-utils ffmpeg
arecord -l
arecord -L
```

Read the USB interface's card ID and device number. In the provided listing its
card ID was `CODEC`, device 0; the numeric index was 3. For that interface the
named shared device is typically `dsnoop:CARD=CODEC,DEV=0`; verify it in `arecord -L`.
Choose your own device rather than copying a numeric card index: enumeration
can change after reboot or when USB devices are added.

ALSA's `dsnoop` plugin shares capture among readers. Raw `hw:` capture can conflict
with another reader. Shared readers must use compatible settings and have access
to the same ALSA IPC resources; the supplied DarkIce unit runs as root, as did the
working unit. Any additional shared-capture reader must have compatible access.
If changing users, configure audio permissions and shared-capture access accordingly.
Reference: [ALSA PCM plugins](https://www.alsa-project.org/alsa-doc/alsa-lib/pcm_plugins.html).

## 2. Configure Icecast and DarkIce

`audio/icecast.xml.example` is a sanitized copy of the supplied working XML.
Its paths match the Debian/Raspberry Pi OS package layout. On another distribution,
preserve that package's installed paths and apply the relevant settings.

Fill a private copy of the XML with your hostname, contact email, and distinct
source/relay/admin passwords. `<admin>` is a contact email; `<admin-user>` is the
metadata login. XML special characters in passwords must be escaped.
Source/password authentication is described in the [Icecast configuration reference](https://icecast.org/docs/icecast-2.4.1/config-file.html).

Fill a private copy of `audio/darkice.cfg.example` with your shared capture device
and the same source password used by Icecast. DarkIce's `mountPoint = vinyl`
corresponds to the HTTP listening path `/vinyl`. Keep the host `127.0.0.1` when
DarkIce and Icecast run on the same Pi. Refer to the [DarkIce configuration manual](https://manpages.debian.org/trixie/darkice/darkice.cfg.5.en.html).

On a fresh installation, place the completed files at:

```text
/etc/icecast2/icecast.xml
/etc/darkice.cfg
```

For an existing working installation, keep its private configuration and compare
the templates instead of overwriting it. Restrict configuration access to the
service accounts. DarkIce's template unit runs as root, so its configuration can
be root-owned mode 600. Icecast must be able to read its XML as its own service
account; a typical Debian configuration is root-owned with group `icecast` and
mode 640. Confirm the account/group on your installation before changing permissions.

## 3. Start the core stream

Use the installed Icecast service. The supplied Icecast unit was generated from
the distribution's `/etc/init.d/icecast2`; there is no need to publish or install
the generated `/run/systemd/generator.late/icecast2.service` file.
If your distribution requires an enable setting in `/etc/default/icecast2`, use
its packaged instructions and retain that local setting.

From the repository root:

```bash
sudo cp audio/darkice.service.example /etc/systemd/system/darkice.service
sudo systemctl daemon-reload
sudo systemctl enable --now icecast2.service darkice.service
journalctl -u darkice.service -u icecast2.service -n 40 --no-pager
```

After changing either configuration, restart its service. Play
`http://YOUR_PI_LAN_IP:8000/vinyl` in VLC before testing recognition.
The original `vinyl-stream.service` launches the same DarkIce command and was
inactive. Enable only one of those DarkIce services; two encoders targeting the
same device/mount are not part of this installation. If migrating that setup,
disable its redundant unit while keeping `darkice.service`:

```bash
sudo systemctl disable --now vinyl-stream.service
```

The main README covers `vinyl-orchestrator.service` and `pi-epaper.service`.
The supplied orchestrator override confirmed that its effective script is
`orchestrator_display_ready.py`; the older service path is not the active command.

## 4. Local speakers through the Behringer

The working `alsaloop.service` uses shared capture (`dsnoop`) and hardware playback
(`hw`) on the Behringer. It runs at 48 kHz with a 50 ms target latency, restarts
after five seconds if it exits, and starts at boot. The template preserves the
supplied service and replaces numeric card indices with a configurable card ID.
See the [alsaloop manual](https://manpages.debian.org/trixie/alsa-utils/alsaloop.1.en.html)
for the capture, playback, rate, and latency options.

Identify both sides before editing `audio/alsaloop.service.example`:

```bash
arecord -l
arecord -L
aplay -l
aplay -L
```

Replace both `YOUR_USB_CARD_ID` placeholders and, if needed, the device numbers.
Use exactly the same shared capture device as DarkIce. Capture and playback
can use different cards on other setups; select the actual speaker output.
The supplied service has no `User` setting and runs as root, matching DarkIce's
shared-capture access. Do not start a second loop targeting the same hardware
playback device.

For a fresh installation with local speakers, from the repository root:

```bash
sudo cp audio/alsaloop.service.example /etc/systemd/system/alsaloop.service
sudo systemctl daemon-reload
sudo systemctl enable --now alsaloop.service
journalctl -u alsaloop.service -n 30 --no-pager
```

An existing working `alsaloop.service` can be retained unchanged. Check both
speaker sound and the Icecast stream after setup. This loop is unnecessary for
an installation that only needs the network stream and display.

The separate old `vinyl-passthrough.service` was failing while this working loop
continued running. Stopping that old unit did not affect speaker sound. On that
installation, keep the old unit disabled and keep `alsaloop.service` enabled:

```bash
sudo systemctl disable --now vinyl-passthrough.service
```

Generic templates for the unused unit remain under `legacy/` as references;
they are not installed by these instructions.

## 5. Last.fm and Icecast song metadata

Fill `pi_epaper/orchestrator.env` as described in the root README:

- `ICECAST_URL` is the server root, normally `http://127.0.0.1:8000`.
- `ICECAST_MOUNT` is `/vinyl`, including the leading slash.
- `ICECAST_METADATA_URL` is the server root, not the full mount or `/admin/metadata`.
- Match `ICECAST_METADATA_USERNAME`/`PASSWORD` to Icecast's admin credentials.
  A blank password disables updates; `ICECAST_METADATA_INCLUDE_ALBUM=1` appends
  album names. The default title is `Artist - Title`.

An audible match updates Icecast independently of Last.fm. A genuine no-match
response sets `Vinyl - No match`; sustained silence sets `Vinyl - Idle`. Failed
recognition or metadata requests retain the existing title. Artwork is used by
the dashboard and is not sent to Icecast.

The persistent scrobble guard suppresses repeated matches and survives restarts.
It permits a new session after sustained silence and confirms a different track
across two matches. It does not measure a track's complete listening duration.
An uncertain Last.fm submission is not retried for that session. Preserve its
state file and run only one recognizer.

## Optional Plex paths

The provided named-pipe feeder and HLS transcoder are preserved as sanitized
unit templates under `audio/`. Both contain their FFmpeg commands directly;
no separate Python/shell script is required. See [PLEX.md](PLEX.md) for the directory,
FIFO, HTTP hosting, and Threadfin/Plex setup still needed around those commands.
