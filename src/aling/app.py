import sys

import httpx
import nonebot
from fastapi import FastAPI, Response
from nonebot import get_bots
from nonebot.adapters.onebot.v11 import Adapter
from pydantic import ValidationError
from pydantic_settings import SettingsError

from aling import __version__
from aling.core.config import Settings
from aling.core.logging import configure_logging, log
from aling.runtime import Runtime, set_runtime
from aling.services.proactive import send_group_text


def build_app(
    settings: Settings, *, ai_transport: httpx.AsyncBaseTransport | None = None
) -> FastAPI:
    configure_logging(settings.log_level)
    # Parse deployment settings once. Force passive reverse WS, never outgoing account URLs.
    nonebot.init(
        _env_file=(".env.example.invalid",),
        driver="~fastapi",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        onebot_access_token=settings.onebot_access_token.get_secret_value(),
        onebot_ws_urls=[],
        onebot_api_roots={},
        superusers={str(user) for user in settings.superusers},
        nickname=set(),
        command_start={"/"},
        command_sep={"."},
        api_timeout=10,
        session_expire_timeout=60,
    )
    driver = nonebot.get_driver()
    driver.register_adapter(Adapter)
    runtime = Runtime.create(settings, ai_transport)
    set_runtime(runtime)

    @driver.on_bot_connect
    async def bot_connected(bot) -> None:
        runtime.status.connected_to(bot)

    @driver.on_bot_disconnect
    async def bot_disconnected(bot) -> None:
        runtime.status.disconnected_from(bot)

    @nonebot.message.event_preprocessor
    async def event_seen(bot, event) -> None:
        runtime.status.connected_to(bot)
        runtime.status.event_received()

    @driver.on_startup
    async def startup() -> None:
        await runtime.startup()
        await runtime.scheduler.start(
            send_schedule,
            lambda: bool(get_bots()),
        )
        log.info("阿零 (ALING) ready; waiting for authenticated OneBot connection")

    @driver.on_shutdown
    async def shutdown() -> None:
        await runtime.shutdown()
        log.info("阿零 (ALING) stopped")

    async def send_schedule(group_id: int, text: str) -> None:
        bot = next(iter(get_bots().values()), None)
        if bot is None:
            return
        await send_group_text(bot, group_id, text)

    for plugin in [
        "aling.plugins.commands",
        "aling.plugins.ai_chat",
        "aling.plugins.web_read",
        "aling.plugins.meme",
        "aling.plugins.group_moderation",
        *settings.extra_plugins,
    ]:
        if nonebot.load_plugin(plugin) is None:
            raise RuntimeError("A configured plugin could not be loaded")
    app = nonebot.get_app()
    if settings.webui_enabled:
        from aling.webui import mount_webui

        mount_webui(app, runtime)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    async def ready(response: Response) -> dict[str, str]:
        if not get_bots():
            response.status_code = 503
            return {"status": "not_ready"}
        return {"status": "ready"}

    return app


def main() -> None:
    if sys.argv[1:] in (["--help"], ["-h"]):
        print(
            "用法：aling [doctor | --help | --version]\n"
            "直接运行 aling 启动阿零；doctor 执行本地离线检查。"
        )
        return
    if sys.argv[1:] == ["--version"]:
        print(f"ALING {__version__}")
        return
    if sys.argv[1:] == ["doctor"]:
        from aling.cli import doctor

        raise SystemExit(doctor())
    if sys.argv[1:]:
        print("不支持的命令参数；用法：aling [doctor | --help | --version]", file=sys.stderr)
        raise SystemExit(2)
    configure_logging()
    try:
        settings = Settings()
    except ValidationError as exc:
        fields = [
            ".".join(str(part) for part in error["loc"]) or "configuration"
            for error in exc.errors(include_input=False, include_context=False)
        ]
        log.error("Invalid configuration fields: {}. See .env.example", ", ".join(fields))
        raise SystemExit(2) from None
    except SettingsError:
        log.error("Invalid configuration format; check JSON arrays in .env.example")
        raise SystemExit(2) from None
    build_app(settings)
    # Access logs may contain request URLs/credentials. Keep server logs on the privacy sink.
    nonebot.run(access_log=False, proxy_headers=False, forwarded_allow_ips="")


if __name__ == "__main__":
    main()
