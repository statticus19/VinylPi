# Public dashboard artwork reference audit

The owner confirms the non-excluded dashboard artwork was created for VinylPi and is approved for inclusion. Assets originating from Pokémon, UK Athletics, and separately licensed font distributions remain excluded from the public repo (see `OPTIONAL-EPAPER-ASSETS.md`).

## Removed (no active runtime references in the staged dashboard)

- `art/blackbird.svg` — removed theme artwork.
- `art/vinylpi-mark.svg` — no template, CSS, or JS reference.
- `favicon-16x16.png` — no runtime reference; `favicon-32x32.png` and `favicon.ico` are used instead.
- `icon-192.png` and `icon-512.png` — no manifest or runtime reference in the staged application.

## Kept (referenced by runtime source)

- `art/royal-city.svg`, `art/eternal-sea.svg`, `art/language-landscape.svg` — backgrounds selected by CSS themes.
- `favicon.ico`, `favicon-32x32.png`, `apple-touch-icon.png` — page and guest-player icons.
- `social-preview.png` — Open Graph and Twitter metadata.

Reference audit searched all staged runtime source files. If future features reintroduce a removed asset, their code and documentation must be updated together.
