# 参与贡献

感谢你帮助改进阿零。当前项目由 Jack233_ / onlyryx 维护，欢迎先通过 GitHub Issues
讨论缺陷、兼容性和功能建议，再提交小而集中的 Pull Request。

## 本地开发

需要 Python 3.12+ 和 uv：

```sh
uv sync --frozen --extra dev
uv run --frozen --extra dev pytest -q
uv run --frozen --extra dev ruff check .
uv run --frozen --extra dev ruff format --check .
uv build
```

测试必须离线；使用临时 SQLite、mock HTTP/DNS 和 fake OneBot。不要让测试加载个人 `.env`、
真实 SQLite、QQ 凭据或模型密钥。没有维护者授权时，不登录账号、发送真实群消息、部署或推送。

## 插件原则

优先使用 `docs/plugins/example_plugin.py` 展示的原生 NoneBot matcher 和 `PluginMetadata`。
新能力应有默认关闭的全局/群级边界（适用时）、可信权限上下文、冷却/频率上限、外部请求超时、
隐私安全日志和离线拒绝路径测试。不要收集普通群聊内容；不要让模型执行任意命令、代码或群管理。

涉及 SQLite 的修改需使用显式列名、事务和兼容旧数据的迁移测试。素材只提交自制或明确许可的文件，
并记录来源与再分发条件；维护者的个人表情包、账号数据和密钥不得进入仓库。

## 提交 PR

说明用户可见变化、风险边界、验证命令和尚未验证的环境。功能变化需更新 README、`.env.example`
或架构文档。请勿在 PR 中附真实 token、群聊记录、QQ 截图或数据库副本。
