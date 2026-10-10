import tomllib
from importlib.resources import files
from pathlib import Path

from aling.core.config import Settings

ROOT = Path(__file__).resolve().parents[1]


def test_project_metadata_and_python_package_use_formal_name():
    with (ROOT / "pyproject.toml").open("rb") as file:
        project = tomllib.load(file)["project"]
    assert project["name"] == "aling"
    assert project["version"] == "0.3.1"
    assert project["authors"] == [{"name": "Jack233_"}]
    assert project["maintainers"] == [{"name": "Jack233_"}]
    assert project["scripts"]["aling"] == "aling.app:main"
    assert project["scripts"]["ryxbot"] == "aling.app:main"
    assert (ROOT / "src/aling/app.py").is_file()
    assert not (ROOT / "src/ryxbot").exists()


def test_default_persona_is_packaged_and_can_still_be_overridden(monkeypatch):
    prompt = files("aling").joinpath("prompts/default.txt").read_text(encoding="utf-8")
    assert len(prompt) > 300
    assert "办事必须靠谱" in prompt
    assert "技术问题" in prompt and "不确定" in prompt

    settings = Settings(_env_file=None, onebot_access_token="test")
    assert settings.ai_system_prompt == prompt.strip()
    monkeypatch.setenv("AI_SYSTEM_PROMPT", "developer custom persona")
    custom = Settings(_env_file=None, onebot_access_token="test")
    assert custom.ai_system_prompt == "developer custom persona"


def test_renaming_preserves_database_path_default():
    settings = Settings(_env_file=None, onebot_access_token="test")
    assert settings.database_path == Path("data/ryxbot.db")
