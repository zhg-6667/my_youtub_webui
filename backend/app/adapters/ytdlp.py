from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import time

import requests
import yt_dlp

from ..sanitize import sanitize_text
from ..sources import SourceConfig
from ..youtube import extract_video_id, is_youtube_url


FORMAT_CANDIDATES = (
    "bestvideo[height<=1080]+bestaudio/best",
    "bestvideo+bestaudio/best",
    "bv*+ba/b",
    "best",
)

COVER_NAME = "cover.jpg"
THUMB_URLS = "https://img.youtube.com/vi/{vid}/{quality}.jpg"
THUMB_QUALITIES = ("maxresdefault", "hqdefault")

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _bootstrap_bilibili_cookie(cookie_path: Path) -> None:
    response = requests.get(
        "https://www.bilibili.com/",
        headers={"User-Agent": DEFAULT_USER_AGENT, "Referer": "https://www.bilibili.com/"},
        timeout=10,
    )
    response.raise_for_status()
    expires = int(time.time()) + 3600 * 24 * 365
    lines = ["# Netscape HTTP Cookie File", ""]
    cookies = dict(response.cookies)
    cookies.setdefault("SESSDATA", "anonymous_for_webpage_playinfo")
    for name, value in cookies.items():
        lines.append("\t".join([".bilibili.com", "TRUE", "/", "FALSE", str(expires), name, value]))
    cookie_path.parent.mkdir(parents=True, exist_ok=True)
    cookie_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _proxy_url(proxy_port: str = "") -> str:
    if proxy_port.strip():
        return f"http://127.0.0.1:{proxy_port.strip()}"
    return os.getenv("HTTP_PROXY") or os.getenv("http_proxy") or ""


def _ensure_cookie(source: SourceConfig) -> None:
    cookie_path = source.cookie_path
    if not cookie_path or source.name != "bilibili":
        return
    if cookie_path.exists() and cookie_path.stat().st_size > 0:
        return
    _bootstrap_bilibili_cookie(cookie_path)


def _ydl_base(source: SourceConfig, proxy_port: str = "") -> dict[str, Any]:
    opts: dict[str, Any] = {
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "js_runtimes": {"node": {}},
        "http_headers": {"User-Agent": DEFAULT_USER_AGENT},
    }
    cookie_path = source.cookie_path
    if cookie_path and cookie_path.exists() and cookie_path.stat().st_size > 0:
        opts["cookiefile"] = str(cookie_path)
    if not source.use_proxy:
        opts["proxy"] = ""
        return opts
    proxy = _proxy_url(proxy_port)
    if proxy:
        opts["proxy"] = proxy
    return opts


def _session_path(workfolder: Path, info: dict[str, Any]) -> Path:
    uploader = sanitize_text(str(info.get("uploader") or "unknown"))
    title = sanitize_text(str(info.get("title") or "untitled"))
    video_id = str(info.get("id") or extract_video_id(str(info.get("webpage_url") or "")))
    return workfolder / uploader / f"{title}__{video_id}"


def _is_format_unavailable(exc: Exception) -> bool:
    return "Requested format is not available" in str(exc)


def _remove_partial_outputs(video_file: Path) -> None:
    for candidate in video_file.parent.glob(f"{video_file.name}*"):
        if candidate == video_file:
            continue
        if candidate.is_file():
            candidate.unlink(missing_ok=True)


def _thumbnail_candidates(info: dict[str, Any], video_id: str) -> list[str]:
    urls: list[str] = []
    original_url = str(info.get("webpage_url") or info.get("original_url") or "")
    if is_youtube_url(original_url):
        for quality in THUMB_QUALITIES:
            urls.append(THUMB_URLS.format(vid=video_id, quality=quality))

    thumbnails = info.get("thumbnails") or []
    if isinstance(thumbnails, list):
        def score(item: Any) -> tuple[int, int]:
            if not isinstance(item, dict):
                return (0, 0)
            preference = item.get("preference")
            area = int(item.get("width") or 0) * int(item.get("height") or 0)
            return (int(preference) if isinstance(preference, int) else 0, area)

        for item in sorted(thumbnails, key=score, reverse=True):
            if isinstance(item, dict) and isinstance(item.get("url"), str):
                urls.append(item["url"])

    thumbnail = info.get("thumbnail")
    if isinstance(thumbnail, str):
        urls.append(thumbnail)

    seen: set[str] = set()
    deduped: list[str] = []
    for url in urls:
        if not url or url in seen:
            continue
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            continue
        seen.add(url)
        deduped.append(url)
    return deduped


def _download_cover_from_info(
    info: dict[str, Any],
    dest: Path,
    *,
    source: SourceConfig,
    proxy_port: str,
) -> Path | None:
    if dest.exists() and dest.stat().st_size > 0:
        return dest

    video_id = str(info.get("id") or "")
    proxies = None
    if source.use_proxy:
        proxy = _proxy_url(proxy_port)
        if proxy:
            proxies = {"http": proxy, "https": proxy}

    for url in _thumbnail_candidates(info, video_id):
        try:
            response = requests.get(
                url,
                headers={"User-Agent": DEFAULT_USER_AGENT},
                timeout=30,
                proxies=proxies,
            )
            response.raise_for_status()
            if not response.content:
                continue
            dest.write_bytes(response.content)
            return dest
        except requests.RequestException:
            continue
        except OSError:
            return None
    return None


def _download_with_format_candidates(
    url: str, video_file: Path, source: SourceConfig, proxy_port: str
) -> None:
    last_error: Exception | None = None
    for format_selector in FORMAT_CANDIDATES:
        download_opts = {
            **_ydl_base(source, proxy_port),
            "format": format_selector,
            "merge_output_format": "mp4",
            "outtmpl": str(video_file),
            "retries": 10,
            "fragment_retries": 10,
        }
        try:
            with yt_dlp.YoutubeDL(download_opts) as ydl:
                ydl.download([url])
            return
        except Exception as exc:
            last_error = exc
            _remove_partial_outputs(video_file)
            if not _is_format_unavailable(exc):
                continue
    if last_error:
        raise last_error


def download_video(
    url: str, workfolder: Path, source: SourceConfig, proxy_port: str = ""
) -> tuple[Path, dict[str, Any]]:
    video_id = extract_video_id(url)
    _ensure_cookie(source)
    info_opts = _ydl_base(source, proxy_port)
    with yt_dlp.YoutubeDL(info_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if str(info.get("id", video_id)) != video_id:
        raise ValueError("The resolved video id does not match the submitted URL.")

    session = _session_path(workfolder, info)
    media_dir = session / "media"
    metadata_dir = session / "metadata"
    media_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)

    video_file = media_dir / "video_source.mp4"
    cover_file = session / COVER_NAME
    metadata_file = metadata_dir / "ytdlp_info.json"
    metadata_file.write_text(json.dumps(ydl.sanitize_info(info), ensure_ascii=False, indent=2), encoding="utf-8")
    _download_cover_from_info(info, cover_file, source=source, proxy_port=proxy_port)

    if video_file.exists() and video_file.stat().st_size > 0:
        return session, info

    _download_with_format_candidates(url, video_file, source, proxy_port)

    if not video_file.exists() or video_file.stat().st_size == 0:
        raise RuntimeError("yt-dlp finished without producing media/video_source.mp4")

    return session, info
