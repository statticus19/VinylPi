"""Offline, no-Pi/no-network checks for the public edition's privilege boundary."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / 'vinylpi-dashboard/dashboard/main.py'
BROKER = ROOT / 'support/vinylpi-control-server'
SOCKET = ROOT / 'systemd-snapshot/vinylpi-control.socket'
SERVICE = ROOT / 'systemd-snapshot/vinylpi-control.service'


def _frozenset_strings(tree, variable):
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == variable for t in node.targets):
            if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id == 'frozenset':
                return ast.literal_eval(node.value.args[0])
    raise AssertionError(f'Cannot statically resolve {variable}')


def test_dashboard_restart_allowlist_is_subset_of_broker():
    dashboard = _frozenset_strings(ast.parse(MAIN.read_text()), 'RESTARTABLE')
    broker = _frozenset_strings(ast.parse(BROKER.read_text()), 'APPROVED')
    assert dashboard <= broker, f'Dashboard offers operations rejected by broker: {dashboard - broker}'
    assert all(x.endswith('.service') for x in dashboard | broker)


def test_broker_no_shell_execution_and_peer_uid_check():
    code = BROKER.read_text()
    tree = ast.parse(code)
    assert 'SO_PEERCRED' in code
    assert 'uid != VINYL_UID' in code
    assert 'LISTEN_FDS' in code
    assert 'shell=True' not in code
    assert 'os.system(' not in code
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    runs = [n for n in calls if isinstance(n.func, ast.Attribute) and n.func.attr == 'run']
    assert runs
    assert all(not any(k.arg == 'shell' and isinstance(k.value, ast.Constant) and k.value.value is True for k in n.keywords) for n in runs)


def test_socket_has_private_permissions_and_no_network_listener():
    cfg = SOCKET.read_text()
    assert 'ListenStream=/run/vinylpi-control.sock' in cfg
    assert 'SocketMode=0600' in cfg
    assert 'SocketUser=vinyl' in cfg
    assert 'SocketGroup=vinyl' in cfg
    assert '0.0.0.0:' not in cfg
    svc = SERVICE.read_text()
    assert 'User=root' in svc  # Elevated privileges are intentionally isolated.
    assert 'RestrictAddressFamilies=AF_UNIX' in svc
    assert 'NoNewPrivileges=true' in svc


def test_dashboard_control_routes_require_csrf():
    tree = ast.parse(MAIN.read_text())
    funcs = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for name in ('restart_service', 'reboot_system'):
        assert name in funcs
        code = ast.get_source_segment(MAIN.read_text(), funcs[name])
        assert 'hmac.compare_digest' in code
        assert 'get_session(' in code


def test_nas_routes_and_modules_absent():
    dash = ROOT / 'vinylpi-dashboard/dashboard'
    assert not list(dash.glob('nas*.py'))
    assert not list((dash / 'static').glob('nas*.js'))
    assert not list((dash / 'templates').glob('nas*.html'))
    code = MAIN.read_text()
    assert "'/nas" not in code and '"/nas' not in code
