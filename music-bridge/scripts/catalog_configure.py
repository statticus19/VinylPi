#!/usr/bin/env python3
"""Save private catalog app credentials after a read-only connection test."""
import argparse
import asyncio
import getpass
import json
import os
from pathlib import Path
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from music_bridge.catalog import Catalogs, CatalogError
import httpx

stage = 'Credential file preparation'

async def check(service,path):
    global stage
    os.environ['MUSIC_BRIDGE_'+service.upper()+'_CLIENT']=str(path)
    catalogs=Catalogs()
    async with httpx.AsyncClient(timeout=25,follow_redirects=False) as client:
        stage = 'Token authorization'
        await catalogs.token(client,service)
        print('Token authorization succeeded.', flush=True)
        stage = 'Catalog search'
        rows = await getattr(catalogs,service)(client,{'type':'song','title':'Still Into You','artist':'Paramore','country':'US'})
        print(f'Catalog search succeeded: {len(rows)} candidates.', flush=True)

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--service',required=True,choices=['spotify','tidal'])
args=parser.parse_args()
path=Path('secrets')/(args.service+'-client.json')
if path.exists():
    print('Credentials already exist. No file was replaced. Run catalog_preview.py to test them.')
    raise SystemExit(1)
path.parent.mkdir(mode=0o700,exist_ok=True)
if path.parent.stat().st_mode & 0o077:
    print('The secrets directory must have permissions 700.')
    raise SystemExit(1)
client_id=getpass.getpass('Client ID (hidden): ').strip()
client_secret=getpass.getpass('Client secret (hidden): ').strip()
if not client_id or not client_secret:
    print('Both values are required. Nothing was saved.')
    raise SystemExit(1)
fd,temp=tempfile.mkstemp(prefix='.catalog-setup-',dir=path.parent)
try:
    with os.fdopen(fd,'w') as stream:
        json.dump({'client_id':client_id,'client_secret':client_secret},stream)
        stream.flush();os.fsync(stream.fileno())
    asyncio.run(check(args.service,Path(temp)))
    # Atomic no-overwrite publication of the already-private validated file.
    stage = 'Credential file save'
    os.link(temp,path)
    print('Read-only catalog connection succeeded. Credentials saved with private permissions.')
    print('No playlist was created. No existing playlist was changed.')
except Exception as exc:
    detail = str(exc) if isinstance(exc, CatalogError) else type(exc).__name__
    if isinstance(exc, OSError) and exc.errno is not None:
        detail = type(exc).__name__ + ', errno=' + str(exc.errno)
    print(stage + ' failed: ' + detail)
    print('Credentials were not installed. No playlist was created or changed.')
    raise SystemExit(1)
finally:
    Path(temp).unlink(missing_ok=True)
