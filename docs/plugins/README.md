# 编写 NoneBot 插件

`example_plugin.py` 是可复制修改的最小示例，包含 `PluginMetadata`、独立插件配置、
部署者开关与群功能门、QQ 管理员权限、按用户冷却和每小时限频、参数长度限制，以及
不记录命令正文的隐私约定。设置：

从源码仓库根目录运行时，可直接启用该示例；若通过 wheel 安装阿零，应先将示例复制到
自己的可导入插件包中，再把该包名填入 `EXTRA_PLUGINS`。

```dotenv
EXTRA_PLUGINS=["docs.plugins.example_plugin"]
ALING_EXAMPLE_ENABLED=true
ALING_EXAMPLE_COOLDOWN_SECONDS=3
ALING_EXAMPLE_MAX_PER_HOUR=10
```

示例插件无需部署者全局授权。使用项目内网页功能时，先由超级用户运行 `/feature global web on`，
再由群管理员在允许的群内运行 `/feature web on`。示例不调用外部服务；接入真实
服务时，在服务层增加输入校验、超时和失败处理，并只将固定事件类别写入日志。

离线验证示例设置见 `tests/test_plugin_example.py`；`tests/test_core.py` 覆盖项目共用的
冷却与滑动窗口限频器。完整测试不会登录 QQ 或调用外部服务。
