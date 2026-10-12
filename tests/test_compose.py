from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_search_overlay_is_opt_in_private_and_keeps_base_volumes_unchanged():
    base = yaml.safe_load((ROOT / "compose.yaml").read_text())
    overlay = yaml.safe_load((ROOT / "compose.search.yaml").read_text())
    service = overlay["services"]["search"]

    assert service["profiles"] == ["search"]
    assert service["image"].startswith("searxng/searxng:2026.")
    assert "ports" not in service
    assert "search" not in base["services"]["bot"].get("depends_on", {})
    assert set(base["volumes"]) == {"bot-data", "napcat-qq", "napcat-config"}
    assert "search-cache" in overlay["volumes"]
    assert service["environment"]["GRANIAN_LOG_ACCESS_ENABLED"] == "false"
    assert service["environment"]["FORCE_OWNERSHIP"] == "false"
    assert all("/app/data" not in item for item in service["volumes"])


def test_search_settings_enable_post_json_strict_safe_search_and_fixed_engines():
    settings = yaml.safe_load((ROOT / "deploy/searxng/settings.yml.example").read_text())

    assert settings["server"]["method"] == "POST"
    assert settings["server"]["secret_key"] == "$SEARXNG_SECRET"
    assert settings["search"]["formats"] == ["json"]
    assert settings["search"]["safe_search"] == 2
    assert settings["use_default_settings"]["engines"]["keep_only"] == [
        "duckduckgo",
        "wikipedia",
        "duckduckgo images",
    ]
