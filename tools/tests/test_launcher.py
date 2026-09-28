import importlib.util
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("uuv_launcher", ROOT/"tools/launcher.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def test_root_launcher_accepts_flags_from_another_directory(tmp_path):
    result = subprocess.run(["sh", str(ROOT/"run.sh"), "--help"], cwd=tmp_path,
        capture_output=True, text=True, check=True)
    assert "--status" in result.stdout
    assert "--no-model" in result.stdout


def test_existing_supervisor_is_recognized_without_matching_other_processes():
    previous = str(ROOT/"tools/scripts/run.py").encode()
    current = str(ROOT/"tools/launcher.py").encode()
    assert launcher.is_launcher_command([b"python", previous, b"--foreground"])
    assert launcher.is_launcher_command([b"python", current, b"--foreground"])
    assert not launcher.is_launcher_command([b"python", previous, b"--status"])
    assert not launcher.is_launcher_command([b"python", b"/somewhere/launcher.py", b"--foreground"])


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
