# Historical GitHub content review — 2026-10-17

Repository: `statticus19/VinylPi` (private during review).

Four commits exist: `77defed47d`, `4a4fdc24f5`, `2b99da93f1`, `a76b25a27a`.
The initial commit contains a single file. The last three commits share application-source blobs: both later commits change only the README.

## Completed via authenticated GitHub connector

- Retrieved recursive commit trees for all four commits; none were truncated.
- Inspected source and documentation text files from the original full-tree commit, including Python application/test files, Arduino firmware, ESP32 display drivers, dependency manifests, configuration examples, README, documentation, workflow and metadata file.
- Reviewed the two later README revisions separately.
- Scanned for recognizable literal credential assignments, embedded private-network IPv4 addresses, and private-key PEM headers; only test fixtures under `pi_epaper/test_display_service.py` and `pi_epaper/test_icecast_metadata.py` triggered the suspicious-literal heuristic. Inspection shows these are dummy/mocked token/password values in tests, rather than confirmed live secrets.
- Recursive trees had no obvious credential-bearing filenames beyond `firmware/CrowPanel_Remote_BMP/secrets.example.h`, an expected template.

## Limits and decision

This constitutes a **targeted readable-source screening**, not a complete high-confidence history/secret scan. Regex heuristics can miss obfuscated, unusual, or embedded credentials; binary assets and compressed files were not fully decoded or audited. Historical git-pack search by specialized scanners (e.g., Gitleaks) and manual review of any flagged records are still recommended **before changing repository visibility**.

The live Pi's older standalone orchestrator source, collected separately from GitHub, contained real-looking Last.fm credentials, including plaintext password. The public staging copy removed those values, but **Last.fm credential rotation is required if they remain usable**. This finding does not prove those values were ever in GitHub history.

Do not make the GitHub repository public until license/provenance of bundled images and the old history checks have been resolved. Do not publish the separately prepared private recovery data.
