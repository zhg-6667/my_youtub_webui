r"""把 workfolder 下的本地化成片投稿到 B 站（基于 biliup-rs 命令行）。

用法：
    python scripts/upload_bilibili.py <任务目录或视频路径> --title "中文标题" [可选项]

位置参数既可以是任务目录（形如 ...__0n5Xs9XleLw），也可以直接是
media/video_final_trimmed.mp4。传任务目录时优先使用裁剪后成片，缺失则依次回退到
拼接后视频、合成后视频、原始视频。标题（--title）必须手动给中文。
简介、封面、来源、日期均从该任务的 metadata/ytdlp_info.json 自动生成。

首次使用需先登录一次（cookie 存本地复用）：
    .venv\Scripts\biliup.exe login
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# 脚本固定约定
PROJECT_ROOT = Path(__file__).resolve().parent.parent
WOLFEY_TAGS = "狼叔,宝可梦,神奇宝贝,VGC,WolfeyVGC,对战游戏,Pokemon,口袋妖怪"
DEFAULT_TAGS = {
    "WolfeyVGC": WOLFEY_TAGS,
}
AUTHOR_CONFIG_ENV = "BILIBILI_AUTHOR_CONFIG_JSON"
AUTHOR_TAGS_ENV = "BILIBILI_AUTHOR_TAGS_JSON"
AUTHOR_TIDS_ENV = "BILIBILI_AUTHOR_TIDS_JSON"
DEFAULT_TID = 17  # 单机游戏
COPYRIGHT_SELF = 1  # 自制
VIDEO_NAME = "video_final_trimmed.mp4"
CONCAT_VIDEO_NAME = "video_final_with_cta.mp4"
FALLBACK_VIDEO_NAME = "video_final.mp4"
SOURCE_VIDEO_NAME = "video_source.mp4"
VIDEO_CANDIDATES = (VIDEO_NAME, CONCAT_VIDEO_NAME, FALLBACK_VIDEO_NAME, SOURCE_VIDEO_NAME)
COVER_NAME = "cover.jpg"


class UploadBilibiliError(RuntimeError):
    """B 站投稿失败；库调用方可捕获，CLI 再转换成退出码。"""

    def __init__(
        self,
        message: str,
        *,
        return_code: int | None = None,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        super().__init__(message)
        self.return_code = return_code
        self.stdout = stdout
        self.stderr = stderr


@dataclass(frozen=True)
class UploadBilibiliOptions:
    path: str
    title: str
    tag: str | None = None
    tid: int | None = None
    cover: str | None = None
    desc: str | None = None
    dtime: str | None = None
    cookie: str | None = None
    biliup: str | None = None
    author_config_json: str | None = None
    author_tags_json: str | None = None
    author_tids_json: str | None = None
    dry_run: bool = False


@dataclass(frozen=True)
class PreparedBilibiliUpload:
    command: list[str]
    task_dir: Path
    video: Path
    cookie: Path
    title: str
    desc: str
    tag: str
    tid: int
    cover_path: str
    dtime: str | None
    raw_dtime: str | None


@dataclass(frozen=True)
class UploadBilibiliResult:
    return_code: int
    command: list[str]
    task_dir: Path
    video: Path
    title: str
    dtime: str | None
    stdout: str = ""
    stderr: str = ""


def fail(msg: str) -> "None":
    raise UploadBilibiliError(msg)


def resolve_paths(raw: str) -> "tuple[Path, Path]":
    """把用户给的路径解析成 (任务目录, 视频文件)。"""
    p = Path(raw).expanduser().resolve()
    if p.is_file():
        if p.suffix.lower() != ".mp4":
            fail(f"传入的文件不是 mp4：{p}")
        # media/video_final_trimmed.mp4 -> 任务目录是 video.parent.parent
        return p.parent.parent, p
    if p.is_dir():
        for name in VIDEO_CANDIDATES:
            candidate = p / "media" / name
            if candidate.is_file():
                return p, candidate
        return p, p / "media" / VIDEO_NAME
    fail(f"路径不存在：{p}")


def load_metadata(task_dir: Path) -> dict:
    meta = task_dir / "metadata" / "ytdlp_info.json"
    if not meta.exists():
        fail(f"找不到元数据文件：{meta}")
    with meta.open(encoding="utf-8") as f:
        return json.load(f)


def format_date(yyyymmdd: str) -> str:
    """20260117 -> 2026年1月17日（无前导零）。"""
    s = str(yyyymmdd)
    if len(s) != 8 or not s.isdigit():
        return s
    return f"{int(s[:4])}年{int(s[4:6])}月{int(s[6:8])}日"


def build_desc(info: dict) -> str:
    return (
        "请大家多多关注、点赞、投币、收藏，有什么想看的也可以评论区说\n"
        f"原视频：{info.get('title', '')}\n"
        f"原作者：{info.get('uploader') or info.get('channel', '')}\n"
        f"发布日期：{format_date(info.get('upload_date', ''))}\n"
        f"视频链接：{info.get('webpage_url') or info.get('original_url', '')}"
    )


def author_name(info: dict) -> str:
    return str(info.get("uploader") or info.get("channel") or "").strip()


def author_tags_config(raw_config: str | None = None) -> dict[str, str]:
    configured = dict(DEFAULT_TAGS)
    raw = (raw_config if raw_config is not None else os.getenv(AUTHOR_TAGS_ENV, "")).strip()
    if not raw:
        return configured
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError as exc:
        fail(f"{AUTHOR_TAGS_ENV} 不是有效 JSON：{exc}")
    if not isinstance(loaded, dict):
        fail(f"{AUTHOR_TAGS_ENV} 必须是作者到标签字符串的 JSON 对象。")
    for author, tags in loaded.items():
        if not isinstance(author, str) or not isinstance(tags, str):
            fail(f"{AUTHOR_TAGS_ENV} 的键和值都必须是字符串。")
        configured[author.strip()] = tags.strip()
    return configured


def default_tags_for_author(author: str, raw_config: str | None = None) -> str:
    if not author:
        return ""
    return author_tags_config(raw_config).get(author, "")


def author_tids_config(raw_config: str | None = None) -> dict[str, int]:
    raw = (raw_config if raw_config is not None else os.getenv(AUTHOR_TIDS_ENV, "")).strip()
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError as exc:
        fail(f"{AUTHOR_TIDS_ENV} 不是有效 JSON：{exc}")
    if not isinstance(loaded, dict):
        fail(f"{AUTHOR_TIDS_ENV} 必须是作者到分区 ID 的 JSON 对象。")
    configured: dict[str, int] = {}
    for author, tid in loaded.items():
        if not isinstance(author, str):
            fail(f"{AUTHOR_TIDS_ENV} 的键必须是字符串。")
        if isinstance(tid, bool) or not isinstance(tid, int):
            fail(f"{AUTHOR_TIDS_ENV} 的值必须是整数分区 ID。")
        if tid <= 0:
            fail(f"{AUTHOR_TIDS_ENV} 的分区 ID 必须大于 0。")
        key = author.strip()
        if key:
            configured[key] = tid
    return configured


def default_tid_for_author(author: str, raw_config: str | None = None) -> int:
    if not author:
        return DEFAULT_TID
    return author_tids_config(raw_config).get(author, DEFAULT_TID)


def author_upload_config(
    raw_config: str | None = None,
    *,
    legacy_tags_json: str | None = None,
    legacy_tids_json: str | None = None,
) -> dict[str, dict[str, str | int]]:
    configured: dict[str, dict[str, str | int]] = {
        author: {"tags": tags} for author, tags in DEFAULT_TAGS.items()
    }
    for author, tags in author_tags_config(legacy_tags_json).items():
        configured.setdefault(author, {})["tags"] = tags
    for author, tid in author_tids_config(legacy_tids_json).items():
        configured.setdefault(author, {})["tid"] = tid

    raw = (raw_config if raw_config is not None else os.getenv(AUTHOR_CONFIG_ENV, "")).strip()
    if not raw:
        return configured
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError as exc:
        fail(f"{AUTHOR_CONFIG_ENV} 不是有效 JSON：{exc}")
    if not isinstance(loaded, dict):
        fail(f"{AUTHOR_CONFIG_ENV} 必须是作者到投稿配置的 JSON 对象。")
    for author, profile in loaded.items():
        if not isinstance(author, str):
            fail(f"{AUTHOR_CONFIG_ENV} 的键必须是字符串。")
        if not isinstance(profile, dict):
            fail(f"{AUTHOR_CONFIG_ENV} 的值必须是包含 tags/tid 的对象。")
        key = author.strip()
        if not key:
            continue
        normalized: dict[str, str | int] = {}
        if "tags" in profile:
            tags = profile["tags"]
            if not isinstance(tags, str):
                fail(f"{AUTHOR_CONFIG_ENV} 中 tags 必须是字符串。")
            normalized["tags"] = tags.strip()
        if "tid" in profile:
            tid = profile["tid"]
            if isinstance(tid, bool) or not isinstance(tid, int):
                fail(f"{AUTHOR_CONFIG_ENV} 中 tid 必须是整数分区 ID。")
            if tid <= 0:
                fail(f"{AUTHOR_CONFIG_ENV} 中 tid 必须大于 0。")
            normalized["tid"] = tid
        configured[key] = {**configured.get(key, {}), **normalized}
    return configured


def find_biliup(explicit: "str | None") -> str:
    if explicit:
        return explicit
    venv_bin = PROJECT_ROOT / ".venv" / "Scripts" / "biliup.exe"
    if venv_bin.exists():
        return str(venv_bin)
    found = shutil.which("biliup")
    if found:
        return found
    fail(
        "找不到 biliup 命令。请先安装：.venv\\Scripts\\pip install biliup，"
        "或用 --biliup 指定可执行文件路径。"
    )


def parse_dtime(raw: str) -> str:
    """把人类可读的时间字符串转成 10 位 Unix 时间戳。

    支持格式：
        - 纯 10 位数字 → 直接当时间戳用
        - "2026-07-01 18:00" 或 "2026-07-01T18:00" 或 "2026-07-01T18:00:00"
        - "2026-07-01 18:00:00"
    所有非数字时间均按本地时区解析。
    """
    raw = raw.strip()
    # 已经是 10 位时间戳
    if len(raw) == 10 and raw.isdigit():
        return raw
    # 尝试多种日期时间格式
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            dt = datetime.strptime(raw, fmt)
            ts = int(dt.timestamp())
            # biliup 要求 ≥ 提交时间 + 4 小时，这里只做宽松校验（至少在未来）
            if ts <= int(time.time()):
                fail(f"定时发布时间必须在未来：{raw}")
            return str(ts)
        except ValueError:
            continue
    fail(f"无法解析发布时间：{raw!r}，支持格式：2026-07-01 18:00 或 10 位时间戳")


def shell_join(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def build_upload_command(options: UploadBilibiliOptions) -> PreparedBilibiliUpload:
    title_input = options.title.strip()
    if not title_input:
        fail("B 站稿件标题不能为空。")

    task_dir, video = resolve_paths(options.path)
    if not video.exists():
        fail(f"找不到视频文件：{video}")

    info = load_metadata(task_dir)
    desc = options.desc if options.desc is not None else build_desc(info)
    dtime = parse_dtime(options.dtime) if options.dtime else None

    # 标题自动包装：【中配】<正文><作者名>，例如 【中配】最弱的草系宝可梦是谁？WolfeyVGC
    author = author_name(info)
    title = f"{title_input}{author}"
    author_profiles = author_upload_config(
        options.author_config_json,
        legacy_tags_json=options.author_tags_json,
        legacy_tids_json=options.author_tids_json,
    )
    author_profile = author_profiles.get(author, {})
    tag = options.tag if options.tag is not None else str(author_profile.get("tags") or "")
    tid = options.tid if options.tid is not None else int(author_profile.get("tid") or DEFAULT_TID)

    # 封面：优先用户指定，否则复用下载阶段写入任务目录的封面。
    cover_path = ""
    if options.cover:
        cp = Path(options.cover).expanduser().resolve()
        if not cp.exists():
            fail(f"指定的封面不存在：{cp}")
        cover_path = str(cp)
    else:
        cached_cover = task_dir / COVER_NAME
        if cached_cover.exists() and cached_cover.stat().st_size > 0:
            cover_path = str(cached_cover)
        else:
            print("[封面] 未找到下载阶段生成的封面，跳过封面，交给 biliup 自动截帧")

    biliup = find_biliup(options.biliup)
    cookie = Path(options.cookie).resolve() if options.cookie else PROJECT_ROOT / "cookies.json"

    cmd = [
        biliup,
        "-u", str(cookie),
        "upload", str(video),
        "--copyright", str(COPYRIGHT_SELF),
        "--title", title,
        "--desc", desc,
        "--tag", tag,
        "--tid", str(tid),
    ]
    if cover_path:
        cmd += ["--cover", cover_path]
    if dtime:
        cmd += ["--dtime", dtime]

    return PreparedBilibiliUpload(
        command=cmd,
        task_dir=task_dir,
        video=video,
        cookie=cookie,
        title=title,
        desc=desc,
        tag=tag,
        tid=tid,
        cover_path=cover_path,
        dtime=dtime,
        raw_dtime=options.dtime,
    )


def run_upload_command(prepared: PreparedBilibiliUpload, *, dry_run: bool = False) -> UploadBilibiliResult:
    if dry_run:
        return UploadBilibiliResult(
            return_code=0,
            command=prepared.command,
            task_dir=prepared.task_dir,
            video=prepared.video,
            title=prepared.title,
            dtime=prepared.dtime,
            stdout=shell_join(prepared.command),
        )

    if not prepared.cookie.exists():
        biliup = prepared.command[0]
        fail(
            f"未找到登录 cookie：{prepared.cookie}\n"
            f"请先登录一次：\"{biliup}\" -u \"{prepared.cookie}\" login"
        )

    result = subprocess.run(prepared.command, capture_output=True, text=True, check=False)
    upload_result = UploadBilibiliResult(
        return_code=result.returncode,
        command=prepared.command,
        task_dir=prepared.task_dir,
        video=prepared.video,
        title=prepared.title,
        dtime=prepared.dtime,
        stdout=result.stdout,
        stderr=result.stderr,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or f"biliup 退出码：{result.returncode}").strip()
        raise UploadBilibiliError(
            f"B 站上传失败：{detail}",
            return_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )
    return upload_result


def upload_bilibili(options: UploadBilibiliOptions) -> UploadBilibiliResult:
    prepared = build_upload_command(options)
    return run_upload_command(prepared, dry_run=options.dry_run)


def print_upload_summary(prepared: PreparedBilibiliUpload) -> None:
    print("\n========== 投稿参数 ==========")
    print(f"视频   : {prepared.video}")
    print(f"标题   : {prepared.title}")
    print(f"分区   : {prepared.tid}    类型: 自制")
    print(f"标签   : {prepared.tag}")
    print(f"封面   : {prepared.cover_path or '(自动截帧)'}")
    if prepared.dtime:
        print(f"定时   : {prepared.raw_dtime}  → 时间戳 {prepared.dtime}")
    print(f"简介   :\n{prepared.desc}")
    print("==============================\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="把 workfolder 成片投稿到 B 站")
    parser.add_argument("path", help="任务目录，或 media/video_final_trimmed.mp4 路径")
    parser.add_argument("--title", required=True, help="B 站稿件标题（中文，必填）")
    parser.add_argument(
        "--tag",
        default=None,
        help=(
            "逗号分隔标签；不传时按作者从 DEFAULT_TAGS / "
            f"{AUTHOR_TAGS_ENV} 匹配，未匹配则为空"
        ),
    )
    parser.add_argument("--tid", type=int, default=None, help=f"投稿分区；不传时按作者配置选择，默认 {DEFAULT_TID}")
    parser.add_argument("--cover", help="自定义封面图路径（给了就不从 YouTube 下载）")
    parser.add_argument("--desc", help="自定义简介（给了就不按模板自动生成）")
    parser.add_argument("--dtime", help="定时发布时间，如 '2026-07-01 18:00' 或 10 位 Unix 时间戳")
    parser.add_argument("--cookie", help="biliup cookie 文件路径，默认项目根 cookies.json")
    parser.add_argument("--biliup", help="biliup 可执行文件路径")
    parser.add_argument("--dry-run", action="store_true", help="只打印命令，不真正上传")
    args = parser.parse_args()

    options = UploadBilibiliOptions(
        path=args.path,
        title=args.title,
        tag=args.tag,
        tid=args.tid,
        cover=args.cover,
        desc=args.desc,
        dtime=args.dtime,
        cookie=args.cookie,
        biliup=args.biliup,
        author_config_json=os.getenv(AUTHOR_CONFIG_ENV, ""),
        author_tags_json=os.getenv(AUTHOR_TAGS_ENV, ""),
        author_tids_json=os.getenv(AUTHOR_TIDS_ENV, ""),
        dry_run=args.dry_run,
    )

    try:
        prepared = build_upload_command(options)
        print_upload_summary(prepared)
        if args.dry_run:
            print("[dry-run] 将执行的命令：")
            print(shell_join(prepared.command))
            return

        print("[上传] 调用 biliup …\n")
        result = run_upload_command(prepared)
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        sys.exit(result.return_code)
    except UploadBilibiliError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        if exc.stdout:
            print(exc.stdout, end="")
        if exc.stderr:
            print(exc.stderr, end="", file=sys.stderr)
        sys.exit(exc.return_code or 1)


if __name__ == "__main__":
    main()
