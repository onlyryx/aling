import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest


def environment(tmp_path):
    return dict(
        os.environ,
        ONEBOT_ACCESS_TOKEN="local-test-token",
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


def test_doctor_checks_configuration_offline_and_hides_token(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        free_port = sock.getsockname()[1]
    env = dict(environment(tmp_path), ONEBOT_ACCESS_TOKEN="DO_NOT_PRINT_ME", PORT=str(free_port))
    result = subprocess.run(
        [sys.executable, "-m", "aling.app", "doctor"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "配置：通过" in result.stdout
    assert "数据目录：可写" in result.stdout
    assert "DO_NOT_PRINT_ME" not in result.stdout + result.stderr


def test_doctor_ignores_legacy_ai_enabled_flag_without_credentials(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(environment(tmp_path), AI_ENABLED="true", AI_API_KEY="", PORT=str(port))
    result = subprocess.run(
        [sys.executable, "-m", "aling.app", "doctor"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert "配置：通过" in result.stdout


@pytest.mark.parametrize("args, expected", [(["--help"], 0), (["--version"], 0), (["typo"], 2)])
def test_cli_arguments_do_not_start_server_or_read_config(tmp_path, args, expected):
    env = dict(environment(tmp_path), ONEBOT_ACCESS_TOKEN="", SUPERUSERS="invalid-private-value")
    result = subprocess.run(
        [sys.executable, "-m", "aling.app", *args],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == expected
    assert "invalid-private-value" not in result.stdout + result.stderr
    assert "Invalid configuration" not in result.stderr
    assert not (tmp_path / "settings.db").exists()
    if args == ["--version"]:
        assert "0.5.0" in result.stdout
