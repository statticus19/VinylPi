#!/usr/bin/env python3
"""Offline pre-publication checks. Does not display suspected secret values."""
import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
TEXT_EXTS = {'.py', '.js', '.css', '.html', '.md', '.txt', '.ini', '.toml', '.yaml', '.yml', '.json', '.conf', '.service', '.socket', '.timer', '.example'}
EXCLUDE = {'.git', '.venv', '.venv-learning', '__pycache__', 'node_modules'}
RISKY_NAME = re.compile(r'(^|[/.])(\.env(?:\.|$)|token\.json$|credentials\.json$|secrets\.h$|id_rsa$|.*\.(?:pem|p12|pfx|key|db|sqlite3?)$)', re.I)
SECRET = re.compile(r'(?im)^\s*(?:LASTFM_PASSWORD|LASTFM_API_SECRET|DISCORD_BOT_TOKEN|GOOGLE_CLIENT_SECRET|YOUTUBE_CLIENT_SECRET|API_SECRET|PASSWORD)[ \t]*[:=][ \t]*[\'\"]?[A-Za-z0-9_\-/+]{12,}')
PRIVATE_NET = re.compile(r'(?<!\d)(?:192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(?!\d)')
PLACEHOLDER = re.compile(r'YOUR-(?:VINYLPI-HOST|PI-LAN-IP)')
errors, warnings = [], []
files = [p for p in ROOT.rglob('*') if p.is_file() and not (set(p.relative_to(ROOT).parts) & EXCLUDE)]
for p in files:
    rel = str(p.relative_to(ROOT))
    if RISKY_NAME.search(rel): errors.append(f'Private-file risk: {rel}')
    if p.suffix.lower() not in TEXT_EXTS and not p.name.endswith(('.service', '.timer', '.socket')): continue
    try: content = p.read_text(encoding='utf-8')
    except (UnicodeError, OSError): continue
    if SECRET.search(content) and not (rel.startswith('config-examples/') and p.name.endswith('.example')): errors.append(f'Secret-like assignment: {rel} (value withheld)')
    if PRIVATE_NET.search(content): warnings.append(f'Private-network literal: {rel}')
    if PLACEHOLDER.search(content) and not (rel.startswith(('docs/', 'config-examples/')) or rel == 'RELEASE-CANDIDATE-STATUS.md' or rel.startswith('tests/')):
        warnings.append(f'Unconfigured placeholder in source: {rel}')
    if p.suffix == '.py':
        try: ast.parse(content, filename=rel)
        except SyntaxError as exc: errors.append(f'Python syntax: {rel}:{exc.lineno}')
if any('nas' in p.name.lower() for p in files): warnings.append('Filename references NAS; inspect before publication')
required = ['vinylpi-dashboard/dashboard/static/favicon.ico', 'vinylpi-dashboard/dashboard/static/social-preview.png', 'firmware', 'config-examples']
for path in required:
    if not (ROOT / path).exists(): warnings.append(f'Missing release dependency: {path}')
print(f'Checked {len(files)} files')
print(f'ERRORS: {len(errors)}')
for v in errors: print('  ERROR:', v)
print(f'WARNINGS: {len(warnings)}')
for v in sorted(set(warnings)): print('  WARNING:', v)
print('Publication approval requires human review, credential/history screening and licensing checks; hardware validation is explicitly out of scope.')
sys.exit(1 if errors else 0)
