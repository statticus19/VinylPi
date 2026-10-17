#!/usr/bin/env python3
"""Validate and save a MusicLink API key with private permissions."""
import asyncio
import getpass
import json
import os
from pathlib import Path
import sys
import tempfile
import httpx
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from music_bridge.catalog import Catalogs, CatalogError, apple_source

async def check(path):
    os.environ['MUSIC_BRIDGE_MUSICLINK_CLIENT'] = str(path)
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        source = await apple_source(client, 'https://music.apple.com/us/album/still-into-you/593146366?i=593148441')
        candidate, reason = await Catalogs().musiclink_spotify(client, source)
        if not candidate:
            raise CatalogError('Test song did not return a matching Spotify link.')
        print('Read-only Spotify conversion succeeded: ' + candidate['url'])

def main():
    path = Path('secrets/musiclink-client.json')
    if path.exists():
        print('MusicLink credentials already exist. Run catalog_preview.py to test them.')
        return 1
    path.parent.mkdir(mode=0o700, exist_ok=True)
    if path.parent.stat().st_mode & 0o077:
        print('The secrets directory must have permissions 700.')
        return 1
    key = getpass.getpass('MusicLink API key (hidden): ').strip()
    if not key or any(c.isspace() for c in key):
        print('A nonempty key without whitespace is required. Nothing was saved.')
        return 1
    fd, temp = tempfile.mkstemp(prefix='.musiclink-setup-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump({'api_key':key}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        asyncio.run(check(Path(temp)))
        os.link(temp, path)
        print('MusicLink key saved with private permissions. No playlist was created or changed.')
        return 0
    except Exception as exc:
        detail = str(exc) if isinstance(exc, CatalogError) else type(exc).__name__
        print('Setup failed: ' + detail + ' Credentials were not installed.')
        return 1
    finally:
        Path(temp).unlink(missing_ok=True)

if __name__ == '__main__':
    raise SystemExit(main())
