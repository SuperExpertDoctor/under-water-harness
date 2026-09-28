import importlib.util
from pathlib import Path


spec = importlib.util.spec_from_file_location("uuv_launcher", Path(__file__).parents[1]/"scripts/run.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def test_process_identity_never_accepts_unrelated_pid():
    assert launcher.is_our_supervisor(1) is False
    assert launcher.is_our_supervisor(-1) is False
    assert launcher.is_our_supervisor(999999999) is False


def test_free_port_does_not_reuse_occupied_listener():
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        assert launcher.free_port(port) != port


def test_worker_environments_cannot_cross_side_authentication():
    friendly, enemy = launcher.worker_environments({'UUV_WORKER_TOKEN': 'friendly', 'UUV_ADVERSARY_TOKEN': 'enemy', 'LONGCAT_API_KEY': 'test'})
    assert 'UUV_ADVERSARY_TOKEN' not in friendly
    assert 'UUV_WORKER_TOKEN' not in enemy
    assert friendly['LONGCAT_API_KEY'] == enemy['LONGCAT_API_KEY'] == 'test'
