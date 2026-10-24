# 批量获取 YouTube 作者最新视频

打开“监控配置”，在顶部的“批量获取 YouTube 作者最新视频”中使用：

1. 输入作者主页，每行一个，最多 20 位作者；也支持空格、中文或英文逗号分隔。
2. 设置每位作者每种类型的最新数量 X，范围 1–100，默认 5。
3. 点击“获取视频”。每位作者分别显示最新 X 个普通视频和 X 个 Shorts；不足 X 个时显示实际数量。
4. 结果以小封面（桌面 96×54 像素）、标题、发布时间、时长展示，可打开原视频、复制全部链接或记录单条链接。
5. 可逐条勾选、全选作者某个分类，或全选所有未创建任务的视频，选择自动／手动模式后点击“批量创建任务”。已有任务自动跳过，提供查看任务入口；失败项保留勾选并显示原因，支持重试。新任务进入现有处理队列依次执行。自动发布到 B 站默认关闭。

示例输入：

```text
https://www.youtube.com/@pokedexfillers
https://www.youtube.com/@WolfeyVGC
```

支持 `@handle`、`channel/UC…`、`c/…`、`user/…` 主页，也可粘贴作者的 `/videos` 或 `/shorts` 链接。相同主页及其分类链接会合并查询。视频和 Shorts 分别按 YouTube 分类页默认的最新顺序展示，不混合排序，不包含直播分类。

查询复用设置中的 YouTube 代理与 Cookie。每次最多并行查询 3 位作者；某位作者或某个分类失败时，保留其他成功结果，并在对应位置显示错误。不存在的分类显示为空。

查询操作是一次性读取，不会自动创建频道监控或处理任务。勾选后点击“批量创建任务”才会创建处理任务。

发布时间在列表出现后按可见视频分批补齐，优先读取缓存和 RSS，缺失时获取单条视频详情；查询日期不会下载视频。具体时间显示为北京时间；仅提供日期时直接显示日期，无法获取时显示“暂不可用”，不使用推算时间。日期写入本地数据库缓存，失败查询短暂缓存 10 分钟。封面通过固定 YouTube 图片地址由后端代理获取，缓存 24 小时；图片加载失败时显示占位图。监控配置页随标签页内容区铺满宽度。

接口：`POST /api/youtube-authors/lookup`

```json
{
  "channel_urls": [
    "https://www.youtube.com/@pokedexfillers",
    "https://www.youtube.com/@WolfeyVGC"
  ],
  "latest_count": 5
}
```

返回 `channels` 数组，每项包含 `input_url`、`channel_url`、`channel_id`、`title`、`videos`、`shorts` 和 `errors`。视频项包含 `video_id`、`title`、`url`、`duration_seconds`、`thumbnail_url`、`published_at`、`published_date`、`metadata_checked`、`task_id` 和 `task_status`；分类不存在时数组为空，获取失败时在 `errors` 中返回对应的 `channel`、`videos` 或 `shorts` 错误。

补充接口：

- `POST /api/youtube-videos/metadata`：`items` 最多 12 条，每条包含 `video_id` 和可选的 `channel_id`；返回 `videos`，包含发布时间及是否已查询。
- `GET /api/youtube-videos/{video_id}/thumbnail`：获取经过后端代理并缓存的小封面。
- `POST /api/tasks/batch`：传入 `urls`（1–100 条）、`execution_mode` 和 `auto_bilibili_upload`。返回逐条 `results` 及 `created`、`existing`、`failed` 统计。前端超过 100 条选择时分批提交；重复提交不会重复入队。
