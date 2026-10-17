"""Non-invasive NAS-free dashboard smoke tests. Run from repository root with pytest."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'vinylpi-dashboard'))
os.environ.setdefault('VINYLPI_DASHBOARD_USER', 'review')
os.environ.setdefault('VINYLPI_DASHBOARD_PASSWORD_SCRYPT', 'deadbeef:deadbeef')
os.environ.setdefault('VINYLPI_DASHBOARD_SESSION_SECRET', '0123456789abcdef0123456789abcdef0123456789abcdef')

from fastapi.testclient import TestClient
from dashboard.main import app


def test_login_page():
    with TestClient(app) as client:
        resp = client.get('/login')
        assert resp.status_code == 200
        assert 'password' in resp.text.lower()


def test_requires_authentication():
    with TestClient(app) as client:
        resp = client.get('/', follow_redirects=False)
        assert resp.status_code in (302, 303, 307)
        assert resp.headers['location'].startswith('/login')


def test_static_assets():
    with TestClient(app) as client:
        for name in ('theme.css', 'social-preview.png', 'favicon.ico'):
            assert client.get('/static/' + name).status_code == 200


def test_removed_nas_endpoints():
    with TestClient(app) as client:
        for path in ('/nas', '/api/nas'):
            assert client.get(path).status_code == 404
    assert not any('nas' in str(getattr(route, 'path', '')).lower() for route in app.routes)
