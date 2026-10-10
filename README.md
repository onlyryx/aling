# ryxbot

onlyryx 旗下独立开源 QQ 群机器人，功能插件为主、AI 聊天为辅。

第一阶段提供基础指令、群内明确 @ 触发的单轮 AI 聊天、隐私日志及插件扩展入口。
使用 Python 3.12+、NoneBot2、OneBot V11、NapCatQQ、SQLite、httpx 与 BeautifulSoup。
许可证：MIT。

## 当前功能

- `/help`：帮助；`/ping`：回复 `pong`。群聊和私聊均可使用。
- 群内 `@机器人 你的问题`：可配置的 OpenAI 兼容 Chat Completions API。
- `/proactive on|off`：仅群主、群管理员或配置的超级用户可调整本群开关。
- SQLite 仅持久化群开关；AI 无群聊历史，不收集普通聊天。
- 群允许列表、AI 冷却/小时限频/全局并发限制、统一主动发言限制。
- 表情包搜索、网页解析、群管理扩展协议与受保护的网页读取服务。

第一阶段没有自动插话、表情包供应商、封禁指令、多轮记忆或 AI 工具执行。
群友人格由可配置的提示词提供，后续按具体功能逐步扩展。

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

只使用指令时保留 `AI_ENABLED=false`。启用聊天还需设置：

```dotenv
AI_ENABLED=true
AI_API_KEY=你自己的模型服务密钥
AI_BASE_URL=https://你的模型服务地址/v1
AI_MODEL=服务商支持的模型标识
```

上面的值是说明用示例，不是可用凭据。`AI_BASE_URL` 是完整 API 基础地址，客户端
会追加 `/chat/completions`；已包含该路径会导致路径重复。兼容接口需支持
`messages`、`max_tokens`、`stream=false`，并返回 `choices[0].message.content` 文本。
部署者可配置本地 HTTP 模型服务，群成员与模型输出不能修改服务地址。
客户端请求未压缩响应，拒绝忽略 `Accept-Encoding: identity` 而强制返回压缩内容的服务。

```sh
uv run --frozen ryxbot
# 或 uv run --frozen python bot.py
```

缺少令牌或启用了 AI 却缺少模型配置时，程序退出并只提示错误配置项。
启动只监听本机，不主动登录或连接 QQ。环境变量优先于 `.env`；改配置后重启。
`GET http://127.0.0.1:8080/health` 返回应用健康状态，**不代表已连接 QQ**。

不使用 uv 时，运行 `python3.12 -m venv .venv` 并激活环境，再运行
`python -m pip install -e '.[dev]'`。此方式按版本范围解析依赖；uv 使用提交的锁文件。

## 手动接入 NapCatQQ

账号登录由你在决定接入时完成。按 [NapCat 官方说明](https://napneko.github.io/use/integration)
安装、登录并新建 **OneBot V11 WebSocket 客户端（反向 WebSocket）**：

1. 同机本地运行的 URL：`ws://127.0.0.1:8080/onebot/v11/ws`。
2. Token 填写与 `.env` 的 `ONEBOT_ACCESS_TOKEN` 完全相同的值。
3. 启用连接与群消息上报，消息格式选择 `array`。
4. 群里发送 `/ping`、`/help`；配置 AI 后发送 `@机器人 你好`。

这是 [NoneBot 官方适配器推荐的方式](https://onebot.adapters.nonebot.dev/docs/guide/setup/)。
昵称、普通回复机器人（包括回复自动附带的 @）、@ 他人、@ 全体、普通群聊和私聊
均不会触发 AI；回复中另行添加独立 @ 可以触发。
`@机器人 /ping` 仍由指令插件处理。AI 不读取回复消息的历史正文或图片内容。

默认 `ALLOWED_GROUPS=[]` 表示所有群可用；实际接入时建议配置目标群 ID，例如
`ALLOWED_GROUPS=[123456789]`。`SUPERUSERS=[12345678]` 填维护者 QQ ID。
允许列表对群指令、AI、群开关和未来主动消息均生效。QQ ID 数组须用 JSON 格式。

## Docker Compose

需要运行中的 Docker Engine 与 Compose v2。复制并编辑 `.env` 后：

```sh
# 只构建并启动 bot，不启动 NapCat、不登录账号
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
src/ryxbot/
  app.py               # 驱动、OneBot 注册、插件加载、生命周期
  runtime.py           # 共享服务与依赖
  core/
    config.py          # 环境配置和校验
    events.py          # 明确 @ 的原始消息检查
    permissions.py     # 群管理权限，与 AI 独立
    rate_limit.py      # 冷却与滑动窗口频率
    storage.py         # SQLite 群开关
    extensions.py      # 表情包、网页、群操作扩展协议
    logging.py         # 无聊天正文、凭据和异常局部变量的日志
  services/
    ai.py              # OpenAI 兼容客户端和聊天策略
    http.py            # 响应大小限制
    web.py             # SSRF 防护与基础网页文本提取
    proactive.py       # 主动消息双开关、权限、限频入口
  plugins/
    commands.py        # /help /ping /proactive
    ai_chat.py         # QQ 群事件与 AI 服务间的适配
tests/                 # 离线测试与 OneBot 模拟连接
```

设计见 [架构说明](docs/architecture.md)。添加普通 NoneBot 插件后，在
`EXTRA_PLUGINS=["你的包.插件模块"]` 中显式加载；安装的插件属于受信任代码。
插件使用 `get_runtime()` 获取设置与服务，QQ matcher 保持薄层。

- 表情包插件实现 `MemeProvider.search()`，返回结构化结果；下载 URL 必须再验证。
  现有网页客户端仅允许文本内容，未来图片下载须另设 MIME 与大小限制。
- 网页插件调用 `runtime.web.parse(url)` 得到 `WebPage`；当前没有面向群成员的
  网页命令。入口只允许 HTTP(S) 标准端口和全部 DNS 地址均为公网的目标，固定目标
  IP 并保留 HTTPS 主机名校验，不跟随重定向、不使用环境代理或 Cookie，不接受
  压缩响应，限制响应大小和总时长。重定向站点需提供最终 URL。
- 群管理插件必须由真实 OneBot 事件构造 `PermissionContext`，先调用
  `require_group_admin()`，再确认机器人自己的 QQ 操作权限。不能信任聊天中自称
  管理员、模型输出或工具参数提供的角色。
- 主动消息必须调用 `runtime.proactive.send(group_id, text, send_text)`；回调只负责
  用 `MessageSegment.text(text)` 调用 OneBot 发送 API。全局 `PROACTIVE_ENABLED=true`
  与本群 `/proactive on` 必须同时满足。默认冷却 300 秒、每小时最多 3 次；
  调度器留给后续功能。普通 @ 回复使用独立 AI 限频。

限频器为单进程内存状态，重启清空；本阶段仅部署一个 bot 进程。群开关持久化。
全局开关关闭即禁止主动发送，群开关关闭与发送通过同一锁保持顺序。

## 隐私与安全默认值

密钥与账号资料不提交 Git，也不进入镜像构建上下文。应用日志输出固定事件和
错误类别；NoneBot 默认会包含群消息正文，因此框架日志被过滤，警告/错误只留
事件提示；即使 DEBUG 级别也不输出框架正文和异常局部变量。
排查外部插件时需在受控环境进行，不应给生产日志增加聊天正文。

仅本次被 @ 的文本和人格提示会发送给配置的模型服务。模型服务商可能独立保存
这些请求，接入前应按自己的需要选择服务和告知群成员。本项目不存储它们。
模型无工具执行器，不能运行系统命令、修改配置或操作群管理。HTTP 请求、
DNS/整体任务和 OneBot API 等待均有超时。额外第三方插件需要遵守同样约束。

## 测试与构建

```sh
uv sync --frozen --extra dev
uv run --frozen --extra dev pytest
uv run --frozen --extra dev ruff check .
uv run --frozen --extra dev ruff format --check .
uv build
RYXBOT_ENV_FILE=.env.example docker compose --env-file .env.example --profile qq config --quiet
docker build -t ryxbot:local .
```

测试使用模拟 HTTP 传输与带令牌的本地 ASGI WebSocket，不接入真实 QQ、模型或网页。
GitHub Actions 执行测试、代码检查、包构建、Compose 配置验证和容器镜像构建。
本次验证结果与环境限制见 [验证记录](docs/verification.md)。
