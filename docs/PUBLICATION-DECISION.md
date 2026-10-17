# Release decision — 2026-10-17

**Decision: keep current GitHub repository private; offline-reviewed source available for review.** This is not permission to make a public release.

## Completed
- NAS-specific dashboard routes/UI/modules removed in candidate; private Pi left unchanged.
- The owner-confirmed CrowPanel BMP v3 firmware incorporated with credential values excluded.
- Audio, streaming, reverse-proxy example configurations and optional assets documentation provided.
- Added missing upstream MIT license for vendored audfprint code.
- Removed the original owner's Last.fm username as a fallback default from the public orchestrator.
- Historical GitHub source/readme files received targeted content screening across four commits; mocks in tests were the only triggered patterns. This does not substitute for full git history secret scanning.
- Offline regression suite: 13 passing tests, syntax compilation and static publication check; all scans are heuristic.
- Owner declined a live install test; this is documented and is not a requested task.

## Before changing GitHub visibility
1. Run an independent full-history secret scan (e.g. Gitleaks) on a complete clone; investigate any actual findings and rotate credential exposures. Existing legacy Pi orchestrator credentials are known to have been present outside GitHub.
2. Determine whether 12 snapshot dashboard art/icon files may be redistributed; replace/omit with neutral licensed equivalents if not.
3. Review vendored libraries and display drivers for attribution/license completeness; never distribute bundled Pokemon sprites/UK logo without adequate rights.
4. Review user-specific paths and sites in docs/systemd snapshot. The systemd snapshots are not installable templates.
5. Create a **private** GitHub review branch with the candidate files, then review branch changes against `main`; do not merge or make public until clearance.

## Separate recovery project

A private backup instruction kit was produced, but actual encrypted backup data and raw SD-card image cannot be made without owner-side access to the Pi/card. No backup has been performed or verified in this environment. The owner must create and securely store the actual archives.
