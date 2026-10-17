#!/usr/bin/env python3
"""Read-only catalog check. No playlist is created or modified."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from music_bridge.catalog import Catalogs, CatalogError

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--url',required=True)
parser.add_argument('--service',choices=['spotify','youtube_music','tidal'])
args=parser.parse_args()
try:
    result=asyncio.run(Catalogs().resolve(args.url,[args.service] if args.service else None))
    print(json.dumps(result,indent=2,ensure_ascii=False))
except Exception as exc:
    print('Preview stopped: '+(str(exc) if isinstance(exc,(CatalogError,ValueError)) else 'File, network, or source response error.'),file=sys.stderr)
    raise SystemExit(1)
