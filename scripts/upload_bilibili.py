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
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import requests

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
COPYRIGHT_SELF = 1  # B 站投稿接口仍要求 1=自制；“内容无需标注”由 recreate 控制
RECREATE_NO_MARK = -1  # 内容无需标注
CREATION_STATEMENT_EXTRA_FIELDS = json.dumps(
    {
        "creationStatementId": RECREATE_NO_MARK,
        "recreate": RECREATE_NO_MARK,
    },
    separators=(",", ":"),
)
BILIBILI_TOPIC_SEARCH_PAGE_SIZE = 20
BILIBILI_TOPIC_SEARCH_MAX_PAGES = 5
BILIBILI_ARCHIVE_LIST_STATUSES = ("is_pubing", "pubed", "not_pubed")
BILIBILI_ARCHIVE_LIST_PAGE_SIZE = 30
BILIBILI_ARCHIVE_LIST_MAX_PAGES = 5
BILIBILI_AID_RESOLVE_MAX_ATTEMPTS = 6
BILIBILI_AID_RESOLVE_DELAY_SECONDS = 5.0
BILIBILI_SEASON_SWITCH_MAX_ATTEMPTS = 6
BILIBILI_SEASON_SWITCH_DELAY_SECONDS = 5.0
BILIUP_UPLOAD_MAX_ATTEMPTS = 2
BILIUP_UPLOAD_RETRY_DELAY_SECONDS = 8.0
VIDEO_NAME = "video_final_trimmed.mp4"
CONCAT_VIDEO_NAME = "video_final_with_cta.mp4"
FALLBACK_VIDEO_NAME = "video_final.mp4"
SOURCE_VIDEO_NAME = "video_source.mp4"
VIDEO_CANDIDATES = (VIDEO_NAME, CONCAT_VIDEO_NAME, FALLBACK_VIDEO_NAME, SOURCE_VIDEO_NAME)
COVER_NAME = "cover.jpg"
BILIBILI_WEB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/143.0.0.0 Safari/537.36"
    ),
    "Referer": "https://member.bilibili.com/platform/upload/video/frame",
    "Origin": "https://member.bilibili.com",
    "Accept": "application/json, text/plain, */*",
}


class UploadBilibiliError(RuntimeError):
    """B 站投稿失败；库调用方可捕获，CLI 再转换成退出码。"""

    def __init__(
        self,
        message: str,
        *,
        return_code: int | None = None,
        stdout: str = "",
        stderr: str = "",
        api_code: int | None = None,
        api_response: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.return_code = return_code
        self.stdout = stdout
        self.stderr = stderr
        self.api_code = api_code
        self.api_response = api_response


@dataclass(frozen=True)
class UploadBilibiliOptions:
    path: str
    title: str
    tag: str | None = None
    tid: int | None = None
    cover: str | None = None
    desc: str | None = None
    topic_name: str | None = None
    topic_id: int | None = None
    topic_mission_id: int | None = None
    dtime: str | None = None
    cookie: str | None = None
    biliup: str | None = None
    author_config_json: str | None = None
    author_tags_json: str | None = None
    author_tids_json: str | None = None
    dry_run: bool = False
    title_is_final: bool = False
    use_default_cover: bool = True


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
    topic_name: str
    topic_id: int | None
    topic_mission_id: int | None
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
    aid: int | None = None
    bvid: str | None = None
    topic_name: str = ""
    topic_id: int | None = None
    topic_mission_id: int | None = None


@dataclass(frozen=True)
class BilibiliTopic:
    id: int
    name: str
    mission_id: int | None = None


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
    date = format_date(info.get("upload_date", "")).strip()
    source_url = str(info.get("webpage_url") or info.get("original_url") or "").strip()
    lines = ["请大家多多关注、点赞、投币、收藏，有什么想看的也可以评论区说"]
    if date:
        lines.append(date)
    if source_url:
        lines.append(source_url)
    return "\n".join(lines)


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
            fail(f"{AUTHOR_CONFIG_ENV} 的值必须是投稿配置对象。")
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
        if "topic_name" in profile:
            topic_name = profile["topic_name"]
            if not isinstance(topic_name, str):
                fail(f"{AUTHOR_CONFIG_ENV} 中 topic_name 必须是字符串。")
            normalized["topic_name"] = topic_name.strip()
        for field in ("topic_id", "topic_mission_id", "season_id", "section_id"):
            if field not in profile:
                continue
            number = profile[field]
            if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
                fail(f"{AUTHOR_CONFIG_ENV} 中 {field} 必须是正整数。")
            normalized[field] = number
        if ("season_id" in normalized) != ("section_id" in normalized):
            fail(f"{AUTHOR_CONFIG_ENV} 中 season_id 和 section_id 必须同时配置。")
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
            # 这里只校验时间在未来，具体定时投稿条件由 B 站接口校验。
            if ts <= int(time.time()):
                fail(f"定时发布时间必须在未来：{raw}")
            return str(ts)
        except ValueError:
            continue
    fail(f"无法解析发布时间：{raw!r}，支持格式：2026-07-01 18:00 或 10 位时间戳")


def shell_join(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def load_cookie_bundle(cookie: str | Path | None = None) -> tuple[dict[str, str], str]:
    cookie_path = Path(cookie).resolve() if cookie else PROJECT_ROOT / "cookies.json"
    if not cookie_path.exists():
        fail(f"未找到登录 cookie：{cookie_path}")
    try:
        raw = json.loads(cookie_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"cookie 文件不是有效 JSON：{exc}")

    cookies: dict[str, str] = {}
    if isinstance(raw, dict) and isinstance(raw.get("cookie_info"), dict):
        for item in raw["cookie_info"].get("cookies") or []:
            if isinstance(item, dict) and item.get("name") and item.get("value") is not None:
                cookies[str(item["name"])] = str(item["value"])
    elif isinstance(raw, dict):
        cookies = {str(key): str(value) for key, value in raw.items() if isinstance(value, (str, int, float))}

    csrf = cookies.get("bili_jct", "")
    if not cookies.get("SESSDATA") or not csrf:
        fail("cookie 文件缺少 SESSDATA 或 bili_jct，无法访问 B 站创作中心接口。")
    return cookies, csrf


def request_json(method: str, url: str, *, cookies: dict[str, str] | None = None, **kwargs) -> dict:
    headers = dict(BILIBILI_WEB_HEADERS)
    headers.update(kwargs.pop("headers", {}) or {})
    try:
        response = requests.request(method, url, cookies=cookies, headers=headers, timeout=20, **kwargs)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        fail(f"B 站接口请求失败：{exc}")
    except ValueError as exc:
        fail(f"B 站接口返回不是有效 JSON：{exc}")
    if isinstance(data, dict) and data.get("code") not in (None, 0):
        code = data.get("code")
        raise UploadBilibiliError(
            f"B 站接口返回错误：{data}",
            api_code=code if isinstance(code, int) else None,
            api_response=data,
        )
    return data


def _topic_from_item(item: dict) -> BilibiliTopic | None:
    topic_id = item.get("topic_id", item.get("id"))
    topic_name = str(item.get("topic_name", item.get("name", "")) or "").strip()
    if isinstance(topic_id, bool) or not isinstance(topic_id, int) or topic_id <= 0 or not topic_name:
        return None
    mission_id = item.get("mission_id")
    if isinstance(mission_id, bool) or not isinstance(mission_id, int) or mission_id <= 0:
        mission_id = None
    return BilibiliTopic(id=topic_id, name=topic_name, mission_id=mission_id)


def search_bilibili_topics(
    keyword: str,
    *,
    cookie: str | Path | None = None,
    page_size: int = BILIBILI_TOPIC_SEARCH_PAGE_SIZE,
    max_pages: int = BILIBILI_TOPIC_SEARCH_MAX_PAGES,
) -> list[BilibiliTopic]:
    """Search Bilibili creator-center topics by keyword."""
    keyword = keyword.strip()
    if not keyword:
        return []
    cookies, _csrf = load_cookie_bundle(cookie)
    topics: list[BilibiliTopic] = []
    offset = 0
    for _page in range(max_pages):
        payload = request_json(
            "GET",
            "https://member.bilibili.com/x/vupre/web/topic/search",
            cookies=cookies,
            params={
                "page_size": max(1, page_size),
                "offset": offset,
                "keywords": keyword,
            },
        )
        result = ((payload.get("data") or {}).get("result") or {})
        for item in result.get("topics") or []:
            if isinstance(item, dict):
                topic = _topic_from_item(item)
                if topic:
                    topics.append(topic)
        page_info = result.get("page_info") or {}
        if not page_info.get("has_more"):
            break
        next_offset = page_info.get("offset")
        if isinstance(next_offset, bool) or not isinstance(next_offset, int) or next_offset <= offset:
            break
        offset = next_offset
    return topics


def resolve_bilibili_topic_by_name(topic_name: str, *, cookie: str | Path | None = None) -> BilibiliTopic:
    """Resolve a full topic name to a Bilibili topic id using exact name matching."""
    expected = topic_name.strip()
    if not expected:
        fail("B 站话题名称不能为空。")
    topics = search_bilibili_topics(expected, cookie=cookie)
    for topic in topics:
        if topic.name == expected:
            return topic
    candidates = "、".join(topic.name for topic in topics[:5])
    suffix = f"。搜索结果：{candidates}" if candidates else "。"
    fail(f"未找到完全匹配的话题：{expected}{suffix}")


def build_extra_fields(*, topic_id: int | None = None, mission_id: int | None = None) -> str:
    fields: dict[str, int] = {
        "creationStatementId": RECREATE_NO_MARK,
        "recreate": RECREATE_NO_MARK,
    }
    if topic_id is not None:
        if isinstance(topic_id, bool) or topic_id <= 0:
            fail("B 站话题 ID 必须是大于 0 的整数。")
        fields["topic_id"] = topic_id
    if mission_id is not None:
        if isinstance(mission_id, bool) or mission_id <= 0:
            fail("B 站话题活动 ID 必须是大于 0 的整数。")
        fields["mission_id"] = mission_id
    return json.dumps(fields, separators=(",", ":"))


def list_bilibili_seasons(cookie: str | Path | None = None) -> list[dict[str, str | int]]:
    cookies, _csrf = load_cookie_bundle(cookie)
    payload = request_json(
        "GET",
        "https://member.bilibili.com/x2/creative/web/seasons",
        cookies=cookies,
        params={"pn": 1, "ps": 100, "order": "mtime", "sort": "desc", "draft": 1},
    )
    seasons: list[dict[str, str | int]] = []
    for item in (payload.get("data") or {}).get("seasons") or []:
        season = item.get("season") or {}
        season_id = season.get("id")
        season_title = str(season.get("title") or "").strip()
        sections = ((item.get("sections") or {}).get("sections") or [])
        for section in sections:
            section_id = section.get("id")
            if not isinstance(season_id, int) or not isinstance(section_id, int):
                continue
            seasons.append(
                {
                    "season_id": season_id,
                    "season_title": season_title,
                    "section_id": section_id,
                    "section_title": str(section.get("title") or "正片").strip() or "正片",
                }
            )
    return seasons


def parse_uploaded_archive(stdout: str, stderr: str = "") -> tuple[int | None, str | None]:
    text = "\n".join(part for part in (stdout, stderr) if part)
    aid: int | None = None
    bvid: str | None = None
    aid_match = re.search(r"""["']?(?:aid|archive_id)["']?\s*[:=]\s*["']?(\d+)""", text, re.IGNORECASE)
    if not aid_match:
        aid_match = re.search(r"\bav(\d{3,})\b", text, re.IGNORECASE)
    if aid_match:
        aid = int(aid_match.group(1))
    bvid_match = re.search(r"\b(BV[0-9A-Za-z]{8,})\b", text)
    if bvid_match:
        bvid = bvid_match.group(1)
    return aid, bvid


ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def strip_ansi(value: str) -> str:
    return ANSI_ESCAPE_RE.sub("", value)


def is_retryable_biliup_upload_error(stdout: str = "", stderr: str = "") -> bool:
    text = strip_ansi("\n".join(part for part in (stdout, stderr) if part)).lower()
    if "invalid peer certificate" in text and "expired" in text:
        return True
    if "error sending request for url" not in text:
        return False
    return any(
        marker in text
        for marker in (
            "client error (connect)",
            "connection reset",
            "timed out",
            "timeout",
            "dns error",
            "failed to lookup address",
        )
    )


def parse_biliup_list_bvid(stdout: str | None, title: str) -> str | None:
    expected = title.strip()
    expected_truncated = expected[:80]
    for raw_line in (stdout or "").splitlines():
        line = strip_ansi(raw_line).strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        bvid = parts[0].strip()
        listed_title = parts[1].strip()
        if not bvid.startswith("BV"):
            continue
        if listed_title == expected or listed_title == expected_truncated:
            return bvid
    return None


def find_recent_uploaded_bvid_by_title(
    *,
    biliup: str,
    cookie: Path,
    title: str,
    max_pages: int = 3,
) -> str | None:
    try:
        result = subprocess.run(
            [
                biliup,
                "-u",
                str(cookie),
                "list",
                "--max-pages",
                str(max_pages),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    return parse_biliup_list_bvid(output, title)


def _archive_title_matches(listed_title: str, expected_title: str) -> bool:
    expected = expected_title.strip()
    return listed_title.strip() in {expected, expected[:80]}


def _archive_from_list_item(item: dict) -> dict | None:
    archive = item.get("Archive")
    if isinstance(archive, dict):
        return archive
    if any(key in item for key in ("aid", "bvid", "title")):
        return item
    return None


def _iter_creator_archives(
    cookie: str | Path | None = None,
    *,
    statuses: tuple[str, ...] = BILIBILI_ARCHIVE_LIST_STATUSES,
    max_pages: int = BILIBILI_ARCHIVE_LIST_MAX_PAGES,
):
    for archive, _item in _iter_creator_archive_records(
        cookie,
        statuses=statuses,
        max_pages=max_pages,
    ):
        yield archive


def _iter_creator_archive_records(
    cookie: str | Path | None = None,
    *,
    statuses: tuple[str, ...] = BILIBILI_ARCHIVE_LIST_STATUSES,
    max_pages: int = BILIBILI_ARCHIVE_LIST_MAX_PAGES,
):
    cookies, _csrf = load_cookie_bundle(cookie)
    for status in statuses:
        for page in range(1, max(1, max_pages) + 1):
            payload = request_json(
                "GET",
                "https://member.bilibili.com/x/web/archives",
                cookies=cookies,
                params={
                    "status": status,
                    "pn": page,
                    "ps": BILIBILI_ARCHIVE_LIST_PAGE_SIZE,
                    "coop": 1,
                    "interactive": 1,
                },
                headers={"Referer": "https://member.bilibili.com/platform/upload-manager/article"},
            )
            data = payload.get("data") or {}
            items = list(data.get("arc_audits") or []) + list(data.get("archives") or [])
            if not items:
                break
            for item in items:
                if not isinstance(item, dict):
                    continue
                archive = _archive_from_list_item(item)
                if archive:
                    yield archive, item


def find_recent_creator_archive_by_title(
    *,
    cookie: str | Path | None = None,
    title: str,
) -> tuple[int | None, str | None]:
    for archive in _iter_creator_archives(cookie):
        listed_title = str(archive.get("title") or "")
        if not _archive_title_matches(listed_title, title):
            continue
        aid = archive.get("aid")
        bvid = str(archive.get("bvid") or "").strip() or None
        return (aid if isinstance(aid, int) else None), bvid
    return None, None


def find_recent_creator_archive_by_bvid(
    *,
    cookie: str | Path | None = None,
    bvid: str,
) -> tuple[int | None, str | None]:
    expected = bvid.strip()
    for archive in _iter_creator_archives(cookie):
        listed_bvid = str(archive.get("bvid") or "").strip()
        if listed_bvid != expected:
            continue
        aid = archive.get("aid")
        return (aid if isinstance(aid, int) else None), listed_bvid
    return None, None


def resolve_archive_aid(
    *,
    aid: int | None,
    bvid: str | None,
    cookie: str | Path | None = None,
    max_attempts: int = BILIBILI_AID_RESOLVE_MAX_ATTEMPTS,
    delay_seconds: float = BILIBILI_AID_RESOLVE_DELAY_SECONDS,
) -> int:
    if aid:
        return aid
    if not bvid:
        fail("上传已成功，但无法从 biliup 输出中解析 aid/bvid，无法加入合集。")
    attempts = max(1, max_attempts)
    for attempt in range(attempts):
        try:
            payload = request_json(
                "GET",
                "https://api.bilibili.com/x/web-interface/view",
                params={"bvid": bvid},
            )
            resolved = (payload.get("data") or {}).get("aid")
            if isinstance(resolved, int):
                return resolved
        except UploadBilibiliError:
            pass
        resolved, _resolved_bvid = find_recent_creator_archive_by_bvid(cookie=cookie, bvid=bvid)
        if isinstance(resolved, int):
            return resolved
        if attempt < attempts - 1:
            time.sleep(max(0.0, delay_seconds))
    fail(f"无法通过 bvid 反查 aid：{bvid}")


def _cid_from_view_data(data: dict) -> int | None:
    cid = data.get("cid")
    if isinstance(cid, int) and cid > 0:
        return cid
    for page in data.get("pages") or []:
        if not isinstance(page, dict):
            continue
        cid = page.get("cid")
        if isinstance(cid, int) and cid > 0:
            return cid
    return None


def _cid_from_creator_archive_item(archive: dict, item: dict) -> int | None:
    cid = archive.get("cid")
    if isinstance(cid, int) and cid > 0:
        return cid
    for raw_cid in item.get("cid_list") or archive.get("cid_list") or []:
        if isinstance(raw_cid, int) and raw_cid > 0:
            return raw_cid
    for video in item.get("Videos") or archive.get("Videos") or []:
        if not isinstance(video, dict):
            continue
        cid = video.get("cid")
        if isinstance(cid, int) and cid > 0:
            return cid
    return None


def resolve_archive_cid(
    *,
    aid: int,
    bvid: str | None = None,
    cookie: str | Path | None = None,
    max_attempts: int = BILIBILI_AID_RESOLVE_MAX_ATTEMPTS,
    delay_seconds: float = BILIBILI_AID_RESOLVE_DELAY_SECONDS,
) -> int:
    if not aid:
        fail("上传已成功，但缺少 aid，无法解析 cid 后加入合集。")
    attempts = max(1, max_attempts)
    for attempt in range(attempts):
        try:
            payload = request_json(
                "GET",
                "https://api.bilibili.com/x/web-interface/view",
                params={"aid": aid},
            )
            cid = _cid_from_view_data(payload.get("data") or {})
            if isinstance(cid, int):
                return cid
        except UploadBilibiliError:
            pass

        for archive, item in _iter_creator_archive_records(cookie):
            archive_aid = archive.get("aid")
            archive_bvid = str(archive.get("bvid") or "").strip()
            if archive_aid != aid and (not bvid or archive_bvid != bvid):
                continue
            cid = _cid_from_creator_archive_item(archive, item)
            if isinstance(cid, int):
                return cid
        if attempt < attempts - 1:
            time.sleep(max(0.0, delay_seconds))
    suffix = f"，bvid={bvid}" if bvid else ""
    fail(f"无法解析稿件 cid：aid={aid}{suffix}")


def add_bilibili_season_episode(
    *,
    cookie: str | Path | None = None,
    aid: int,
    cid: int,
    section_id: int,
    title: str,
    max_attempts: int = BILIBILI_SEASON_SWITCH_MAX_ATTEMPTS,
    delay_seconds: float = BILIBILI_SEASON_SWITCH_DELAY_SECONDS,
) -> dict:
    cookies, csrf = load_cookie_bundle(cookie)
    attempts = max(1, max_attempts)
    last_error: UploadBilibiliError | None = None
    for attempt in range(attempts):
        try:
            return request_json(
                "POST",
                "https://member.bilibili.com/x2/creative/web/season/section/episodes/add",
                cookies=cookies,
                params={"csrf": csrf},
                json={
                    "sectionId": section_id,
                    "episodes": [
                        {
                            "aid": aid,
                            "cid": cid,
                            "title": title,
                            "charging_pay": 0,
                        }
                    ],
                },
                headers={"Content-Type": "application/json; charset=UTF-8"},
            )
        except UploadBilibiliError as exc:
            if exc.api_code != -404:
                raise
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(max(0.0, delay_seconds))
    if last_error:
        raise last_error
    fail("B 站合集接口没有返回结果。")


def switch_bilibili_season(
    *,
    cookie: str | Path | None = None,
    aid: int,
    season_id: int,
    section_id: int,
    title: str,
    max_attempts: int = BILIBILI_SEASON_SWITCH_MAX_ATTEMPTS,
    delay_seconds: float = BILIBILI_SEASON_SWITCH_DELAY_SECONDS,
) -> dict:
    """Compatibility wrapper for older callers; new code should add an episode."""
    cid = resolve_archive_cid(
        aid=aid,
        cookie=cookie,
        max_attempts=max_attempts,
        delay_seconds=delay_seconds,
    )
    return add_bilibili_season_episode(
        cookie=cookie,
        aid=aid,
        cid=cid,
        section_id=section_id,
        title=title,
        max_attempts=max_attempts,
        delay_seconds=delay_seconds,
    )


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

    # 标题自动包装：<正文>-<作者名>，例如 最弱的草系宝可梦是谁？-WolfeyVGC
    author = author_name(info)
    title = f"{title_input}-{author}" if author and not options.title_is_final else title_input
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
    elif options.use_default_cover:
        cached_cover = task_dir / COVER_NAME
        if cached_cover.exists() and cached_cover.stat().st_size > 0:
            cover_path = str(cached_cover)
        else:
            print("[封面] 未找到下载阶段生成的封面，跳过封面，交给 biliup 自动截帧")

    cookie = Path(options.cookie).resolve() if options.cookie else PROJECT_ROOT / "cookies.json"
    topic_name = (options.topic_name or "").strip()
    topic_id = options.topic_id
    topic_mission_id = options.topic_mission_id
    if topic_name and topic_id is None:
        topic = resolve_bilibili_topic_by_name(topic_name, cookie=cookie)
        topic_id = topic.id
        topic_mission_id = topic.mission_id
        topic_name = topic.name
    extra_fields = build_extra_fields(topic_id=topic_id, mission_id=topic_mission_id)

    biliup = find_biliup(options.biliup)

    cmd = [
        biliup,
        "-u", str(cookie),
        "upload",
        "--submit", "web",
        str(video),
        "--copyright", str(COPYRIGHT_SELF),
        "--title", title,
        "--desc", desc,
        "--tag", tag,
        "--tid", str(tid),
        "--extra-fields", extra_fields,
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
        topic_name=topic_name,
        topic_id=topic_id,
        topic_mission_id=topic_mission_id,
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
            topic_name=prepared.topic_name,
            topic_id=prepared.topic_id,
            topic_mission_id=prepared.topic_mission_id,
        )

    if not prepared.cookie.exists():
        biliup = prepared.command[0]
        fail(
            f"未找到登录 cookie：{prepared.cookie}\n"
            f"请先登录一次：\"{biliup}\" -u \"{prepared.cookie}\" login"
        )

    attempts: list[subprocess.CompletedProcess[str]] = []
    result: subprocess.CompletedProcess[str] | None = None
    for attempt in range(1, BILIUP_UPLOAD_MAX_ATTEMPTS + 1):
        result = subprocess.run(
            prepared.command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        attempts.append(result)
        aid, bvid = parse_uploaded_archive(result.stdout, result.stderr)
        if result.returncode == 0:
            break
        if aid or bvid:
            break
        if attempt >= BILIUP_UPLOAD_MAX_ATTEMPTS:
            break
        if not is_retryable_biliup_upload_error(result.stdout, result.stderr):
            break
        time.sleep(max(0.0, BILIUP_UPLOAD_RETRY_DELAY_SECONDS))
    assert result is not None
    if len(attempts) > 1:
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        for index, attempt_result in enumerate(attempts, start=1):
            prefix = f"[biliup 第 {index} 次尝试]\n"
            if attempt_result.stdout:
                stdout_parts.append(prefix + attempt_result.stdout)
            if attempt_result.stderr:
                stderr_parts.append(prefix + attempt_result.stderr)
        stdout = "\n".join(stdout_parts)
        stderr = "\n".join(stderr_parts)
    else:
        stdout = result.stdout
        stderr = result.stderr
    aid, bvid = parse_uploaded_archive(stdout, stderr)
    if result.returncode == 0 and bvid and not aid:
        aid, resolved_bvid = find_recent_creator_archive_by_bvid(
            cookie=prepared.cookie,
            bvid=bvid,
        )
        bvid = resolved_bvid or bvid
    if result.returncode == 0 and not aid and not bvid:
        aid, bvid = find_recent_creator_archive_by_title(
            cookie=prepared.cookie,
            title=prepared.title,
        )
    if result.returncode == 0 and not aid and not bvid:
        bvid = find_recent_uploaded_bvid_by_title(
            biliup=prepared.command[0],
            cookie=prepared.cookie,
            title=prepared.title,
        )
    if result.returncode == 0 and bvid and not aid:
        aid, resolved_bvid = find_recent_creator_archive_by_bvid(
            cookie=prepared.cookie,
            bvid=bvid,
        )
        bvid = resolved_bvid or bvid
    upload_result = UploadBilibiliResult(
        return_code=result.returncode,
        command=prepared.command,
        task_dir=prepared.task_dir,
        video=prepared.video,
        title=prepared.title,
        dtime=prepared.dtime,
        stdout=stdout,
        stderr=stderr,
        aid=aid,
        bvid=bvid,
        topic_name=prepared.topic_name,
        topic_id=prepared.topic_id,
        topic_mission_id=prepared.topic_mission_id,
    )
    if result.returncode != 0:
        detail = (stderr or stdout or f"biliup 退出码：{result.returncode}").strip()
        raise UploadBilibiliError(
            f"B 站上传失败：{detail}",
            return_code=result.returncode,
            stdout=stdout,
            stderr=stderr,
        )
    return upload_result


def upload_bilibili(options: UploadBilibiliOptions) -> UploadBilibiliResult:
    prepared = build_upload_command(options)
    return run_upload_command(prepared, dry_run=options.dry_run)


def print_upload_summary(prepared: PreparedBilibiliUpload) -> None:
    print("\n========== 投稿参数 ==========")
    print(f"视频   : {prepared.video}")
    print(f"标题   : {prepared.title}")
    print(f"分区   : {prepared.tid}")
    print("创作声明: 内容无需标注")
    if prepared.topic_id:
        mission = f"，mission_id={prepared.topic_mission_id}" if prepared.topic_mission_id else ""
        print(f"话题   : {prepared.topic_name or '(按 ID 指定)'}（topic_id={prepared.topic_id}{mission}）")
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
    parser.add_argument("--topic-name", help="完整 B 站话题名称；上传前会查询并精确匹配 topic_id")
    parser.add_argument("--topic-id", type=int, default=None, help="直接指定 B 站 topic_id；优先于 --topic-name 查询")
    parser.add_argument("--topic-mission-id", type=int, default=None, help="话题关联的 mission_id；通常由 --topic-name 自动查询")
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
        topic_name=args.topic_name,
        topic_id=args.topic_id,
        topic_mission_id=args.topic_mission_id,
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
