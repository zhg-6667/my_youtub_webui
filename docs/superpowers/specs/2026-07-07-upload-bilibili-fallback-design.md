# upload_bilibili 目录输入视频回退设计

日期：2026-07-07

## 背景

`scripts/upload_bilibili.py` 支持传入任务目录或直接传入 `.mp4` 文件。当前传入任务目录时固定使用 `media/video_final_trimmed.mp4`。当该文件不存在但 `media/video_final.mp4` 已生成时，脚本会报错，导致需要用户手动传入视频文件路径。

## 目标

只在用户传入任务目录时增加回退逻辑：

1. 优先使用 `media/video_final_trimmed.mp4`。
2. 如果首选文件不存在，则使用 `media/video_final.mp4`。
3. 如果两个文件都不存在，沿用现有报错流程。

## 非目标

- 不改变直接传入 `.mp4` 文件路径时的行为。
- 不新增命令行参数。
- 不改变上传参数、封面、标题、简介、cookie 或 biliup 调用逻辑。

## 设计

在 `scripts/upload_bilibili.py` 中保持 `VIDEO_NAME = "video_final_trimmed.mp4"` 作为首选文件名，并新增 `FALLBACK_VIDEO_NAME = "video_final.mp4"`。

修改 `resolve_paths(raw: str)` 的目录分支：

1. 构造首选路径：`task_dir / "media" / VIDEO_NAME`。
2. 如果首选路径存在，返回 `(task_dir, preferred_video)`。
3. 构造回退路径：`task_dir / "media" / FALLBACK_VIDEO_NAME`。
4. 如果回退路径存在，返回 `(task_dir, fallback_video)`。
5. 如果两者都不存在，返回 `(task_dir, preferred_video)`，让 `main()` 中现有的 `if not video.exists()` 继续输出错误。

文件输入分支保持不变：只有传入路径本身存在且是 `.mp4` 时才接受，不根据文件名自动回退。

## 测试与验证

用临时目录直接调用 `resolve_paths()` 验证三种情况：

1. `media/video_final_trimmed.mp4` 存在时返回首选文件。
2. 首选文件不存在但 `media/video_final.mp4` 存在时返回回退文件。
3. 两个文件都不存在时返回首选文件，后续主流程仍会报找不到首选文件。

这个改动很小，不需要启动 Web 或后端服务。