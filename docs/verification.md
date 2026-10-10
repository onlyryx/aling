# 第一阶段验证记录

验证日期：2026-10-10（Asia/Shanghai）。

## 环境

- 初始工作区为空、无 Git；已初始化 `main` 分支。
- 系统默认 Python 3.9.9 不适用；使用可用的 Python 3.12.14 创建项目专用 `.venv`。
- uv 0.13.0；锁定 NoneBot2 2.5.0、OneBot 适配器 2.4.6、httpx 0.28.1。
- Docker CLI 可用，但 Docker Engine 未运行，最初也没有 Compose 插件。
  下载并校验官方 Compose v5.6.0 的 SHA256，只放在被忽略的 `.tools/` 中用于配置验证；
  未修改系统工具、未启动容器。

## 已执行且通过

- `uv sync --extra dev`：安装和锁文件解析成功。
- `pytest -q`：56 项通过。覆盖配置、权限、SQLite、冷却/频率、AI 成功与失败、
  超时/并发/大小、SSRF 地址与 DNS 固定、拒绝重定向/压缩、网页解析、日志隐私、
  OneBot 令牌校验与命令/AI API 往返。
- CLI 子进程：无凭据/错误 JSON 配置退出码 2，日志无敏感值和异常堆栈；
  仅本地 HTTP 启动、健康检查、关闭钩子以及 DEBUG 级别令牌隐私通过。
- `ruff check .`、`ruff format --check .`、`compileall`：通过。
- `uv build`：生成 `dist/ryxbot-0.1.0.tar.gz` 和 `dist/ryxbot-0.1.0-py3-none-any.whl`。
- 将构建的 wheel 安装到隔离目标目录，以临时配置实际启动；HTTP 健康检查与
  正常关闭通过。没有连接 QQ。
- 官方 Compose CLI 的 `config --quiet`（包含 `qq` profile）通过；默认
  `config --services` 只返回 `bot`。使用 `.env.example` 验证，无真实密钥。

## 审查与回归

独立只读审查发现两项需要修复的问题，均已加入先失败后通过的测试：
回复自动附带 @ 的误触发，以及 HTTP 压缩流先解压再检查大小。
实际包启动检查还发现 NoneBot 驱动重复日志参数导致启动失败，已修复并加入
真实 CLI 回归测试。格式错误的 JSON 环境配置改为安全失败。

## 环境限制与未进行的操作

- `docker build -t ryxbot:local .` 已尝试：无法连接本机 Docker socket，镜像构建
  与容器运行**没有通过本地验证**。CI 已配置 Linux Docker 构建，但本次未运行远程 CI。
- 测试有一条 Starlette 对 `httpx` TestClient 后端的弃用提示，属于依赖迁移提醒；
  现有 OneBot 模拟连接测试通过。没有通过屏蔽警告伪造干净输出。
- 未登录真实 QQ、未请求真实模型/群成员提供的网页，未部署、未推送或创建远程仓库。
  QQ 实机登录后的兼容性仍需维护者在准备好账号和令牌后验证。
