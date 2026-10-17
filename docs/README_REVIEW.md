# VinylPi public edition — staging review

This copy is **not** a complete fresh-install release. It represents current live source for the recognizer, E-paper renderer, Music Bridge, dashboard, and streaming integrations, with NAS-specific public code removed. The live Raspberry Pi is unchanged.

See [`FRESH_INSTALL_GAPS.md`](FRESH_INSTALL_GAPS.md) for missing artifacts and blockers.

Run `python3 tools/prepublish_check.py` locally as a preliminary audit. It intentionally reports **locations only**, never suspected secret values; a passing result does **not** certify that the repository is secret-free.

Do not push or publish until the review blockers are resolved, fresh-install tests pass, and the Git history has been audited.
