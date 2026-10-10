# 本地表情包素材

阿零的 `/meme <关键词>` 只检索本地素材，不会联网搜索或下载图片。素材默认放在
`data/memes/`，Docker Compose 会以只读方式挂载到 bot 容器的 `/app/memes`；NapCat
无需共享这个目录，图片会通过 OneBot 的 base64 图片段发送。

在该目录创建 `manifest.json`，每项包含唯一资源 ID、简短描述、标签和相对路径：

```json
[
  {
    "id": "shrug-01",
    "description": "耸肩",
    "tags": ["无语", "不知道", "耸肩"],
    "path": "shrug.png"
  }
]
```

只添加自己制作或明确允许再分发的素材，并保留来源和许可信息。清单上限为 1 MB、
5000 项；图片只接受 PNG、JPEG、静态 WebP 或静态 GIF，单张最大 5 MB、最多 1200
万像素。解析到素材目录之外的路径（包括符号链接）都会被拒绝。仓库不提供默认图片素材。

首次使用前由超级用户运行 `/feature global meme on`，再由群主或管理员在目标群运行
`/feature meme on`。启用后无需重启 bot；群级开关不能覆盖超级用户关闭的全局开关。
