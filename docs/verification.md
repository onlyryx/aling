# 验证记录

## v0.3.0 最终收尾验证（未提交、未部署）

验证日期：2026-10-10（Asia/Shanghai）。工作树：`codex/aling-v03`，基线提交仍为
`5d2f656d9c08d32b9c25436f0819093c5619e1ac`。

- `uv run --frozen --extra dev pytest -q`：**141 项通过**。保留 1 条现有 Starlette TestClient
  关于 httpx 的弃用提示；未屏蔽提示，也未为此升级依赖。
- `ruff check .`、`ruff format --check .`、`uv lock --check`、`git diff --check` 均通过。
- `uv build` 生成 `dist/aling-0.3.0.tar.gz` 和 `dist/aling-0.3.0-py3-none-any.whl`。
  wheel 在临时目录独立安装后，包导入、默认人格资源、`--version` 和离线 `doctor` 验证通过；
  wheel 同时保留 `aling` 与 `ryxbot` CLI 入口。
- `RYXBOT_ENV_FILE=.env.example docker compose --env-file .env.example --profile qq config --quiet` 通过。
- `docker build -t aling:0.3.0-final-check .` 通过。仅构建独立本地检查标签，未覆盖运行标签。
  隔离的临时容器以虚构 token、禁用网络、只读根文件系统和临时数据目录运行 `aling doctor`，
  配置、依赖、目录与端口诊断通过；容器运行结束后自动删除。
- 独立只读复核的问题均已处理；最后的定向复核无剩余可操作缺陷。

| 验收范围 | 当前证据 |
| --- | --- |
| 环境能力上限、持久化全局开关、群默认关闭与超级用户权限 | `test_features.py`、`test_storage_migrations.py`、带认证的 OneBot 集成测试 |
| `/help`、`/ping`、维护者 `/status`、未知指令与私聊全局命令 | `test_help.py`、`test_status.py`、`test_integration.py` |
| 网页超时、SSRF、限长、正文提取与摘要回退 | `test_services.py`、`test_web_plugin.py` |
| 本地图片目录边界、有界读取、解码验证与发送失败反馈 | `test_memes.py`、`test_integration.py` |
| 只读模型工具、实际群能力过滤、异常响应与供应商回退 | `test_ai_tools.py`、`test_services.py` |
| 短期会话隔离、过期清理、停用/清除、旧回复不能恢复缓存、生命周期关闭 | `test_sessions.py`、`test_features.py`、`test_integration.py` |
| QQ 独立管理权限、单目标、时长限制及真实适配器调用结构 | `test_group_moderation.py`、`test_integration.py`（均为 fake OneBot） |
| 调度双开关、安静时段、DST、跨午夜宽限、失败不重试与重启/删除重建不重复 | `test_scheduler.py`、`test_storage_migrations.py` |
| legacy/v2 数据库迁移保留设置及日期声明 | 临时 SQLite 迁移测试；当前目标 schema 为 3 |
| 默认人格、正式品牌、兼容 CLI、离线诊断和错误参数处理 | `test_rename.py`、`test_cli.py`、独立 wheel 安装检查 |

候选仓库文件、sdist 和 wheel 已检查私密环境文件、数据库及账号/私人素材路径；构建排除规则
保留 `.env`、运行数据、Git、本地工具与缓存的隔离，`.gitignore` 另补充根目录 SQLite 文件保护。
剩余 `ryxbot` 标识均用于已有数据库路径、CLI、Compose 项目名/卷或历史文档兼容。
README、架构、环境示例和变更记录已与当前的群默认关闭、运行时全局开关及隐私行为对齐。

**本轮边界：**未读取或修改真实 `.env`、SQLite、NapCat 配置或账号数据；未访问真实 QQ/模型服务；
未提交、推送、创建标签/Release，未重启或替换现有 bot/NapCat 实例。远端 CI 和这些收尾修改的
真实 QQ/NapCat/模型供应商验收仍应在后续提交、部署流程中完成。此前人工接入记录不能替代本轮新版本的实际验收。

以下是此前各阶段的历史快照，其测试数量和运行边界不代表本轮最终状态。

## v0.3.0 发布候选验证（未发布）

验证日期：2026-10-10（Asia/Shanghai）。

- `uv run --frozen --extra dev pytest -q`：121 项通过；保留 1 条 Starlette 关于
  `httpx` TestClient 的弃用提示，未屏蔽。
- Ruff 检查、格式检查、`uv lock --check` 均通过。
- `uv build` 成功生成 `aling-0.3.0.tar.gz` 与 `aling-0.3.0-py3-none-any.whl`。
- `RYXBOT_ENV_FILE=.env.example docker compose --env-file .env.example --profile qq config --quiet` 通过。
- `docker build -t aling:0.3.0-local .` 成功；Docker 构建排除了真实环境文件、数据目录、
  测试缓存与本地工具。临时容器以虚构令牌运行 `aling doctor`，配置、依赖、临时数据目录和端口检查通过。
- 离线 legacy SQLite 迁移、旧群设置保留、迁移重复执行、群功能开关、AI 工具拒绝路径、
  会话隔离/过期/清除、图片路径与格式验证、OneBot 模拟权限和定时调度测试均通过；调度器重启后不重复发送，定时文本按纯文本消息段传送。
- HTTP `/health` 保持进程存活语义，新增 `/ready` 在 OneBot 连接前返回 503、连接后返回就绪，且 Compose 仍仅检查 `/health`。
- GitHub Actions 固定到经官方上游 release 核对的 `actions/checkout v7.0.1` 与
  `astral-sh/setup-uv v10.2.0` 完整提交 SHA；CI 仍仅具备 `contents: read`。
  [checkout release](https://github.com/actions/checkout/releases/tag/v7.0.1)，
  [setup-uv release](https://github.com/astral-sh/setup-uv/releases/tag/v10.2.0)。
- 未读取真实 `.env`、数据库或 NapCat 配置；未登录 QQ、调用真实模型、推送、打 tag 或公开部署。
  需要维护者在测试群手动验收 QQ 风控、NapCat 和模型供应商兼容性。

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
