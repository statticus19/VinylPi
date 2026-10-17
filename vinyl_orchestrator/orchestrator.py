import asyncio
import logging
import math
import os
import struct
import subprocess
import sys
import time
import wave
import pylast
from shazamio import Shazam

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

# --- LAST.FM CREDENTIALS ---
API_KEY = os.environ.get("LASTFM_API_KEY", "")
API_SECRET = os.environ.get("LASTFM_API_SECRET", "")
USERNAME = os.environ.get("LASTFM_USERNAME", "")
PASSWORD_HASH = os.environ.get("LASTFM_PASSWORD_HASH", "")

ICECAST_MOUNT = "/vinyl"
ICECAST_URL = "http://127.0.0.1:8000"

TEMP_AUDIO = "/tmp/vinyl_sample.wav"
STREAM_URL = f"{ICECAST_URL}{ICECAST_MOUNT}"
SILENCE_THRESHOLD_DB = -35.0  # Skip Shazam if quieter than this threshold

# Initialize Last.fm Network
network = None
if API_KEY and API_SECRET and USERNAME and PASSWORD_HASH:
  try:
    network = pylast.LastFMNetwork(
        api_key=API_KEY,
        api_secret=API_SECRET,
        username=USERNAME,
        password_hash=PASSWORD_HASH,
    )
    logging.info(f"Connected to Last.fm as {USERNAME}")
  except Exception as e:
    logging.error(f"Last.fm Auth Error: {e}")
else:
  logging.warning("Last.fm credentials incomplete. Scrobbling disabled.")


def get_wav_rms_db(filepath):
  """Calculates the RMS volume level of a WAV file in dBFS."""
  try:
    if not os.path.exists(filepath):
      return -99.0
    with wave.open(filepath, "rb") as wf:
      num_frames = wf.getnframes()
      if num_frames == 0:
        return -99.0
      frames = wf.readframes(num_frames)
      samples = struct.unpack(f"<{num_frames * wf.getnchannels()}h", frames)
      if not samples:
        return -99.0
      sum_squares = sum(s * s for s in samples)
      mean_sq = sum_squares / len(samples)
      rms = math.sqrt(mean_sq)
      if rms <= 0:
        return -99.0
      return 20 * math.log10(rms / 32768.0)
  except Exception as e:
    logging.error(f"Error measuring sample volume: {e}")
    return -99.0


def capture_sample():
  """Captures 12s of audio from Icecast in standard 44.1kHz stereo WAV format."""
  cmd = [
      "ffmpeg",
      "-y",
      "-i",
      STREAM_URL,
      "-t",
      "12",
      "-f",
      "wav",
      TEMP_AUDIO,
      "-loglevel",
      "quiet",
      "-hide_banner",
  ]
  try:
    subprocess.run(cmd, check=True)
    return True
  except subprocess.CalledProcessError as e:
    logging.error(f"FFmpeg capture failed: {e}")
    return False


async def main():
  shazam = Shazam()
  last_scrobbled_track = None

  logging.info("Vinyl Orchestrator started. Entering monitoring loop...")

  while True:
    try:
      # Step 1: Capture 12-second audio sample
      logging.info("Capturing audio sample from stream...")
      if not capture_sample():
        await asyncio.sleep(15)
        continue

      # Step 2: Measure Volume (Silence Gate)
      volume_db = get_wav_rms_db(TEMP_AUDIO)
      logging.info(f"Sample audio level: {volume_db:.1f} dB")

      if volume_db < SILENCE_THRESHOLD_DB:
        logging.info(
            "Stream is silent/idle (below threshold). Skipping Shazam"
            " lookup."
        )
        last_scrobbled_track = None
        await asyncio.sleep(30)
        continue

      # Step 3: Identify Track with Shazam
      logging.info("Volume acceptable. Querying Shazam...")
      out = await shazam.recognize(TEMP_AUDIO)
      track_info = out.get("track", {})

      if not track_info:
        logging.info("No match found for this audio snippet.")
        await asyncio.sleep(45)
        continue

      title = track_info.get("title")
      artist = track_info.get("subtitle")
      current_track_id = f"{artist} - {title}"

      logging.info(f"MATCH FOUND: {current_track_id}")

      # Step 4: Scrobble to Last.fm
      if current_track_id != last_scrobbled_track:
        if network:
          try:
            logging.info(
                f"Updating 'Now Playing' on Last.fm: {artist} - {title}"
            )
            network.update_now_playing(artist=artist, title=title)

            logging.info(f"Scrobbling to Last.fm: {artist} - {title}")
            network.scrobble(
                artist=artist,
                title=title,
                timestamp=int(time.time())
            )
          except Exception as e:
            logging.error(f"Last.fm API Scrobble Error: {e}")
        else:
          logging.warning("Last.fm client not connected; skipping scrobble.")

        last_scrobbled_track = current_track_id
      else:
        logging.info("Track is still playing. Skipping duplicate scrobble.")

      # Sleep 60 seconds before next check during active playback
      await asyncio.sleep(60)

    except Exception as e:
      logging.error(f"Unexpected error in orchestrator main loop: {e}")
      await asyncio.sleep(30)


if __name__ == "__main__":
  asyncio.run(main())
