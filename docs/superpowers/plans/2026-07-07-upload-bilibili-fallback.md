# upload_bilibili 目录输入视频回退 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `scripts/upload_bilibili.py` 在用户传入任务目录时，优先使用 `media/video_final_trimmed.mp4`，若不存在则自动回退到 `media/video_final.mp4`。

**Architecture:** 改动集中在 `resolve_paths(raw: str)` 的目录输入分支，保持文件输入分支与上传主流程不变。新增一个回退文件名常量，并用 pytest 对首选、回退、缺失三种目录输入场景做覆盖。

**Tech Stack:** Python 3.12、`argparse`、`pathlib.Path`、pytest。

## Global Constraints

- 只在用户传入任务目录时增加回退逻辑。
- 直接传入 `.mp4` 文件路径时行为保持不变。
- 不新增命令行参数。
- 不改变标题、简介、封面、cookie、biliup 查找或上传命令构造逻辑。
- 如果 `video_final_trimmed.mp4` 与 `video_final.mp4` 都不存在，继续沿用现有主流程报错。
- 本会话不执行 `git commit`，除非用户明确要求提交；若提交，提交信息必须包含规定的 `Co-Authored-By` 行。

---

## File Structure

- `scripts/upload_bilibili.py`
  - 负责解析用户输入路径、读取元数据、下载封面并调用 `biliup`。
  - 本计划只修改顶部说明、视频文件名常量和 `resolve_paths(raw: str)` 的目录分支。
- `backend/tests/test_upload_bilibili.py`
  - 新增 pytest 测试文件，专门覆盖 `scripts.upload_bilibili.resolve_paths()` 的目录输入视频选择逻辑。
  - 放在 `backend/tests/` 下是为了复用现有 `backend/tests/conftest.py` 中把项目根目录加入 `sys.path` 的测试设置。

---

### Task 1: 实现目录输入的视频文件 fallback

**Files:**
- Modify: `scripts/upload_bilibili.py:6-8`
- Modify: `scripts/upload_bilibili.py:27-35`
- Modify: `scripts/upload_bilibili.py:43-53`
- Create: `backend/tests/test_upload_bilibili.py`

**Interfaces:**
- Consumes: `resolve_paths(raw: str) -> tuple[Path, Path]` 当前公开函数签名。
- Produces: `resolve_paths(raw: str) -> tuple[Path, Path]` 签名不变；目录输入时新增选择规则：`video_final_trimmed.mp4` 存在则返回它，否则 `video_final.mp4` 存在则返回它，否则返回首选路径让主流程报错。

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_upload_bilibili.py`，内容如下：

```python
from __future__ import annotations

from pathlib import Path

from scripts.upload_bilibili import VIDEO_NAME, resolve_paths

FALLBACK_VIDEO_NAME = "video_final.mp4"


def _make_task_dir(tmp_path: Path) -> Path:
    task_dir = tmp_path / "task__abc123"
    (task_dir / "media").mkdir(parents=True)
    return task_dir


def test_resolve_paths_prefers_trimmed_video_for_task_directory(tmp_path: Path) -> None:
    task_dir = _make_task_dir(tmp_path)
    preferred = task_dir / "media" / VIDEO_NAME
    fallback = task_dir / "media" / FALLBACK_VIDEO_NAME
    fallback.write_bytes(b"fallback")
    preferred.write_bytes(b"preferred")

    resolved_task_dir, resolved_video = resolve_paths(str(task_dir))

    assert resolved_task_dir == task_dir.resolve()
    assert resolved_video == preferred.resolve()


def test_resolve_paths_uses_video_final_when_trimmed_missing(tmp_path: Path) -> None:
    task_dir = _make_task_dir(tmp_path)
    fallback = task_dir / "media" / FALLBACK_VIDEO_NAME
    fallback.write_bytes(b"fallback")

    resolved_task_dir, resolved_video = resolve_paths(str(task_dir))

    assert resolved_task_dir == task_dir.resolve()
    assert resolved_video == fallback.resolve()


def test_resolve_paths_keeps_preferred_path_when_no_directory_video_exists(tmp_path: Path) -> None:
    task_dir = _make_task_dir(tmp_path)
    preferred = task_dir / "media" / VIDEO_NAME

    resolved_task_dir, resolved_video = resolve_paths(str(task_dir))

    assert resolved_task_dir == task_dir.resolve()
    assert resolved_video == preferred.resolve()
    assert not resolved_video.exists()
```

- [ ] **Step 2: 运行测试确认失败**

运行：

```powershell
python -m pytest backend/tests/test_upload_bilibili.py -v
```

预期：测试失败，失败点是 `test_resolve_paths_uses_video_final_when_trimmed_missing`，因为当前实现仍返回 `media/video_final_trimmed.mp4`，而不是 `media/video_final.mp4`。典型断言差异应类似：

```text
E       AssertionError: assert WindowsPath('.../media/video_final_trimmed.mp4') == WindowsPath('.../media/video_final.mp4')
```

- [ ] **Step 3: 修改脚本顶部说明**

在 `scripts/upload_bilibili.py` 顶部 docstring 中，把当前关于目录推断的说明：

```python
位置参数既可以是任务目录（形如 ...__0n5Xs9XleLw），也可以直接是
media/video_final_trimmed.mp4，脚本会自动推断另一边。标题（--title）必须手动给中文。
```

替换为：

```python
位置参数既可以是任务目录（形如 ...__0n5Xs9XleLw），也可以直接是
media/video_final_trimmed.mp4。传任务目录时优先使用 trimmed 成片，缺失则回退到
media/video_final.mp4。标题（--title）必须手动给中文。
```

- [ ] **Step 4: 新增 fallback 常量**

在 `scripts/upload_bilibili.py` 的脚本固定约定区域，把当前常量块：

```python
# 脚本固定约定
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TAGS = "中配,狼叔,宝可梦"
DEFAULT_TID = 17  # 单机游戏
COPYRIGHT_SELF = 1  # 自制
VIDEO_NAME = "video_final_trimmed.mp4"
COVER_NAME = "cover.jpg"
THUMB_URLS = "https://img.youtube.com/vi/{vid}/{quality}.jpg"
THUMB_QUALITIES = ("maxresdefault", "hqdefault")
```

替换为：

```python
# 脚本固定约定
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TAGS = "中配,狼叔,宝可梦"
DEFAULT_TID = 17  # 单机游戏
COPYRIGHT_SELF = 1  # 自制
VIDEO_NAME = "video_final_trimmed.mp4"
FALLBACK_VIDEO_NAME = "video_final.mp4"
COVER_NAME = "cover.jpg"
THUMB_URLS = "https://img.youtube.com/vi/{vid}/{quality}.jpg"
THUMB_QUALITIES = ("maxresdefault", "hqdefault")
```

- [ ] **Step 5: 修改目录输入分支**

在 `scripts/upload_bilibili.py` 的 `resolve_paths(raw: str)` 中，把当前目录分支：

```python
    if p.is_dir():
        return p, p / "media" / VIDEO_NAME
```

替换为：

```python
    if p.is_dir():
        preferred = p / "media" / VIDEO_NAME
        if preferred.exists():
            return p, preferred
        fallback = p / "media" / FALLBACK_VIDEO_NAME
        if fallback.exists():
            return p, fallback
        return p, preferred
```

不要修改文件输入分支：

```python
    if p.is_file():
        if p.suffix.lower() != ".mp4":
            fail(f"传入的文件不是 mp4：{p}")
        # media/video_final_trimmed.mp4 -> 任务目录是 video.parent.parent
        return p.parent.parent, p
```

- [ ] **Step 6: 运行目标测试确认通过**

运行：

```powershell
python -m pytest backend/tests/test_upload_bilibili.py -v
```

预期：

```text
backend/tests/test_upload_bilibili.py::test_resolve_paths_prefers_trimmed_video_for_task_directory PASSED
backend/tests/test_upload_bilibili.py::test_resolve_paths_uses_video_final_when_trimmed_missing PASSED
backend/tests/test_upload_bilibili.py::test_resolve_paths_keeps_preferred_path_when_no_directory_video_exists PASSED
```

- [ ] **Step 7: 运行相关回归测试**

运行：

```powershell
python -m pytest backend/tests/test_upload_bilibili.py backend/tests/test_pipeline.py -v
```

预期：新增上传脚本路径解析测试通过，既有 pipeline 测试通过。若 `backend/tests/test_pipeline.py` 因本地环境缺依赖失败，记录完整失败输出，不要声称回归全部通过。

- [ ] **Step 8: 检查 diff**

运行：

```powershell
git diff -- scripts/upload_bilibili.py backend/tests/test_upload_bilibili.py
```

预期 diff 只包含：

```text
scripts/upload_bilibili.py
- 顶部说明增加目录输入 fallback 描述
- 新增 FALLBACK_VIDEO_NAME = "video_final.mp4"
- resolve_paths() 目录分支增加 preferred/fallback 选择

backend/tests/test_upload_bilibili.py
- 新增 3 个 resolve_paths() 目录输入测试
```

- [ ] **Step 9: 如用户明确要求提交，再提交**

只有用户明确要求提交时才运行：

```powershell
git add scripts/upload_bilibili.py backend/tests/test_upload_bilibili.py docs/superpowers/specs/2026-07-07-upload-bilibili-fallback-design.md docs/superpowers/plans/2026-07-07-upload-bilibili-fallback.md
git commit -m @'
fix: fall back to untrimmed bili upload video

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
'@
```

预期：git 创建一个新提交。若用户没有要求提交，保持未提交工作区改动。

---

## Self-Review

### 1. Spec coverage

- 优先使用 `media/video_final_trimmed.mp4`：Task 1 Step 5 实现，Step 1 第一个测试覆盖。
- trimmed 不存在时使用 `media/video_final.mp4`：Task 1 Step 5 实现，Step 1 第二个测试覆盖。
- 两者都不存在时沿用现有报错流程：Task 1 Step 5 返回首选路径，Step 1 第三个测试覆盖。
- 直接传 `.mp4` 文件路径行为不变：Task 1 Step 5 明确禁止修改文件输入分支。
- 不新增参数、不改变上传逻辑：本计划只修改 docstring、常量、`resolve_paths()` 和新增测试。

### 2. Placeholder scan

没有 `TBD`、`TODO`、`implement later`、未定义函数、泛化的“写相关测试”或“添加适当处理”。每一步都给出确切文件、代码或命令。

### 3. Type consistency

`resolve_paths(raw: str) -> tuple[Path, Path]` 签名保持不变。测试和实现都使用 `Path`，新增常量名 `FALLBACK_VIDEO_NAME` 在实现中唯一使用。