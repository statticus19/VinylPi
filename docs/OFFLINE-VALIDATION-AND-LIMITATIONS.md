# Offline release policy (user decision, 2026-10-17)

No live installation, spare-Pi, hardware flash, or full-system deployment test will be performed as part of this release process. Do not request it again as a prerequisite. The public project may be published with accurate limitations once security, content, licensing and repository-history reviews are complete; it must not be advertised as installation-tested or production-verified.

## What offline validation establishes
- Python syntax and file-level structure can be checked without a Pi.
- Isolated dashboard route tests can verify authentication redirection, basic asset serving and absent NAS endpoints.
- Source-level security boundary tests can check service allowlist consistency, CSRF usage and local UNIX-socket restrictions.
- Example configuration syntax can be parsed. This does not verify system behavior.

## What is not established
- End-to-end audio capture, recognition, scrobbling, encoding, HLS, Plex/Threadfin integration.
- OAuth callback and account permissions against production providers.
- CrowPanel flashing, display rendering under load, and on-device heartbeat timing.
- Long-running service startup, update, or power-loss recovery.
- End-user hardware portability and unattended installation success.

## Pre-publication gates still required (offline)
1. Remove or replace all host-specific settings in service snapshots; do not publish raw snapshots as installable units.
2. Verify complete secret/public identifier scanning, and separately audit old Git history for prior secrets before public visibility.
3. Review dashboard static artwork and third-party asset licensing.
4. Verify package inventory, installation docs, config examples and service-to-source mapping.
5. Run regression suite and record output. A successful offline test suite is **not** a live test.

## Private disaster recovery
The user's separate encrypted backup must capture their actual NAS-enabled installation. This is not part of the public release and does not depend on performing a live installation test.
