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
