# iOS playlist prototype

Build this after the Pi's /health endpoint works over Tailscale. This is an action-by-action recipe, not an installable Shortcut. The user confirmed native video creation, Store ID extraction, and rehydration of the Pi's chosen Paramore video. Multi-match automatic collection still needs its two-video test.

## First: native Store-ID check

Duplicate the working video test. Between Search iTunes Store and Create Playlist:

1. Choose one music video from the results.
2. Get Details of iTunes Product → **Store ID** for the chosen video. Show Result.
3. Confirm you can read a numeric ID. Keep the original chosen native video as the input to Create Playlist; the ID itself cannot be added to a playlist.

For the development probe, Search iTunes Store for `Paramore Still Into You`, type Music Videos. The Pi matched Store ID `1021726093`. If your country/catalog has another version, inspect the results rather than substituting it silently.
If Store ID is absent on your iOS version, inspect Store URL; we can add URL-based validation after seeing the returned format. Do not feed a raw JSON dictionary or URL into Create Playlist and assume it becomes a native video.

## Source extraction: start with a 3-song playlist

1. Create **Music Videos Prototype** in Shortcuts.
2. Add **Get Playlist** (may appear as Get Songs in Playlist), select the source playlist already in your library. For the prototype, set it directly; Ask Each Time can be added later.
3. Add **Ask for Input**, Text: `Original playlist name`. Save as `Source Title`.
4. **Repeat with Each** song from Get Playlist, in returned order:
   - Get Details of Music → Name, from Repeat Item.
   - Get Details of Music → Artist, from that same Repeat Item.
   - Optional: Album.
   - Build a **Dictionary**: `title` = Name, `artists` = Artist as Text, optional `album` = Album. The Pi also accepts a list of artist strings.
   - End the repeat with this Dictionary, so Repeat Results becomes the track list.
5. Save Repeat Results as `Tracks`. Confirm the list contains one dictionary per song in playlist order. Do not manually construct JSON text; dictionary serialization handles quotes in song names safely.

This prototype does not yet translate a shared playlist URL into the native library playlist. That needs a separate compatibility check. The Pi can accept `source_url` later for identity tracking without fetching it.

## Submit and preserve the job

6. Tailscale Get Status. If disconnected, Connect; wait briefly and check again. Stop if still disconnected.
7. Current Date → Format Date (custom `yyyyMMddHHmmss`). Random Number, 100000–999999. Text: `Formatted Date-Random Number`. Save as `Run Key`.
8. Get Contents of URL: `https://YOUR-PI.YOUR-TAILNET.ts.net:8766/v1/playlist/jobs`.
   - Method POST.
   - Header Authorization: `Bearer YOUR_LOCAL_TOKEN`.
   - Header Idempotency-Key: `Run Key`.
   - Request body JSON:
     - mode (Text): `apple_video_plan`
     - title (Text): Source Title
     - country (Text): your uppercase storefront country, such as `US`
     - tracks (Array): Tracks
   If the action UI cannot insert the whole array into a JSON field, build a complete Dictionary with these fields first and use that dictionary as the JSON request body. Verify the outgoing structure against README; do not send tracks as a JSON-encoded string.
9. Get Dictionary Value `job_id`. If absent, show the returned error and stop.
10. Save a Dictionary containing `job_id` and `run_key` using Save File in a chosen iCloud Drive/On My iPhone folder. This allows resume after an iOS timeout. Avoid saving the access token in that file.

## Poll or resume

11. Repeat up to 30 times:
   - Wait 3 seconds.
   - Get Contents of URL, GET `/v1/playlist/jobs/JOB_ID`, Authorization header.
   - Inspect `status`.
   - On `completed`, obtain `result` and exit polling.
   - On `failed` or `interrupted`, show `error` and stop.
12. If still queued/running after that limit, show `Still processing; resume with the saved job ID` and stop. A separate Resume Shortcut can read the saved file and repeat step 11; it must not submit a new job.

Apple searches are paced below 20/minute, so large lists take several minutes. Do not keep Shortcuts waiting indefinitely. Once results are ready, save the plan locally before native creation as well.

## Rehydrate native video items and create once

13. Get `playlist_name` and `matches` from result. If matches is empty, show `No confident music-video matches` and stop without creating an empty playlist.
14. Repeat with Each dictionary in `matches`, in the returned order:
   - Get its `query` and `store_id`.
   - Wait at least 3 seconds between native searches.
   - **Search iTunes Store** for query, with type **Music Videos**, using the same settings as your successful test.
   - Repeat with Each native search result. Get Details of iTunes Product → Store ID.
   - Compare ID as text to planned `store_id`.
   - Keep exactly the first matching native video, and append that original video object to `Native Videos`. Append the planned ID to a parallel `Created IDs` list.
   - If no ID matches, skip that source entry. Do not choose the first search result instead.
   Use Add to Variable to collect the native objects and ID strings; keep separate variables. A per-iteration `Found` flag prevents adding duplicate search results. Preserve repeated source songs as repeated output videos.
15. If Native Videos is empty, stop with a message.
16. **Create Playlist**, name = returned playlist_name, input = Native Videos.
17. Only after creation succeeds, POST `/v1/playlist/jobs/JOB_ID/apple-created`, authorization, JSON:
   - account_alias: a fixed label such as `my-apple-music`
   - store_ids: Created IDs array
18. Show Result: playlist name plus actual video count. No Apple playlist share URL is attempted.

If acknowledgement fails after the playlist was created, retry ONLY step 17. Do not rerun Create Playlist. The Pi's job idempotency cannot make a native iPhone playlist-creation action idempotent. Before rerunning an interrupted native creation stage, check your Music library for the returned unique name.

The plan uses music-video resource type and exact normalized artist/title matching, with alternate/audio labels excluded. A matching standard video is preferred; explicitly labeled Lyric Video or Lyrics entries are fallback choices when none is found. When several entries in the preferred category pass those checks, the lowest numeric Store ID is selected consistently, per user preference. Catalog metadata does not prove that video content is identical or official provenance beyond catalog attribution. Verify the first small playlist plays as intended.

## Spotify variant

Reuse source extraction, connection, submission, and polling, but use `mode: spotify_handoff`. Maximum 200 tracks; larger lists are explicitly rejected.
On completion, Open URLs using result.handoff_url. Review and complete transfer on Soundiiz. This page is for you to complete the transfer, not a permanent Spotify playlist share URL. Apple-created acknowledgement is not used.
Soundiiz supports choosing the destination/account and reviewing matches. The backend receives no transfer completion callback or finished Spotify playlist ID through this free handoff.
