from pathlib import Path
import ast
ROOT = Path(__file__).resolve().parents[1]

def test_no_staging_host_placeholder_in_application_code():
    for root in ('music-bridge', 'vinylpi-dashboard', 'vinylpi-guest'):
        for p in (ROOT / root).rglob('*'):
            if p.is_file() and p.suffix in {'.py', '.html', '.js'}:
                s=p.read_text(encoding='utf8')
                assert 'YOUR-VINYLPI-HOST' not in s, p
                assert 'YOUR-PI-LAN-IP' not in s, p

def test_all_python_syntax():
    for p in ROOT.rglob('*.py'):
        if '.venv' not in str(p) and '__pycache__' not in str(p):
            ast.parse(p.read_text(encoding='utf8'), str(p))
