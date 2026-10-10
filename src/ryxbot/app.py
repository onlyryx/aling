import httpx
import nonebot
from fastapi import FastAPI
from nonebot.adapters.onebot.v11 import Adapter
from pydantic import ValidationError
from pydantic_settings import SettingsError

from ryxbot.core.config import Settings
from ryxbot.core.logging import configure_logging, log
from ryxbot.runtime import Runtime, set_runtime


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

    @driver.on_startup
    async def startup() -> None:
        await runtime.startup()
        log.info("ryxbot ready; waiting for authenticated OneBot connection")

    @driver.on_shutdown
    async def shutdown() -> None:
        await runtime.shutdown()
        log.info("ryxbot stopped")

    for plugin in ["ryxbot.plugins.commands", "ryxbot.plugins.ai_chat", *settings.extra_plugins]:
        if nonebot.load_plugin(plugin) is None:
            raise RuntimeError("A configured plugin could not be loaded")
    app = nonebot.get_app()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def main() -> None:
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
    nonebot.run(access_log=False)


if __name__ == "__main__":
    main()
