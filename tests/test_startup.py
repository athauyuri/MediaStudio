from pathlib import Path
import subprocess
import sys


def test_native_startup_and_clean_shutdown(tmp_path):
    root = Path(__file__).resolve().parents[1]
    # Exercise the real entry point and Windows Qt platform in a fresh process.
    code = '''
import sys
from PySide6.QtCore import QTimer
import app.main as entry
original = entry.MainWindow.show
def show(window):
    original(window)
    QTimer.singleShot(150, window.close)
entry.MainWindow.show = show
raise SystemExit(entry.main(["--runtime-root", sys.argv[1]]))
'''
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path)],
                            cwd=root, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    log = (tmp_path / "logs" / "media-studio.log").read_text()
    assert "Application starting" in log
    assert "Application stopped" in log
    assert set(p.name for p in tmp_path.iterdir()) == {"logs", "data"}
    assert (tmp_path / "data" / "media_studio.db").is_file()


def test_invalid_config_aborts_without_runtime_writes(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text("worker_count = 0")
    result = subprocess.run([sys.executable, "-m", "app", "--config", str(path),
                             "--runtime-root", str(tmp_path / "runtime")],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 1
    assert "startup failed" in result.stderr
    assert not (tmp_path / "runtime").exists()
