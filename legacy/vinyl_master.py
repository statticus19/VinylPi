"""Legacy direct-audio/serial/Plex recognizer; not used by the bitmap setup.

Configure credentials locally as described in docs/LEGACY.md. Do not run alongside
orchestrator_display_ready.py: this script has no persistent duplicate protection.
"""
import os
if os.environ.get("ALLOW_LEGACY_VINYL_MASTER") != "1":
    raise SystemExit("Legacy script disabled. See docs/LEGACY.md before explicitly enabling it.")

import asyncio
import subprocess
import time
import serial
import pylast
from shazamio import Shazam
from plexapi.server import PlexServer

LASTFM_API_KEY = os.environ["LASTFM_API_KEY"]
LASTFM_API_SECRET = os.environ["LASTFM_API_SECRET"]
LASTFM_USERNAME = os.environ["LASTFM_USERNAME"]
LASTFM_PASSWORD_HASH = os.environ["LASTFM_PASSWORD_HASH"]

PLEX_URL = os.environ.get("PLEX_URL", "http://127.0.0.1:32400")
PLEX_TOKEN = os.environ["PLEX_TOKEN"]

lastfm = pylast.LastFMNetwork(
    api_key=LASTFM_API_KEY, 
    api_secret=LASTFM_API_SECRET, 
    username=LASTFM_USERNAME, 
    password_hash=LASTFM_PASSWORD_HASH
)
plex = PlexServer(PLEX_URL, PLEX_TOKEN)

try:
    ser = serial.Serial(os.environ.get("DISPLAY_SERIAL_PORT", "/dev/ttyACM0"), 115200, timeout=1)
except Exception as e:
    print(f"Warning: Could not connect to ELECROW display over serial: {e}")
    ser = None

async def record_snippet():
    subprocess.run(["arecord", "-D", os.environ["ALSA_DEVICE"], "-f", "cd", "-d", "10", "/tmp/sample.wav"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

async def main():
    current_song = None
    
    while True:
        await record_snippet()
        shazam = Shazam()
        out = await shazam.recognize("/tmp/sample.wav")
        
        if out and 'track' in out:
            title = out['track']['title']
            artist = out['track']['subtitle']
            album = out['track']['sections'][0].get('metadata', [{}])[0].get('text', 'Vinyl Release')
            
            song_id = f"{artist} - {title}"
            
            if song_id != current_song:
                current_song = song_id
                timestamp = int(time.time())
                
                print(f"[NOW PLAYING] {title} by {artist} (Album: {album})")
                
                try:
                    lastfm.scrobble(artist=artist, title=title, timestamp=timestamp, album=album)
                    print(" ✓ Scrobbled to Last.fm")
                except Exception as e:
                    print(f" ✗ Last.fm update error: {e}")

                if ser:
                    try:
                        payload = f"{artist}|{title}|{album}\n"
                        ser.write(payload.encode('utf-8'))
                        print(" ✓ Sent metadata payload to ELECROW display")
                    except Exception as e:
                        print(f" ✗ Serial display output error: {e}")

                try:
                    track = plex.fetchItem(int(os.environ["PLEX_TRACK_ID"]))
                    track.edit({
                        'title.value': title,
                        'artist.value': artist,
                        'album.value': album
                    })
                    print(" ✓ Updated Plex live track metadata")
                except Exception as e:
                    print(f" ✗ Plex API update error: {e}")
                
        await asyncio.sleep(90)

if __name__ == "__main__":
    asyncio.run(main())
