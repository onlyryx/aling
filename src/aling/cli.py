import os
import socket

from pydantic import ValidationError
from pydantic_settings import SettingsError

from aling.core.config import Settings


def doctor() -> int:
    """Run local, offline checks without starting NoneBot or contacting services."""
    try:
        settings = Settings()
    except (ValidationError, SettingsError):
        print("配置：失败（请检查 .env 与 .env.example；具体值已隐藏）")
        return 1
    print("配置：通过（敏感值已隐藏）")
    print("Python 依赖：通过")
    data_dir = settings.database_path.parent
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        writable = os.access(data_dir, os.W_OK)
    except OSError:
        writable = False
    print(f"数据目录：{'可写' if writable else '不可写'}")
    if not writable:
        return 1
    try:
        with socket.socket() as sock:
            sock.bind((settings.host, settings.port))
        print(f"本地端口 {settings.port}：可用")
    except OSError:
        print(f"本地端口 {settings.port}：占用或不可绑定")
        return 1
    return 0
