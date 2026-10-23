from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any
from urllib.parse import urlparse

import time

import requests
import yt_dlp

from ..config import DATA_DIR, REPO_ROOT
from ..sanitize import sanitize_text
from ..sources import SourceConfig
from ..youtube import extract_video_id, is_youtube_url


FORMAT_CANDIDATES = (
    "bestvideo[height<=1080][protocol!*=m3u8]+bestaudio[protocol!*=m3u8]/best[height<=1080][protocol!*=m3u8]",
    "bestvideo[height<=720][protocol!*=m3u8]+bestaudio[protocol!*=m3u8]/best[height<=720][protocol!*=m3u8]",
    "bestvideo[height<=480][protocol!*=m3u8]+bestaudio[protocol!*=m3u8]/best[height<=480][protocol!*=m3u8]",
    "best[height<=1080][protocol!*=m3u8]",
)

COVER_NAME = "cover.jpg"
THUMB_URLS = "https://img.youtube.com/vi/{vid}/{quality}.jpg"
THUMB_QUALITIES = ("maxresdefault", "hqdefault")

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
)
DEFAULT_YOUTUBE_PLAYER_CLIENTS = "default,-web_safari"
DEFAULT_YOUTUBE_WEBPAGE_CLIENT = "web"
DEFAULT_YOUTUBE_PLAYER_JS_VERSION = "actual"
DEFAULT_YOUTUBE_FORMATS = "incomplete"
DEFAULT_YOUTUBE_FETCH_POT = "always"
DEFAULT_BGUTIL_SERVER_HOME = REPO_ROOT / "tools" / "bgutil-ytdlp-pot-provider" / "server"


def _user_agent() -> str:
    return os.getenv("YTDLP_USER_AGENT", "").strip() or DEFAULT_USER_AGENT


def _split_env_list(name: str, default: str) -> list[str]:
    value = os.getenv(name, default).strip()
    return [item.strip() for item in value.split(";") if item.strip()]


def _youtube_extractor_args() -> dict[str, list[str]]:
    args = {
        "player_client": _split_env_list("YTDLP_YOUTUBE_PLAYER_CLIENTS", DEFAULT_YOUTUBE_PLAYER_CLIENTS),
        "webpage_client": _split_env_list("YTDLP_YOUTUBE_WEBPAGE_CLIENT", DEFAULT_YOUTUBE_WEBPAGE_CLIENT),
        "player_js_version": _split_env_list(
            "YTDLP_YOUTUBE_PLAYER_JS_VERSION", DEFAULT_YOUTUBE_PLAYER_JS_VERSION
        ),
        "formats": _split_env_list("YTDLP_YOUTUBE_FORMATS", DEFAULT_YOUTUBE_FORMATS),
        "fetch_pot": _split_env_list("YTDLP_YOUTUBE_FETCH_POT", DEFAULT_YOUTUBE_FETCH_POT),
    }
    po_token = os.getenv("YTDLP_YOUTUBE_PO_TOKEN", "").strip()
    if po_token:
        args["po_token"] = [po_token]
    return args


def _bgutil_script_args() -> dict[str, list[str]] | None:
    configured = os.getenv("YTDLP_BGUTIL_SERVER_HOME", "").strip()
    server_home = Path(configured).expanduser() if configured else DEFAULT_BGUTIL_SERVER_HOME
    if not (server_home / "build" / "generate_once.js").exists():
        return None
    DATA_DIR.joinpath("cache").mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("XDG_CACHE_HOME", str(DATA_DIR / "cache"))
    return {"server_home": [str(server_home)]}


def _bootstrap_bilibili_cookie(cookie_path: Path) -> None:
    response = requests.get(
        "https://www.bilibili.com/",
        headers={"User-Agent": _user_agent(), "Referer": "https://www.bilibili.com/"},
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


@contextmanager
def _temporary_cookiefile(source: SourceConfig) -> Iterator[Path | None]:
    cookie_path = source.cookie_path
    if not cookie_path or not cookie_path.exists() or cookie_path.stat().st_size == 0:
        yield None
        return
    with tempfile.TemporaryDirectory(prefix="youdub-ytdlp-cookies-", dir=cookie_path.parent) as temp_dir:
        temp_cookie_path = Path(temp_dir) / cookie_path.name
        shutil.copy2(cookie_path, temp_cookie_path)
        yield temp_cookie_path


def _ydl_base(
    source: SourceConfig, proxy_port: str = "", cookie_path: Path | None = None
) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "js_runtimes": {"node": {}},
        "http_headers": {"User-Agent": _user_agent()},
    }
    if source.name == "youtube":
        opts["extractor_args"] = {"youtube": _youtube_extractor_args()}
        bgutil_args = _bgutil_script_args()
        if bgutil_args:
            opts["extractor_args"]["youtubepot-bgutilscript"] = bgutil_args
    cookie_path = cookie_path or source.cookie_path
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
                headers={"User-Agent": _user_agent()},
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
    url: str,
    video_file: Path,
    source: SourceConfig,
    proxy_port: str,
    cookie_path: Path | None = None,
) -> None:
    last_error: Exception | None = None
    for format_selector in FORMAT_CANDIDATES:
        download_opts = {
            **_ydl_base(source, proxy_port, cookie_path),
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
    with _temporary_cookiefile(source) as cookie_path:
        info_opts = _ydl_base(source, proxy_port, cookie_path)
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

        _download_with_format_candidates(url, video_file, source, proxy_port, cookie_path)

        if not video_file.exists() or video_file.stat().st_size == 0:
            raise RuntimeError("yt-dlp finished without producing media/video_source.mp4")

        return session, info
