"""Offline regression checks for conservative review labels."""
from music_bridge.youtube_matching import assess

TRACK = {"title": "All of Me", "artists": ["Billie Holiday"]}

def video(video_id="1lLWURwvoDQ", title="All of me | Billie Holiday. (Lyrics).", channel="Clockcarousel"):
    return {"id": video_id, "snippet": {"title": title, "channelTitle": channel,
            "channelId": "UCFakeExample", "description": "", "liveBroadcastContent": "none"},
            "status": {"privacyStatus": "public", "uploadStatus": "processed"},
            "contentDetails": {"duration": "PT3M2S"}}

def test_unofficial_lyric_video_stays_unverified_and_visible():
    item = assess(TRACK, video(), "US")
    assert item["kind"] == "lyric_video"
    assert item["metadata_eligible"] is True  # Legacy heuristic retained.
    assert item["official_status"] == "unverified"
    assert item["uploader_verified"] is False
    assert item["review_priority"] == "possible_video"

def test_channel_name_is_not_verification():
    item = assess(TRACK, video(channel="BillieHolidayVEVO"), "US")
    assert item["official_status"] == "unverified"
    assert item["publisher_evidence"] == "channel_name_match_unverified"
    assert item["uploader_verified"] is False

def test_audio_is_preserved_but_deprioritized():
    item = assess(TRACK, video(title="Billie Holiday - All of Me (Official Audio)"), "US")
    assert item["rejection_reason"] == "audio_or_visualizer"
    assert item["review_priority"] == "other_results"
    assert item["official_status"] == "unverified"
