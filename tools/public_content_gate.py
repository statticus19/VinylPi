#!/usr/bin/env python3
"""Fail closed on obvious accidental private payloads in a candidate source tree."""
from pathlib import Path
import argparse
import re

DENIED_PARTS = {'.venv', '.git', '__pycache__', 'state', 'backups'}
DENIED_NAMES = {'token.json', 'credentials.json', 'secrets.h', 'id_rsa', '.env'}
DENIED_SUFFIXES = {'.db', '.sqlite', '.sqlite3', '.pem', '.p12', '.pfx', '.key'}
PRIVATE = re.compile(r'(?<!\d)(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3})(?!\d)')
# Only scan shipped source/config, not docs that teach about private IP ranges.
SOURCE_SUFFIXES = {'.py', '.js', '.html', '.css', '.ino', '.h', '.cpp', '.service', '.socket', '.timer', '.conf', '.xml', '.cfg'}

def scan(root):
    findings=[]
    for path in sorted(root.rglob('*')):
        if not path.is_file():
            continue
        parts=path.relative_to(root).parts
        if any(part in DENIED_PARTS or part.startswith('.venv') for part in parts):
            if '.git' in parts or '__pycache__' in parts:
                continue
            findings.append((path, 'forbidden directory')); continue
        if path.name in DENIED_NAMES or path.suffix.lower() in DENIED_SUFFIXES:
            findings.append((path, 'forbidden filename')); continue
        if path.suffix.lower() in SOURCE_SUFFIXES:
            try: data=path.read_text(encoding='utf-8')
            except (UnicodeError, OSError): continue
            if PRIVATE.search(data): findings.append((path, 'embedded private-network address'))
            if re.search(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',data): findings.append((path,'embedded private key'))
    return findings

if __name__=='__main__':
    arg=argparse.ArgumentParser(); arg.add_argument('root',type=Path,nargs='?',default=Path(__file__).resolve().parents[1]); ns=arg.parse_args()
    if not ns.root.is_dir(): arg.error('root is not a directory')
    bad=scan(ns.root)
    for p,reason in bad: print(f'FAIL {p.relative_to(ns.root)}: {reason}')
    print(f'Content gate: {len(bad)} findings (heuristic, not a credential or history audit)')
    raise SystemExit(bool(bad))
