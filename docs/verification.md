# 验证记录

## 本地发布前检查

验证日期：2026-10-10（Asia/Shanghai）。

- `uv run --frozen --extra dev pytest -q`：59 项通过；保留 1 条 Starlette 关于
  `httpx` TestClient 的弃用提示，未屏蔽。
- Ruff 检查和格式检查通过；`uv lock --check` 通过。
- `uv build` 生成 `aling-0.1.0` sdist 和 wheel。
- `RYXBOT_ENV_FILE=.env.example docker compose --env-file .env.example --profile qq config --quiet`
  通过；`docker build -t aling:local .` 通过。
- 更新已有 `.dockerignore`，保留环境文件、Git 元数据、运行目录、测试和本地工具
  排除规则，并补充根目录及其他路径下 SQLite 数据库和 WAL/journal 文件的排除。
- `.env` 未被 Git 跟踪且受忽略规则保护；Git 历史中没有 `.env`、数据库或 NapCat
  二维码文件路径。工作区存在被忽略的 `data/ryxbot.db`，没有读取其内容。
- 这些检查未覆盖远程 CI；未使用真实 QQ 或模型凭据。首次推送后的 CI 状态需在 GitHub Actions 确认。

以下记录按时间列出迁名前和命名迁移时的验证结果；更早记录中的环境限制不代表当前状态。

## ALING 命名迁移验证

验证日期：2026-10-10（Asia/Shanghai）。

- Ruff 检查与格式检查通过；Python 全量测试 59 项通过。
- `uv lock --check` 通过，项目元数据与锁文件一致。
- Python wheel/sdist 构建通过；新 wheel 已实测导入与默认人格资源。CLI 主入口为
  `aling`，旧 `ryxbot` CLI 保留为兼容入口；两个命令均已验证可启动并在缺少令牌时安全退出。
- Compose 配置验证通过，镜像改为 `aling`，项目名和数据卷沿用 `ryxbot`，原数据库
  路径继续使用 `data/ryxbot.db`。
- `docker build -t aling:local .` 通过。
- 未登录 QQ、未请求真实模型或部署；远程 CI 状态需在 GitHub Actions 确认。

## 迁名前第一阶段验证记录

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
