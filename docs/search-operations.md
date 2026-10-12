# 受控联网搜索运维

联网搜索是可选能力。默认 `search` 和 `online_meme` 全局/群开关均关闭；默认 `/meme` 只用本地素材。启用前，部署者需要确认查询会交给自托管 SearXNG 及其上游引擎，当前请求文字会交给已配置模型作用途分类，AI 回答工具可能再收到获准搜索结果或网页正文。分类与回答会消耗已有模型额度并可能产生费用；自托管搜索免单独搜索 API Key，不代表零成本或查询留在本机。

## 组件与搜索配置

可选服务位于 Compose `search` profile，镜像固定为 `searxng/searxng:2026.9.18-0d6910ae5`。该版本 tag 避免跟随 `latest` 漂移，但不是 digest 锁定；升级时应先核对上游发布和镜像摘要，再改成经验证的版本或摘要。当前配置保留 DuckDuckGo、Wikipedia 和 DuckDuckGo Images，设置 SafeSearch 为 2，并启用 JSON 格式。具体引擎能否访问、支持的类别和 SafeSearch 效果取决于上游、地区和引擎状态；没有结果时不会回退到公共搜索服务。

复制示例环境和 SearXNG 设置文件，并生成独立随机密钥：

```sh
cp .env.example .env
cp deploy/searxng/settings.yml.example deploy/searxng/settings.yml
uv run python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

把生成值写入 `.env` 的 `SEARXNG_SECRET`。示例中的 `CHANGE_ME...` 仅供 Compose 配置检查，不能用于启动服务。真实 `.env` 和 `deploy/searxng/settings.yml` 不应提交。搜索 origin 默认是固定的 `http://search:8080`；除非按受控部署重新配置固定服务 origin，不要填入用户或模型可修改的 URL。客户端固定访问 `/search`，最多 10 秒、1 MB 默认响应上限（可配置上限 5 MB）、最多 2 个连接（上限 4），只用 JSON POST，不跟随重定向或读取代理环境变量。

先验证配置，然后仅在需要时启动 bot 和 SearXNG：

```sh
RYXBOT_ENV_FILE=.env docker compose --env-file .env --profile qq config --quiet
RYXBOT_ENV_FILE=.env docker compose --env-file .env -f compose.yaml -f compose.search.yaml --profile qq --profile search config --quiet
RYXBOT_ENV_FILE=.env docker compose --env-file .env -f compose.yaml -f compose.search.yaml --profile qq --profile search up --build -d bot search
```

`search` 没有宿主机发布端口，只能由 Compose 网络内的 bot 访问。bot 仍需按现有部署连接 NapCat 并访问模型服务、获准网页和图片来源；不要为了搜索改动已有 Compose 项目名 `ryxbot`、`bot-data`、`napcat-qq`、`napcat-config` 卷或 `/app/data/ryxbot.db` 数据路径。使用不带搜索 overlay 的基础 profile 时不会启动 SearXNG。

## 授权与来源

完成管理面板一次性数据流确认后，部署者在“联网设置”中选择用途、来源与额度，再启用全局 `search` 或 `online_meme`。在线媒体来源默认为空。技术来源应由维护者逐个核对主机名、HTTPS、路径前缀及页面用途；共享托管平台不能仅凭平台域名视为所有项目均可信。在线图片要分别批准结果页面来源和图片/CDN 来源。群管理员只能在部署者设置的上限内缩小用途、来源、额度或选择 `local` / `auto` / `online` 模式。

`/search` 与在线表情均依赖 AI 功能及模型凭据作保守用途分类。只开放已实现的用途：默认可由部署者允许 `technical` 与 `ordinary_meme`，游戏、兴趣和日常实用用途需另外开放；不确定、混合或禁止用途会拒绝。搜索最多返回三条实际通过来源验证的结果，搜索摘要不代表读取了正文。

`/meme <关键词>` 默认为 `local`，不联网；`--mode auto` 先查本地，只有本地无结果才申请在线操作；`--mode online` 明确尝试在线查找，必须通过用途和图片/页面来源策略。在线只支持有限大小的 PNG、JPEG、静态 WebP；不支持 GIF、动态图片或把远端 URL 交给 NapCat 下载。在线临时图片只保存在进程内，最多 20 项/50 MB，五分钟内过期，策略撤销或重启会使其失效。来源许可、SafeSearch 和用途分类都不能证明内容或图片绝对安全；需要确定内容范围时使用人工审核来源或本地素材。

## 数据流、费用与日志

- 为用途分类，当前用户请求的必要文字会发送给配置的模型服务；分类请求不附聊天历史，也不附工具。
- 获准后，规范化查询会通过 POST 发给 SearXNG。SearXNG 将请求分发给当前设置启用且可用的上游引擎；自托管不隐藏这段外部数据流。
- AI 自动搜索时，获准标题、摘要、链接可能再交给模型组织回答；仅在允许网页读取的任务里，经过限制的页面正文也会交给模型。直接 `/search` 不会把正文伪装成已读取内容。
- 用途分类和 AI 回答使用既有模型调用限额；请按模型供应商的计费、留存和隐私政策评估成本与数据处理。
- 应用只记录固定错误类别/异常类型，不记录原始查询、URL、正文或图片路径。SearXNG 模板关闭 Granian access log，查询使用 POST 以避免进入 URL 请求行；这不保证所有容器/代理/基础设施日志都不留痕。未在本任务启动 SearXNG 容器实测日志，请在自己的部署中核查日志收集链路并限制访问与保留时间。

## 状态排查

在面板“联网设置”查看状态，或使用受控探测。探测采用固定的无敏感测试文本，并仍经过当前策略和预算，不会向 QQ 发送消息。

| 状态 | 含义与处理 |
| --- | --- |
| 配置 | 固定搜索 origin 已配置；这本身不表示服务可用。 |
| 内部服务未知 | 尚未探测，或请求在外部搜索前被额度/预算拦截。 |
| 内部服务不可达 | 检查 `search` profile、容器健康检查、bot 与 search 是否在同一 Compose 网络以及 SearXNG 日志。 |
| JSON 未启用 | 通常对应 403；检查 `settings.yml` 的 `search.formats` 是否含 `json`，然后重启 `search`。 |
| JSON 已启用但上游失败 | 检查所启用引擎在部署地区是否可用、上游返回格式和 SearXNG 日志；不会切换公共实例。 |
| 有效但无结果 | 调整关键词或核查类别/上游可用性；不会将空结果声称为成功命中。 |
| 来源不允许/用途拒绝 | 按来源与用途策略逐项核对；不要为消除错误而开放整个互联网。 |

诊断仅显示安全状态字段，不暴露探测文本、查询结果或 URL。SearXNG 自身和 Docker 的错误日志应当按敏感运维数据处理。

## 停用、备份与回滚

停用在线能力时，先在面板关闭全局 `search` 与 `online_meme`，确认状态保存成功；群级开关也可进一步关闭。策略停用会取消在途操作并使临时媒体失效。若也要停掉 SearXNG，再执行：

```sh
RYXBOT_ENV_FILE=.env docker compose --env-file .env -f compose.yaml -f compose.search.yaml --profile qq --profile search stop search
```

不要使用 `down -v`，它会删除命名数据卷。容器部署可以用 SQLite 在线备份 API 将一致性快照写入同一个持久数据卷：

```sh
RYXBOT_ENV_FILE=.env docker compose --env-file .env exec -T bot python -c 'import sqlite3; source=sqlite3.connect("/app/data/ryxbot.db"); target=sqlite3.connect("/app/data/ryxbot.db.before-v05"); source.backup(target); target.close(); source.close()'
```

在升级应用前完成备份，并另行备份需保留的 NapCat 配置卷。回滚应用版本时，SQLite schema 4 的新数据库不保证旧版本能读；先停 bot，再把升级前的快照还原到原数据库路径：

```sh
RYXBOT_ENV_FILE=.env docker compose --env-file .env stop bot
RYXBOT_ENV_FILE=.env docker compose --env-file .env run --rm --no-deps --entrypoint python bot -c 'import shutil; shutil.copy2("/app/data/ryxbot.db.before-v05", "/app/data/ryxbot.db")'
```

搜索策略保存在 SQLite；SearXNG 缓存卷可重建，实际 `.env` 与 SearXNG 设置文件应另行安全备份或重新生成。基础 Compose 不带 `search` overlay 即可不启动搜索服务，已启用的数据库功能开关仍需由管理员显式关闭。

## 验收范围

离线验收只使用模拟模型、搜索、网页、OneBot 与临时 SQLite。Compose `config` 验证不启动容器；实际引擎质量、SearXNG 部署日志、真实图片来源、模型供应商留存/费用和 QQ 消息送达仍需部署者在获准环境中单独验证。不要将本地配置成功或模拟测试通过解释为线上服务已经就绪。
