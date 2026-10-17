# Fresh-install readiness and missing artifacts

**Status: Not ready to publish or install.** This staging copy is a code-review candidate assembled from file collections, not a reproducible release.

## Missing from user-provided snapshots

- Value-free configuration structure received; generic Nginx, Icecast2 and DarkIce examples added. Exact live setup and ALSA capture mapping still unverified.
- User-confirmed current CrowPanel BMP v3 firmware and drivers are now included; compile/flash on test hardware remains outstanding.
- Binary dashboard assets: icons, social preview, artwork placeholders, image resources and font/media licensing. The previous text-only capture filtered these out.
- Local `.env.example` and `config.example.json` templates for each app. Do not use real tokens, OAuth credentials, passwords, personal addresses, or NAS data.
- Exact virtualenv dependency manifests and OS package versions, including the `pi_epaper/.venv-learning` environment.
- Current data/permission scheme, systemd runtime directories, scheduled jobs, Tailscale serving/forwarding and external integrations.
- A tested installation process on a fresh 64-bit Debian 13/Raspberry Pi OS deployment.

## Problems to resolve in staging

- Service units under `systemd-snapshot` are historical **private system snapshots**, not installation templates; remove identifying assumptions, normalize permissions and choose explicit dependencies.
- Several source files still include `YOUR-VINYLPI-HOST` or `YOUR-PI-LAN-IP` placeholders. Replace their implementations with documented environment variables rather than publishing placeholder URLs as defaults.
- Some service and path assumptions reference `/home/vinyl/`. Make user and install location configurable without breaking permission constraints.
- Review modules for role-based access, media sharing, LAN-only exposure and SSRF/open-redirect behaviors. Do not infer security from a successful syntax check.
- Run unit tests, integration tests, and hardware-in-loop checks before publishing.
- Review all current and **past repository commits** for leaked credentials; rotate any keys/passwords exposed anywhere.

## Public versus private

Public repo must not include live systemd environment files, OAuth material, tokens, databases, logs, SSH or Tailscale state, Synology credentials, static personal addresses or NAS modules.

Private disaster-recovery deliverable should include a full offline storage image, an encrypted app/state archive, verified checksums and a restore guide for the CrowPanel, cloud OAuth/Discord/Last.fm, Shortcuts, router/DHCP, Plex/Threadfin and Synology DSM. No private backup is to be committed or uploaded to GitHub.
