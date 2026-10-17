import asyncio
import logging
import math
import os
import struct
import subprocess
import sys
import time
import wave
from pathlib import Path
import pylast
from shazamio import Shazam
from scrobble_guard import ScrobbleGuard, single_instance
from music_metadata import AlbumResolver, publish_metadata
from icecast_metadata import IcecastMetadata
from learning_client import LearningClient

ROOT = Path(__file__).resolve().parent

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

# Credentials are supplied by the environment, never embedded in this copy.
API_KEY = os.environ.get("LASTFM_API_KEY", "")
API_SECRET = os.environ.get("LASTFM_API_SECRET", "")
USERNAME = os.environ.get("LASTFM_USERNAME", "")
PASSWORD_HASH = os.environ.get("LASTFM_PASSWORD_HASH", "")
if not PASSWORD_HASH and os.environ.get("LASTFM_PASSWORD"):
    PASSWORD_HASH = pylast.md5(os.environ["LASTFM_PASSWORD"])

ICECAST_MOUNT = "/vinyl"
ICECAST_URL = "http://127.0.0.1:8000"

TEMP_AUDIO = "/tmp/vinyl_sample.wav"
STREAM_URL = f"{ICECAST_URL}{ICECAST_MOUNT}"
SILENCE_THRESHOLD_DB = -35.0  # Skip Shazam if quieter than this threshold

def connect_lastfm():
  if not all((API_KEY, API_SECRET, USERNAME, PASSWORD_HASH)):
    logging.warning("Last.fm credentials incomplete. Scrobbling disabled.")
    return None
  try:
    client = pylast.LastFMNetwork(api_key=API_KEY, api_secret=API_SECRET,
                                username=USERNAME, password_hash=PASSWORD_HASH)
    logging.info(f"Connected to Last.fm as {USERNAME}")
    return client
  except Exception as e:
    logging.error("Last.fm authentication failed (%s)", type(e).__name__)
    return None


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
      "-acodec",
      "pcm_s16le",
      "-ar",
      "44100",
      "-ac",
      "2",
      "-f",
      "wav",
      TEMP_AUDIO,
      "-loglevel",
      "quiet",
      "-hide_banner",
  ]
  try:
    subprocess.run(cmd, check=True, timeout=40)
    return True
  except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
    logging.error(f"FFmpeg capture failed: {e}")
    return False


async def main():
  record_recognition("unknown")
  learner = LearningClient(ROOT / "state" / "learning", secrets=(API_KEY, API_SECRET, PASSWORD_HASH))
  shazam = Shazam()
  guard = ScrobbleGuard(ROOT / "state" / "orchestrator_scrobbles.json")
  network = connect_lastfm()
  albums = AlbumResolver(API_KEY)
  try:
    icecast = IcecastMetadata.from_environment(ICECAST_URL, ICECAST_MOUNT)
  except ValueError as exc:
    logging.warning("Icecast metadata configuration invalid: %s; metadata disabled.", exc)
    icecast = IcecastMetadata(ICECAST_URL, ICECAST_MOUNT)

  logging.info("Vinyl Orchestrator started. Entering monitoring loop...")

  while True:
    cycle = None
    try:
      cycle = learner.enter()
      if cycle is None:
        await asyncio.sleep(1)
        continue
      # Privacy pause may become active after enter() takes its shared lock.
      # The dashboard waits for this cycle to drain before acknowledging pause.
      if learner.privacy_paused():
        await monitoring_sleep(1, learner, cycle)
        continue
      # Step 1: Capture 12-second audio sample
      logging.info("Capturing audio sample from stream...")
      if not capture_sample():
        record_recognition("error")
        await monitoring_sleep(15, learner, cycle)
        continue

      # Step 2: Measure Volume (Silence Gate)
      volume_db = get_wav_rms_db(TEMP_AUDIO)
      logging.info(f"Sample audio level: {volume_db:.1f} dB")

      if volume_db < SILENCE_THRESHOLD_DB:
        record_recognition("silent")
        logging.info(
            "Stream is silent/idle (below threshold). Skipping Shazam"
            " lookup."
        )
        if guard.silence(time.time()):
          icecast.update(label="Vinyl - Idle")
          logging.info("Sustained silence confirmed; the next match may start a new listening session.")
        await monitoring_sleep(30, learner, cycle)
        continue

      if learner.privacy_paused():
        await monitoring_sleep(1, learner, cycle)
        continue
      # Step 3: Identify Track with Shazam
      guard.audio(time.time())
      logging.info("Volume acceptable. Querying Shazam...")
      out = await shazam.recognize(TEMP_AUDIO)
      if not isinstance(out, dict) or "error" in out or "errors" in out:
        raise ValueError("Malformed recognition response")
      track_info = out.get("track")
      if track_info is not None and not isinstance(track_info, dict):
        raise ValueError("Malformed recognized track")

      if not track_info:
        track_info = await asyncio.to_thread(learner.match, TEMP_AUDIO)

      if not track_info:
        record_recognition("no_match")
        icecast.update(label="Vinyl - No match")
        logging.info("No match found for this audio snippet.")
        await monitoring_sleep(45, learner, cycle)
        continue

      title = track_info.get("title")
      artist = track_info.get("subtitle")
      if not all(isinstance(value, str) and value.strip() for value in (artist, title)):
        record_recognition("unknown")
        logging.info("Incomplete recognition; skipping Last.fm submission.")
        await monitoring_sleep(45, learner, cycle)
        continue
      current_track_id = f"{artist} - {title}"

      if learner.privacy_paused():
        await monitoring_sleep(1, learner, cycle)
        continue
      logging.info(f"MATCH FOUND: {current_track_id}")
      record_recognition("matched")
      metadata = albums.resolve(track_info)
      icecast.update(artist=artist, title=title, album=metadata["album"])
      extra = {"album": metadata["album"]} if metadata["album"] else {}
      if metadata["album"]:
        logging.info("Album: %s (%s)", metadata["album"], metadata["album_source"])
      else:
        logging.info("Album metadata unavailable; submitting artist/title only.")
      try:
        publish_metadata(ROOT / "state" / "vinyl_metadata.json", metadata)
      except OSError:
        logging.warning("Cannot save artwork fallback; Last.fm submissions will continue.")

      # Renew now-playing on EVERY successful identification, including a
      # repeated match. The display reader relies on this status staying current.
      if network and not learner.privacy_paused():
        try:
          network.update_now_playing(artist=artist, title=title, **extra)
        except Exception as e:
          logging.error("Last.fm now-playing update failed (%s)", type(e).__name__)

      # Now-playing renewals never reset persistent scrobble protection.
      if learner.privacy_paused():
        logging.info("Privacy pause requested; suppressing Last.fm scrobble.")
        await monitoring_sleep(1, learner, cycle)
        continue
      if network:
        timestamp = guard.claim(artist, title, time.time())
        if timestamp is not None:
          try:
            logging.info(f"Scrobbling to Last.fm: {artist} - {title}")
            network.scrobble(
                artist=artist,
                title=title,
                timestamp=timestamp,
                **extra
            )
          except Exception as e:
            guard.result(False)
            logging.error("Scrobble result uncertain (%s); suppressing another submission for this session.", type(e).__name__)
          else:
            guard.result(True)
            logging.info("Scrobble accepted by Last.fm: %s", current_track_id)
        else:
          logging.info("%s. Skipping scrobble.", guard.reason)
      else:
        logging.warning("Last.fm client not connected; skipping scrobble.")

      # Sleep 60 seconds before next check during active playback
      await monitoring_sleep(60, learner, cycle)

    except Exception as e:
      record_recognition("error")
      logging.error(f"Unexpected error in orchestrator main loop: {e}")
      await monitoring_sleep(30, learner, cycle)
    finally:
      learner.leave(cycle)


async def monitoring_sleep(seconds, learner, cycle):
  learner.leave(cycle)
  if not learner.directory.exists():
    await asyncio.sleep(seconds)
    return
  for _ in range(seconds):
    if learner.paused():
      return
    await asyncio.sleep(1)


def record_recognition(status):
  try:
    publish_metadata(ROOT / "state" / "vinyl_recognition.json", {"status": status})
  except OSError:
    logging.warning("Cannot save audio recognition status; monitoring will continue.")


if __name__ == "__main__":
  try:
    with single_instance(ROOT / "state" / "orchestrator.lock"):
      try:
        asyncio.run(main())
      finally:
        record_recognition("stopped")
  except KeyboardInterrupt:
    logging.info("Vinyl Orchestrator stopped.")
  except (RuntimeError, ValueError, OSError) as e:
    logging.error("Orchestrator cannot start safely: %s", e)
    sys.exit(1)
