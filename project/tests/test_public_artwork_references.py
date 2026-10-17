"""Guard against accidentally redistributing retired theme assets."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'vinylpi-dashboard/dashboard/static'
REMOVED = {'art/blackbird.svg', 'art/vinylpi-mark.svg', 'favicon-16x16.png', 'icon-192.png', 'icon-512.png'}
REQUIRED = {'art/royal-city.svg', 'art/eternal-sea.svg', 'art/language-landscape.svg', 'favicon.ico', 'favicon-32x32.png', 'apple-touch-icon.png', 'social-preview.png'}

def test_retired_assets_not_bundled():
    assert not [name for name in REMOVED if (STATIC / name).exists()]

def test_active_assets_present():
    assert not [name for name in REQUIRED if not (STATIC / name).is_file()]
