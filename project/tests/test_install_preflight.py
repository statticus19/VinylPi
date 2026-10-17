from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('install_preflight', ROOT/'tools/install_preflight.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

def test_preflight_does_not_need_secrets():
    found = mod.check_tree(ROOT)
    labels = {name for ok, name in found}
    assert 'firmware: CrowPanel sketch' in labels
    assert 'source: pi_epaper/display_service.py' in labels
    assert not any('password' in label for label in labels)

def test_preflight_reports_missing_source(tmp_path):
    assert any(not ok for ok, _ in mod.check_tree(tmp_path))
