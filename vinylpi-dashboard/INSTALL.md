# VinylPi Dashboard — first read-only milestone

This release is intentionally **read-only**. It does not yet provide restart commands, streaming, learning controls, or YouTube review. It does not change Music Bridge or Tailscale Serve.

1. Using WinSCP, copy the `vinylpi-dashboard-starter` folder to `/home/vinyl/vinylpi-dashboard` (rename folder on Pi if necessary).
2. SSH: `cd ~/vinylpi-dashboard && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`.
3. Run `.venv/bin/python generate_credentials.py` and copy the TWO output lines privately. **Do not paste them into chat or GitHub.**
4. `sudo nano /etc/vinylpi-dashboard.env` and enter `VINYLPI_DASHBOARD_USER=vinyl`, the two generated lines, and `VINYLPI_DASHBOARD_COOKIE_SECURE=true`.
5. Run `sudo chown root:root /etc/vinylpi-dashboard.env && sudo chmod 600 /etc/vinylpi-dashboard.env`.
6. Run `sudo cp vinylpi-dashboard.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now vinylpi-dashboard.service`.
7. Check `curl -sS http://127.0.0.1:8780/health` and `sudo systemctl status vinylpi-dashboard.service --no-pager`.
8. For Tailnet HTTPS, **preserve the existing 8766 Music Bridge Serve route**. Configure a new distinct port using Tailscale Serve (check installed CLI syntax with `tailscale serve --help` before running). Do not open the dashboard's raw HTTP port to the LAN or Internet.

The login cookie is `Secure` by default, which requires HTTPS for browser sign-in. Do not disable this for an internet/Tailnet HTTP endpoint. Prefer Tailnet HTTPS. Subsequent phase: scope restart authorization to an explicit service allowlist with audit logs.
