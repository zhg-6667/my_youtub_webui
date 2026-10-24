from __future__ import annotations

import math
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests

from . import database
from .adapters.ytdlp import DEFAULT_USER_AGENT
from .config import DATA_DIR
from .youtube_channel_lookup import _ydl_init_lock
from .youtube_channel_monitor import _request_proxies, _youtube_ydl_opts, fetch_channel_feed

THUMBNAIL_DIR = DATA_DIR / "youtube-thumbnails"
_feed_cache: dict[str, tuple[float, dict[str, str]]] = {}
_feed_lock = threading.Lock()
_detail_slots = threading.BoundedSemaphore(3)


def _feed_dates(channel_id: str) -> dict[str, str]:
    with _feed_lock:
        cached = _feed_cache.get(channel_id)
        if cached and time.monotonic() - cached[0] < 300:
            return cached[1]
    try:
        dates = {entry.video_id: entry.published_at for entry in fetch_channel_feed(channel_id, 20) if entry.published_at}
    except Exception:
        dates = {}
    with _feed_lock:
        _feed_cache[channel_id] = (time.monotonic(), dates)
    return dates


def _detail_date(video_id: str) -> dict:
    import yt_dlp

    published_at = None
    published_date = None
    try:
        with _detail_slots:
            opts = {**_youtube_ydl_opts(noplaylist=True), "skip_download": True,
                    "socket_timeout": 12, "retries": 0, "extractor_retries": 0}
            with _ydl_init_lock:
                downloader = yt_dlp.YoutubeDL(opts)
            with downloader as ydl:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
            timestamp = info.get("timestamp")
            if isinstance(timestamp, (float, int)) and math.isfinite(timestamp):
                published_at = datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds")
            elif info.get("upload_date"):
                published_date = datetime.strptime(info["upload_date"], "%Y%m%d").date().isoformat()
    except Exception:
        pass
    return database.cache_youtube_video_metadata(video_id, published_at, published_date)


def fetch_video_metadata(items: list[dict]) -> list[dict]:
    unique = {item["video_id"]: item for item in items}
    cached = database.get_youtube_video_metadata(list(unique))
    missing = []
    for video_id, item in unique.items():
        row = cached.get(video_id)
        if row and (row["published_at"] or row["published_date"]):
            continue
        # Briefly cache unsuccessful lookups too, to avoid repeated failing requests.
        if row and (datetime.now(timezone.utc) - datetime.fromisoformat(row["checked_at"])).total_seconds() < 600:
            continue
        channel_id = item.get("channel_id")
        published_at = _feed_dates(channel_id).get(video_id) if channel_id else None
        if published_at:
            cached[video_id] = database.cache_youtube_video_metadata(video_id, published_at, None)
        else:
            missing.append(video_id)
    if missing:
        with ThreadPoolExecutor(max_workers=3) as executor:
            for row in executor.map(_detail_date, missing):
                cached[row["video_id"]] = row
    return [{"video_id": video_id, "published_at": cached.get(video_id, {}).get("published_at"),
             "published_date": cached.get(video_id, {}).get("published_date"), "metadata_checked": True}
            for video_id in unique]


def thumbnail_path(video_id: str):
    THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)
    target = THUMBNAIL_DIR / f"{video_id}.jpg"
    if target.is_file() and time.time() - target.stat().st_mtime < 86400:
        return target
    # The caller validates the ID; the remote host and image size are fixed.
    response = requests.get(
        f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
        proxies=_request_proxies(), headers={"User-Agent": DEFAULT_USER_AGENT},
        timeout=12, allow_redirects=False,
    )
    response.raise_for_status()
    if response.status_code != 200 or not response.headers.get("Content-Type", "").startswith("image/"):
        raise ValueError("Thumbnail unavailable.")
    if len(response.content) > 1024 * 1024:
        raise ValueError("Thumbnail too large.")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=THUMBNAIL_DIR, delete=False) as file:
            temporary = file.name
            file.write(response.content)
        os.replace(temporary, target)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return target
