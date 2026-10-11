"""The installed wheel contains a self-hosted panel with no external assets."""

import os
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_installed_wheel_contains_all_panel_assets_and_module_references(tmp_path):
    dist = tmp_path / "dist"
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(dist)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    (wheel,) = dist.glob("aling-*.whl")
    target = tmp_path / "installed"
    subprocess.run(
        ["uv", "pip", "install", "--target", str(target), "--no-deps", str(wheel)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    installed = tmp_path / "inspection"
    installed.mkdir()
    code = """
import re
from importlib.resources import files
root = files('aling.webui').joinpath('static')
expected = ['index.html', 'styles.css', 'app.js', 'api.js',
            'pages/overview.js', 'pages/features.js', 'pages/memes.js',
            'pages/schedules.js']
paths = {name: root.joinpath(name) for name in expected}
assert all(path.is_file() for path in paths.values())
sources = {name: path.read_text() for name, path in paths.items()}
assert not re.search(r'https?://', ''.join(sources.values()))
html = sources['index.html']
assert '/webui/styles.css' in html and '/webui/app.js' in html
for name, source in sources.items():
    base = root.joinpath(name).parent
    for relative in re.findall(r'from "([./][^"]+)"', source):
        assert base.joinpath(relative).is_file()
"""
    env = {**os.environ, "PYTHONPATH": str(target)}
    subprocess.run([sys.executable, "-c", code], cwd=installed, env=env, check=True)

    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        metadata = next(name for name in names if name.endswith("METADATA"))
        package_metadata = archive.read(metadata).decode()
    assert "aling/webui/static/index.html" in names
    assert "aling/webui/static/styles.css" in names
    assert "aling/webui/static/pages/schedules.js" in names
    assert "Version: 0.4.0" in package_metadata
    assert tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"] == "0.4.0"
