# Configuration examples — staging only

These are **illustrative templates derived from names/sections** in a redacted structure report, NOT exports of the live Pi config, and have not been installed or tested on the Pi.

- `darkice.cfg.example`: replace the ALSA capture device, passwords, host, format and quality to suit your system. **Do not** hardcode an ALSA card index without checking capture and playback mappings.
- `icecast.xml.example`: local-only listener example for the encoder; replace the passwords; actual packages may require additional paths and permissions. Match encoder source password, port and mount name to Icecast.
- `nginx-default.example`: optional loopback proxy example, **not** a drop-in replacement for the distribution's default Nginx site. Dashboard auth, proxy headers, exposed interfaces and HTTPS/Tailscale forwarding require separate security review.
- Legacy `pi-epaper-config.example.json` and `orchestrator.env.example` require reconciliation with current source.

Do not publish any real `.env`, OAuth files, state DBs, production `icecast.xml`, or `darkice.cfg`.
