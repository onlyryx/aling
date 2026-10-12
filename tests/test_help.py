from nonebot.plugin import PluginMetadata

from aling.services.help import render_help


def test_help_renders_loaded_plugin_usage_and_hides_disabled_capabilities():
    plugins = [
        type(
            "Plugin",
            (),
            {"metadata": PluginMetadata(name="base", description="", usage="/ping")},
        )(),
        type(
            "Plugin",
            (),
            {
                "metadata": PluginMetadata(
                    name="web",
                    description="",
                    usage="/read URL",
                    extra={"aling_feature": "web"},
                )
            },
        )(),
        type(
            "Plugin",
            (),
            {
                "metadata": PluginMetadata(
                    name="meme",
                    description="",
                    usage="/meme query",
                    extra={"aling_feature": "meme"},
                )
            },
        )(),
    ]

    text = render_help(plugins, {"meme"})

    assert "/ping" in text
    assert "/meme query" in text
    assert "/read URL" not in text
    assert "/forget" not in text
    assert "阿零（ALING）" in text


def test_help_shows_forget_only_when_group_memory_is_enabled():
    plugin = type(
        "Plugin",
        (),
        {"metadata": PluginMetadata(name="base", description="", usage="/ping")},
    )()

    text = render_help([plugin], {"memory"})

    assert "/forget" in text
    assert "不保存普通聊天" in text


def test_help_documents_online_gates_and_external_flow_before_enabling():
    text = render_help([], set())
    assert "/feature global search|online_meme on|off" in text
    assert "/feature search|online_meme on|off" in text
    assert "用途检查" in text
    assert "模型" in text
    assert "上游" in text
    assert "确认" in text


def test_help_explains_local_first_memes_and_current_online_feature_state():
    disabled = render_help([], set())
    assert "默认仅搜索本地表情包" in disabled
    assert "在线表情当前未启用" in disabled
    assert "需开启 meme、online_meme 与 AI" in disabled
    assert "联网搜索当前未启用" in disabled

    enabled = render_help([], {"ai", "meme", "online_meme", "search"})
    assert "在线表情已开放" in enabled
    assert "服务就绪且来源获准时" in enabled
    assert "联网搜索当前已启用" in enabled
