"""把 workfolder 下的本地化成片投稿到 B 站（基于 biliup-rs 命令行）。

用法：
    python scripts/upload_bilibili.py <任务目录或视频路径> --title "中文标题" [可选项]

位置参数既可以是任务目录（形如 ...__0n5Xs9XleLw），也可以直接是
media/video_final.mp4，脚本会自动推断另一边。标题（--title）必须手动给中文。
简介、封面、来源、日期均从该任务的 metadata/ytdlp_info.json 自动生成。

首次使用需先登录一次（cookie 存本地复用）：
    .venv\\Scripts\\biliup.exe login
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# 脚本固定约定
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TAGS = "中配,狼叔,宝可梦"
DEFAULT_TID = 17  # 单机游戏
COPYRIGHT_SELF = 1  # 自制
VIDEO_NAME = "video_final.mp4"
COVER_NAME = "cover.jpg"
THUMB_URLS = "https://img.youtube.com/vi/{vid}/{quality}.jpg"
THUMB_QUALITIES = ("maxresdefault", "hqdefault")


def fail(msg: str) -> "None":
    print(f"[错误] {msg}", file=sys.stderr)
    sys.exit(1)


def resolve_paths(raw: str) -> "tuple[Path, Path]":
    """把用户给的路径解析成 (任务目录, 视频文件)。"""
    p = Path(raw).expanduser().resolve()
    if p.is_file():
        if p.suffix.lower() != ".mp4":
            fail(f"传入的文件不是 mp4：{p}")
        # media/video_final.mp4 -> 任务目录是 video.parent.parent
        return p.parent.parent, p
    if p.is_dir():
        return p, p / "media" / VIDEO_NAME
    fail(f"路径不存在：{p}")


def load_metadata(task_dir: Path) -> dict:
    meta = task_dir / "metadata" / "ytdlp_info.json"
    if not meta.exists():
        fail(f"找不到元数据文件：{meta}")
    with meta.open(encoding="utf-8") as f:
        return json.load(f)


def extract_video_id(info: dict) -> str:
    url = info.get("webpage_url") or info.get("original_url") or ""
    qs = parse_qs(urlparse(url).query)
    if qs.get("v"):
        return qs["v"][0]
    if info.get("id"):
        return str(info["id"])
    fail(f"无法从链接解析出视频 ID：{url!r}")


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


def download_cover(vid: str, dest: Path) -> "Path | None":
    """下载 YouTube 封面（maxres 失败回退 hq）到 dest，失败返回 None。"""
    for quality in THUMB_QUALITIES:
        url = THUMB_URLS.format(vid=vid, quality=quality)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
            if not data:
                continue
            dest.write_bytes(data)
            print(f"[封面] 已下载 {quality}（{len(data)} 字节）-> {dest}")
            return dest
        except Exception as exc:  # noqa: BLE001 - 任一画质失败都继续尝试
            print(f"[封面] {quality} 下载失败：{exc}")
    return None


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


def main() -> None:
    parser = argparse.ArgumentParser(description="把 workfolder 成片投稿到 B 站")
    parser.add_argument("path", help="任务目录，或 media/video_final.mp4 路径")
    parser.add_argument("--title", required=True, help="B 站稿件标题（中文，必填）")
    parser.add_argument("--tag", default=DEFAULT_TAGS, help=f"逗号分隔标签，默认 {DEFAULT_TAGS}")
    parser.add_argument("--tid", type=int, default=DEFAULT_TID, help=f"投稿分区，默认 {DEFAULT_TID}")
    parser.add_argument("--cover", help="自定义封面图路径（给了就不从 YouTube 下载）")
    parser.add_argument("--desc", help="自定义简介（给了就不按模板自动生成）")
    parser.add_argument("--dtime", help="定时发布时间，如 '2026-07-01 18:00' 或 10 位 Unix 时间戳")
    parser.add_argument("--cookie", help="biliup cookie 文件路径，默认项目根 cookies.json")
    parser.add_argument("--biliup", help="biliup 可执行文件路径")
    parser.add_argument("--dry-run", action="store_true", help="只打印命令，不真正上传")
    args = parser.parse_args()

    task_dir, video = resolve_paths(args.path)
    if not video.exists():
        fail(f"找不到视频文件：{video}")

    info = load_metadata(task_dir)
    vid = extract_video_id(info)
    desc = args.desc if args.desc is not None else build_desc(info)
    dtime = parse_dtime(args.dtime) if args.dtime else None

    # 标题自动包装：【中配】<正文><作者名>，例如 【中配】最弱的草系宝可梦是谁？WolfeyVGC
    author = info.get("uploader") or info.get("channel") or ""
    title = f"【中配】{args.title}{author}"

    # 封面：优先用户指定，否则下载 YouTube 缩略图到任务目录
    cover_path = ""
    if args.cover:
        cp = Path(args.cover).expanduser().resolve()
        if not cp.exists():
            fail(f"指定的封面不存在：{cp}")
        cover_path = str(cp)
    else:
        got = download_cover(vid, task_dir / COVER_NAME)
        if got:
            cover_path = str(got)
        else:
            print("[封面] 下载失败，跳过封面，交给 biliup 自动截帧")

    biliup = find_biliup(args.biliup)
    cookie = Path(args.cookie).resolve() if args.cookie else PROJECT_ROOT / "cookies.json"

    cmd = [
        biliup,
        "-u", str(cookie),
        "upload", str(video),
        "--copyright", str(COPYRIGHT_SELF),
        "--title", title,
        "--desc", desc,
        "--tag", args.tag,
        "--tid", str(args.tid),
    ]
    if cover_path:
        cmd += ["--cover", cover_path]
    if dtime:
        cmd += ["--dtime", dtime]

    print("\n========== 投稿参数 ==========")
    print(f"视频   : {video}")
    print(f"标题   : {title}")
    print(f"分区   : {args.tid}    类型: 自制")
    print(f"标签   : {args.tag}")
    print(f"封面   : {cover_path or '(自动截帧)'}")
    if dtime:
        print(f"定时   : {args.dtime}  → 时间戳 {dtime}")
    print(f"简介   :\n{desc}")
    print("==============================\n")

    if args.dry_run:
        print("[dry-run] 将执行的命令：")
        print(" ".join(f'"{c}"' if " " in c else c for c in cmd))
        return

    if not cookie.exists():
        fail(
            f"未找到登录 cookie：{cookie}\n"
            f"请先登录一次：\"{biliup}\" -u \"{cookie}\" login"
        )

    print("[上传] 调用 biliup …\n")
    result = subprocess.run(cmd)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
