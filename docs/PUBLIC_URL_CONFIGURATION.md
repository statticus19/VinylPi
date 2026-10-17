# Public URL configuration

The dashboard, notifications, and Google OAuth no longer contain a fixed private Tailscale hostname in source.

- `VINYLPI_DASHBOARD_PUBLIC_URL`: externally reachable HTTPS base URL for Discord links. Optional; if unset, notifications omit deep links. Configure in the service environment of Music Bridge, health monitor, dashboard and boot notifier as relevant.
- `VINYLPI_GOOGLE_CALENDAR_REDIRECT_URI`: **required if using Google Calendar OAuth**; complete HTTPS callback URI registered with Google, typically `https://HOST/display/google/callback`. Without it, the OAuth flow fails with an actionable configuration error.
- `VINYLPI_STREAM_LAN_URL`: LAN streaming URL for copying, defaulting to loopback. Set to the user's own LAN address and ensure the stream is accessible from clients.

The stream guest URL is now an optional `guest_url` template variable. If the stream-page route does not supply this value, the field is blank rather than suggesting a made-up hostname. Configure the URL in the route/context before enabling guest-link sharing.

A public dashboard URL must be protected by access controls. Do not expose admin endpoints directly to the Internet simply to enable Discord links or OAuth.
