import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def environment(tmp_path):
    return dict(
        os.environ,
        ONEBOT_ACCESS_TOKEN="local-test-token",
        AI_ENABLED="false",
        AI_API_KEY="",
        LOG_LEVEL="DEBUG",
        HOST="127.0.0.1",
        DATABASE_PATH=str(tmp_path / "settings.db"),
        PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"),
        EXTRA_PLUGINS="[]",
    )


def test_cli_rejects_invalid_configuration_without_printing_secret(tmp_path):
    env = dict(
        environment(tmp_path),
        ONEBOT_ACCESS_TOKEN="",
        AI_ENABLED="true",
        AI_API_KEY="DO_NOT_PRINT_ME",
    )
    result = subprocess.run(
        [sys.executable, "-m", "aling.app"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert "DO_NOT_PRINT_ME" not in result.stdout + result.stderr
    assert "Invalid configuration" in result.stderr


def test_cli_starts_http_server_and_shuts_down_without_qq_connection(tmp_path):
    env = environment(tmp_path)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        env["PORT"] = str(sock.getsockname()[1])
    process = subprocess.Popen(
        [sys.executable, "-m", "aling.app"],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    healthy = False
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and process.poll() is None:
            try:
                with urllib.request.urlopen(
                    "http://127.0.0.1:" + env["PORT"] + "/health", timeout=1
                ) as response:
                    healthy = response.read() == b'{"status":"ok"}'
                    break
            except OSError:
                time.sleep(0.05)
    finally:
        process.terminate()
        stdout, stderr = process.communicate(timeout=5)
    assert healthy, stderr
    # Uvicorn re-raises the shutdown signal after completing its lifecycle hooks.
    assert process.returncode in (0, -signal.SIGTERM)
    assert "阿零 (ALING) stopped" in stderr
    assert "local-test-token" not in stdout + stderr


def test_cli_rejects_malformed_json_configuration_without_traceback(tmp_path):
    env = dict(environment(tmp_path), SUPERUSERS="invalid-json-DO_NOT_PRINT_ME")
    result = subprocess.run(
        [sys.executable, "-m", "aling.app"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert "DO_NOT_PRINT_ME" not in result.stderr
