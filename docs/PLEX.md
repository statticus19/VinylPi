# Optional Plex/Threadfin bridges

The supplied Pi had two active FFmpeg bridges reading Icecast. Their unit
templates preserve the supplied commands and replace local account names with
`YOUR_PI_USER`. Neither bridge is required for USB capture, Last.fm, VLC, or the
e-paper dashboard.

## HLS for a live TV tuner

`audio/vinyl-hls.service.example` combines a 640 × 360 black video stream at five
frames/second with the Icecast audio. It writes a short rolling HLS playlist and
segments to `/var/www/html/vinyl/index.m3u8`.

Edit the unit's `User=` to the Pi account running the bridge. Create its output
directory with that account as owner:

```bash
sudo install -d -m 755 -o YOUR_PI_USER /var/www/html/vinyl
sudo cp audio/vinyl-hls.service.example /etc/systemd/system/vinyl-hls.service
sudo systemctl daemon-reload
sudo systemctl enable --now vinyl-hls.service
```

A web server must serve that directory. The service does not itself host HTTP.
For example, if the web server's document root is `/var/www/html` on port 80, the
playlist URL is `http://YOUR_PI_LAN_IP/vinyl/index.m3u8`. Verify that URL in VLC,
then add it to the playlist/source used by Threadfin and configure the resulting
tuner/channel in Plex. The supplied files did not include the web-server setup,
Threadfin playlist/configuration, channel mapping, or Plex tuner settings.
Those settings remain external integration steps rather than a captured backup.

`audio/threadfin-playlist.m3u.example` illustrates a single HLS channel. Copy it
to a local playlist and replace the Pi address/port with the working HTTP URL.
It contains no credentials. Use your existing web server and Threadfin setup
when migrating a working installation.

## Named-pipe music-library feeder

`audio/plex-vinyl-pipe.service.example` copies the Icecast MP3 stream to a path
inside a music-library directory. Its description calls this a named-pipe feeder,
but a service definition cannot tell whether the destination is actually a FIFO
or a regular file. Before reproducing it, inspect the destination on the Pi:

```bash
stat -c '%F %n' "$HOME/Music/Vinyl/Vinyl Stream/Vinyl Stream/01 - Vinyl Stream.mp3"
```

Do not download or read the FIFO as if it were a source file. We need its type
and creation/setup instructions, not its live audio content.

If deliberately setting up this optional FIFO bridge on a fresh installation:

1. Create the parent music directory.
2. Create a FIFO at the exact output path with `mkfifo`, only if nothing already
   exists there. Do not overwrite a regular library track.
3. Give the feeder account write access and the Plex account read/traverse access.
4. Edit `User=` and the output path in the unit, then install and start it.
5. Configure the intended Plex music-library entry and verify playback.

The writer can block until a reader opens the pipe. A FIFO is not a normal
seekable MP3 file; Plex library scanning/playback behavior must be checked on
the actual server. The HLS path above is the supplied live-tuner bridge.

No Plex token or library ID is required by either FFmpeg service. The old
`legacy/vinyl_master.py` edits a separate library item through the Plex API and
is not needed to produce the HLS or Icecast streams.
