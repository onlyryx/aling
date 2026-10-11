# 阿零（ALING）

阿零（ALING）是由 Jack233_ 开发、属于 onlyryx 个人品牌的开源 QQ 群机器人。
它既是能聊天、接梗的电子群友，也是可以通过插件扩展、处理实用任务的群聊助手。
项目以工具功能为核心，支持可配置的 AI 模型与长期演进的插件架构；人格设定是
“平时不太正经，办事必须靠谱”。

源码与问题反馈：[GitHub 仓库](https://github.com/onlyryx/aling)。

项目版本：**0.4.0**。使用 Python 3.12+、NoneBot2、OneBot V11、NapCatQQ、SQLite、
httpx、BeautifulSoup 与 Pillow，采用 `plugins → services → core` 分层，保留
NoneBot 原生插件加载方式。许可证：MIT。

## 当前功能

- `/help`、`/ping`：查看可用帮助和连通性；`/status` 仅供配置的超级用户查看 OneBot 状态。
- 群内明确 `@阿零 你的问题`：调用配置的 OpenAI 兼容聊天接口。
- `/read <URL> [--summary]`：读取公开网页；摘要会把选取的网页正文发送给模型。
- `/meme <关键词>`：从部署者配置的本地表情包清单中查找并发送一张图片。
- `/mute @成员 [分钟]`、`/unmute @成员`：由本群群主或管理员发起，机器人也必须有群管理权限。
- `/proactive on|off` 与 `/proactive schedule HH:MM <文本>|off|status`：配置显式定时消息。
- `/feature ai|web|meme|memory on|off`：本群管理员切换本群功能；启用记忆需先阅读说明再确认。
- `/feature global ai|web|meme|memory on|off`：配置的超级用户即时调整全局功能开关。
- 可选的阿零管理面板：通过浏览器查看运行状态、管理功能开关、素材和定时消息。

AI 聊天不收集普通群聊。短期会话默认关闭，启用后只暂存明确 @ 机器人的
“群号 + 用户号”独立问答，最多 6 轮；空闲 10 分钟后过期，后台最迟一分钟内清理，重启即清空。
`/forget` 可清除自己的会话，即使短期会话功能已关闭也可使用。
AI 可选工具调用默认关闭，只能请求白名单内的网页阅读或本地表情包搜索，不支持系统命令、
代码执行或群管理。主动发言和群级功能均受全局开关、群级权限与频率限制约束。

默认功能均关闭或受显式命令控制。启用方法见下方配置和 [表情包素材说明](docs/memes/README.md)。
人格提示词位于 `src/aling/prompts/default.txt`，`AI_SYSTEM_PROMPT` 环境变量仍可覆盖。
总体安全和数据流见[架构说明](docs/architecture.md)，版本变化见[变更记录](CHANGELOG.md)。

## 本地运行

准备 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)。
从项目目录运行：

```sh
uv sync --frozen --extra dev
cp .env.example .env
```

编辑 `.env`，至少填写 `ONEBOT_ACCESS_TOKEN`。可在本地生成随机令牌：

```sh
uv run python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

配置模型服务后，超级用户可以通过 `/feature global ai on` 开启 AI；API 凭据不完整时
阿零会拒绝开启。需设置：

```dotenv
AI_API_KEY=你自己的模型服务密钥
AI_BASE_URL=https://你的模型服务地址/v1
AI_MODEL=服务商支持的模型标识
```

上面的值是说明用示例，不是可用凭据。`AI_BASE_URL` 是完整 API 基础地址，客户端
会追加 `/chat/completions`；已包含该路径会导致路径重复。兼容接口需支持
`messages`、`max_tokens`、`stream=false`，并返回 `choices[0].message.content` 文本。
部署者可配置本地 HTTP 模型服务，群成员与模型输出不能修改服务地址。
客户端请求未压缩响应，拒绝忽略 `Accept-Encoding: identity` 而强制返回压缩内容的服务。

## 启用可选功能

可选功能的全局开关默认关闭。具备 `SUPERUSERS` 权限的部署者可用
`/feature global <功能> on|off` 即时切换，并写入 SQLite 持久保存；除 AI 外，网页阅读和本地表情包
不需要额外 `.env` 功能许可。AI 与短期会话要求配置 `AI_API_KEY`、`AI_MODEL`。全局开启后，每个群
仍默认关闭，群主/管理员（或配置的超级用户）还需在允许的群内显式设置群开关。全局命令可在私聊
或允许的群内使用，普通群管理员没有此权限。

| 能力 | 全局开关 | 群内操作 |
| --- | --- | --- |
| AI 聊天 | `/feature global ai on`（需模型凭据） | `/feature ai on` |
| 网页阅读 | `/feature global web on` | `/feature web on` |
| 本地表情包 | `/feature global meme on` 并配置素材清单 | `/feature meme on` |
| AI 工具选择 | `AI_TOOLS_ENABLED=true`，且启用 AI 与对应网页/表情包功能 | 使用明确 @ 提问；模型供应商需支持工具调用 |
| 短期会话 | `/feature global ai on`、`/feature global memory on` | `/feature memory on` 阅读数据说明，再 `/feature memory confirm` |
| 定时主动消息 | `PROACTIVE_ENABLED=true` | `/proactive on` 并配置 `/proactive schedule HH:MM <文本>` |

`/feature memory on` 的说明必须由群管理员先阅读；确认后，同一用户在该群明确 @ 的近期问题和
回复会随新问题发送给模型服务。使用 `/forget` 清除个人会话，管理员运行 `/feature memory off`
会清除整群的短期会话缓存。关闭本群 AI 同样会清理本群缓存；全局关闭 AI 或记忆则清理全部缓存，
正在处理的旧回复不会重新写入已清除的会话。群内开关不能覆盖运行时全局开关。
`AI_TOOLS_ENABLED` 和 `PROACTIVE_ENABLED` 仍仅通过部署配置调整，不属于 `/feature global` 命令。

```sh
uv run --frozen aling
uv run --frozen aling --help
uv run --frozen aling --version
# 兼容旧部署脚本：uv run --frozen ryxbot
# 不使用 uv：python -m aling.app
```

缺少 OneBot 令牌时程序退出并只提示错误配置项；模型配置不完整时 bot 仍可运行，
但超级用户无法开启 AI 功能。
启动只监听本机，不主动登录或连接 QQ。环境变量优先于 `.env`；改配置后重启。
`GET http://127.0.0.1:8080/health` 检查进程存活；`GET http://127.0.0.1:8080/ready`
只有 OneBot 已连接时返回就绪。Compose 只用 `/health`，NapCat 不必等待 QQ 登录成功才启动。

不使用 uv 时，运行 `python3.12 -m venv .venv` 并激活环境，再运行
`python -m pip install -e '.[dev]'`。此方式按版本范围解析依赖；uv 使用提交的锁文件。

## 阿零管理面板

面板默认关闭。启用时，使用独立于 OneBot、NapCat 和模型 API 密钥的随机登录密钥，并将
`WEBUI_OWNER_ID` 设为已经列在 `SUPERUSERS` 中的部署者 QQ ID：

```sh
uv run python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

```dotenv
WEBUI_ENABLED=true
WEBUI_ADMIN_TOKEN=粘贴上一步生成的值
WEBUI_OWNER_ID=12345678
WEBUI_ORIGIN=http://127.0.0.1:8080
```

本机访问使用默认 loopback Origin。远程访问必须通过 HTTPS 反向代理，并把
`WEBUI_ORIGIN` 设置为浏览器实际访问的 HTTPS 来源（含非默认端口时也要包含端口）；不要把
面板直接暴露在未经保护的 HTTP 网络上。登录密钥至少 32 个随机字节，配置格式长度检查不能
代替安全生成。

Docker 部署可以在原有 Compose 命令中加入 `-f compose.webui.yaml`。该覆盖文件会启用面板，并将
`./data/memes` 从只读切换为可写，以允许上传；不改动 `ryxbot` 项目名、`bot-data` 数据卷或
`/app/data/ryxbot.db` 路径。若素材保持只读挂载，浏览、查找和预览仍可用，新增、编辑和移除会被拒绝。
运行命令示例：

```sh
docker compose -f compose.yaml -f compose.webui.yaml up --build -d bot
```

首版只允许固定的单个超级用户登录。模型和人格配置编辑、多部署者账号、插件安装、聊天记录、
群处罚操作、远程部署、进程重启和 QQ 登录均未实现；面板不提供这些入口。

## 手动接入 NapCatQQ

账号登录由你在决定接入时完成。按 [NapCat 官方说明](https://napneko.github.io/use/integration)
安装、登录并新建 **OneBot V11 WebSocket 客户端（反向 WebSocket）**：

1. 同机本地运行的 URL：`ws://127.0.0.1:8080/onebot/v11/ws`。
2. Token 填写与 `.env` 的 `ONEBOT_ACCESS_TOKEN` 完全相同的值。
3. 启用连接与群消息上报，消息格式选择 `array`。
4. 群里发送 `/ping`、`/help`；配置 AI 后发送 `@阿零 你好`。

这是 [NoneBot 官方适配器推荐的方式](https://onebot.adapters.nonebot.dev/docs/guide/setup/)。
昵称、普通回复机器人（包括回复自动附带的 @）、@ 他人、@ 全体、普通群聊和私聊
均不会触发 AI；回复中另行添加独立 @ 可以触发。
`@阿零 /ping` 仍由指令插件处理。AI 不读取回复消息的历史正文或图片内容。

默认 `ALLOWED_GROUPS=[]` 表示所有群可用；实际接入时建议配置目标群 ID，例如
`ALLOWED_GROUPS=[123456789]`。`SUPERUSERS=[12345678]` 填维护者 QQ ID；该用户可在群内
管理功能，也可用 `/feature global ...` 调整运行时全局开关。更新 `.env` 后须重启，运行时开关无需重启。
允许列表对群指令、AI、功能开关、群管理和主动消息均生效。QQ ID 数组须用 JSON 格式。

## Docker Compose

需要运行中的 Docker Engine 与 Compose v2。复制并编辑 `.env` 后：

```sh
# 只构建并启动 bot，不启动 NapCat、不登录账号
# 镜像名称为 aling；兼容沿用项目名 ryxbot 以保留既有命名数据卷
docker compose up --build -d bot
docker compose logs -f bot
```

在你确认接入账号后，手动开启可选 NapCat 服务：

```sh
docker compose --profile qq up --build -d
```

访问本机 [NapCat WebUI](http://127.0.0.1:6099/webui)，按容器日志提供的方式进入，
更改 WebUI Token 后自行登录。在 WebUI 配置反向 WS：
`ws://bot:8080/onebot/v11/ws`，Token 与 bot 一致。宿主机上的独立 NapCat
连接容器 bot 时使用 `ws://127.0.0.1:8080/onebot/v11/ws`。
容器访问宿主机模型服务需使用 Docker 支持的宿主机地址，不能用容器的 `127.0.0.1`。

bot 与 WebUI 的宿主机端口只绑定本机。远程接入需明确调整监听、访问控制和 TLS。
bot 使用非 root 用户、只读文件系统，SQLite 位于可写数据卷；NapCat 配置和账号
资料在单独的数据卷内。`docker compose down` 保留数据卷，`down -v` 会删除它们。
NapCat 镜像来自 [官方容器项目](https://github.com/NapNeko/NapCat-Docker)，默认使用其
示例的 `latest`；正式运行前通过 `NAPCAT_IMAGE` 固定你验证过的版本或 digest。

## 工程结构与扩展

```text
src/aling/
  app.py               # 驱动、OneBot 注册、插件加载、生命周期
  runtime.py           # 共享服务与依赖
  core/
    config.py          # 环境配置和校验
    events.py          # 明确 @ 的原始消息检查
    permissions.py     # 群管理权限，与 AI 独立
    rate_limit.py      # 冷却与滑动窗口频率
    storage.py         # SQLite 全局/群开关、定时配置与发送声明
    extensions.py      # 表情包、网页、群操作扩展协议
    logging.py         # 无聊天正文、凭据和异常局部变量的日志
  services/
    ai.py              # OpenAI 兼容客户端、聊天、有限工具调用
    web.py             # SSRF 防护、正文提取与可选摘要
    memes.py           # 本地清单检索、图片验证
    group_moderation.py # 独立群管理权限校验
    scheduler.py       # 可选定时消息、安静时段与限频
    sessions.py        # 进程内短期会话，无持久化
    proactive.py       # 主动消息双开关与限频入口
  plugins/
    commands.py        # /help /ping /status /feature /proactive
    ai_chat.py         # QQ 群事件与 AI 服务间的适配
    web_read.py        # /read
    meme.py            # /meme
    group_moderation.py # /mute /unmute
  prompts/default.txt  # 随包发布的默认人格
tests/                 # 离线测试与 OneBot 模拟连接
```

设计见 [架构说明](docs/architecture.md)，插件配置和离线测试范例见
[插件开发指南](docs/plugins/README.md)。添加普通 NoneBot 插件后，在
`EXTRA_PLUGINS=["你的包.插件模块"]` 中显式加载；安装的插件属于受信任代码。
插件使用 `get_runtime()` 获取设置与服务，QQ matcher 保持薄层。

- 表情包素材清单和文件只放在 `data/memes/`，遵循自制或明确许可再分发原则。
  插件以不透明 ID 读取，不接受用户提供路径；图片通过 base64 消息段发送。
- 网页读取只允许 HTTP(S) 标准端口和全部 DNS 地址均为公网的目标，固定目标 IP、
  保留 HTTPS 主机名校验，不跟随重定向、不使用环境代理/Cookie，不接收压缩响应，
  限制响应大小和总时长。页面正文是模型的不可信资料。
- 群管理只接受显式 @ 单个成员。操作者和机器人都必须是 QQ 群主/管理员，目标必须是普通成员；
  配置超级用户不能绕过 QQ 角色验证。AI 没有群管理接口。
- 定时消息通过 `/proactive schedule` 明确配置；全局 `PROACTIVE_ENABLED=true`、
  本群 `/proactive on`、在线 OneBot、安静时段、每日/每小时限制都必须满足。
  每群每天仅执行一次；删除、重建定时消息或重启不会清除当日发送声明。
  错过时窗或发送失败不会重试。机器人只发管理员指定的文本，不采集群聊来生成插话。

限频器为单进程内存状态，重启清空；本阶段仅部署一个 bot 进程。群开关持久化。
全局开关关闭即禁止主动发送，群开关关闭与发送通过同一锁保持顺序。

旧数据兼容说明：数据库默认仍在 `data/ryxbot.db`，Compose 项目名固定为 `ryxbot`，
保留此前的 `bot-data`、`napcat-qq`、`napcat-config` 数据卷名。原 `ryxbot` 命令、
根目录 `bot.py` 与 `RYXBOT_ENV_FILE` 留作兼容入口；新项目包和模块导入使用 `aling`。

## 隐私与安全默认值

密钥与账号资料不提交 Git，也不进入镜像构建上下文。应用日志输出固定事件和
错误类别；NoneBot 默认会包含群消息正文，因此框架日志被过滤，警告/错误只留
事件提示；即使 DEBUG 级别也不输出框架正文和异常局部变量。
排查外部插件时需在受控环境进行，不应给生产日志增加聊天正文。

本轮明确 @ 的文字和人格提示会发送给配置的模型服务。启用短期记忆并通过群级确认后，
后续请求还会带上同一用户近期的明确 @ 提问与阿零回复；这些内容仅保存在进程内，
不会写日志、SQLite 或向量库。模型服务商可能独立保存请求，启用前应告知群成员。
网页摘要和 AI 网页工具会把有限网页正文发给模型。模型只能调用网页读取、表情包检索白名单，
不能运行系统命令、修改配置或操作群管理。HTTP 请求、
DNS/整体任务和 OneBot API 等待均有超时。额外第三方插件需要遵守同样约束。

## 测试与构建

```sh
uv sync --frozen --extra dev
uv run --frozen --extra dev pytest
uv run --frozen --extra dev ruff check .
uv run --frozen --extra dev ruff format --check .
uv build
RYXBOT_ENV_FILE=.env.example docker compose --env-file .env.example --profile qq config --quiet
docker build -t aling:local .
uv run --frozen aling doctor
```

测试使用模拟 HTTP 传输与带令牌的本地 ASGI WebSocket，不接入真实 QQ、模型或网页。
GitHub Actions 执行测试、代码检查、包构建、Compose 配置验证和容器镜像构建。
本次验证结果与环境限制见 [验证记录](docs/verification.md)。

## 升级与数据兼容

`data/ryxbot.db`、Compose 项目名 `ryxbot` 和既有 `bot-data`、`napcat-qq`、
`napcat-config` 数据卷保持兼容。更新前停止 bot 并备份数据库及 Docker 卷；首次启动会
以事务方式执行可重复的 SQLite 追加迁移，不会自动删除或重建数据库。需要回滚时停止新版本，
将备份还原到原路径后再运行旧版本。数据库新版本写入 `schema_version`；新版本打开过的库
不保证可由旧程序直接读取。旧 `ryxbot` CLI、`bot.py` 与 `RYXBOT_ENV_FILE` 继续作为兼容入口。

首次升级建议先执行 `uv run --frozen aling doctor`，再启动应用检查 `/status`。
`/health` 只代表进程存活，不代表 OneBot 已连接；真实 QQ 群功能与群管理仍需维护者
在测试群手动验证。请勿在共享工作树中提交真实 `.env`、数据库、NapCat 登录资料或私人表情包。

## 贡献与安全报告

开始前请阅读 [贡献指南](CONTRIBUTING.md)。漏洞报告渠道与支持版本规则见
[SECURITY.md](SECURITY.md)；目前没有配置经验证的私密报告邮箱。
