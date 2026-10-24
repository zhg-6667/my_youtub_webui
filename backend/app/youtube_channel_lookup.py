from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor
from itertools import islice
from typing import Any
from urllib.parse import unquote, urlparse

from . import database
from .youtube import YOUTUBE_ID_RE
from .youtube_channel_monitor import _youtube_ydl_opts

MAX_CHANNELS = 20
MAX_LATEST_COUNT = 100
_ydl_init_lock = threading.Lock()


def normalize_channel_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
        "youtube.com", "www.youtube.com", "m.youtube.com",
    } or parsed.username or parsed.password or parsed.port:
        raise ValueError("Enter a YouTube author homepage URL.")
    parts = [part for part in parsed.path.split("/") if part]
    if parts and parts[0].startswith("@") and len(parts[0]) > 1:
        base_length = 1
    elif len(parts) >= 2 and parts[0] in {"channel", "c", "user"}:
        if parts[0] == "channel" and not re.fullmatch(r"UC[\w-]{22}", parts[1], flags=re.ASCII):
            raise ValueError("Invalid YouTube channel ID.")
        base_length = 2
    else:
        raise ValueError("Use an author homepage, not a video or playlist URL.")
    if len(parts) > base_length and parts[base_length:] not in [["videos"], ["shorts"], ["featured"], ["streams"]]:
        raise ValueError("Use an author homepage, videos tab, or Shorts tab URL.")
    return "https://www.youtube.com/" + "/".join(parts[:base_length])


def fetch_author_videos(channel_url: str, latest_count: int) -> dict[str, Any]:
    result: dict[str, Any] = {
        "input_url": channel_url, "channel_url": channel_url, "channel_id": None,
        "title": unquote(urlparse(channel_url).path.split("/")[-1]),
        "videos": [], "shorts": [], "errors": {},
    }
    try:
        normalized = normalize_channel_url(channel_url)
    except ValueError as exc:
        result["errors"]["channel"] = str(exc)
        return result
    result["channel_url"] = normalized

    import yt_dlp

    opts = {
        **_youtube_ydl_opts(noplaylist=False),
        "extract_flat": True,
        "playlistend": latest_count,
        "skip_download": True,
        "socket_timeout": 15,
        "retries": 1,
        "extractor_retries": 1,
    }
    # yt-dlp loads global plugin registries on first construction.
    with _ydl_init_lock:
        downloader = yt_dlp.YoutubeDL(opts)
    with downloader as ydl:
        for tab in ("videos", "shorts"):
            try:
                info = ydl.extract_info(f"{normalized}/{tab}", download=False)
                if not info:
                    raise ValueError("YouTube returned no channel information.")
                result["channel_id"] = info.get("channel_id") or result["channel_id"]
                result["title"] = info.get("channel") or info.get("uploader") or result["title"]
                seen: set[str] = set()
                for entry in islice(info.get("entries") or [], latest_count):
                    if not entry:
                        continue
                    video_id = str(entry.get("id") or "")
                    if not YOUTUBE_ID_RE.fullmatch(video_id) or video_id in seen:
                        continue
                    seen.add(video_id)
                    duration = entry.get("duration")
                    result[tab].append({
                        "video_id": video_id,
                        "title": entry.get("title") or video_id,
                        "url": f"https://www.youtube.com/shorts/{video_id}" if tab == "shorts"
                        else f"https://www.youtube.com/watch?v={video_id}",
                        "duration_seconds": max(0, round(duration)) if isinstance(duration, (int, float)) else None,
                        "thumbnail_url": f"/api/youtube-videos/{video_id}/thumbnail",
                        "published_at": None,
                        "published_date": None,
                        "metadata_checked": False,
                        "task_id": None,
                        "task_status": None,
                    })
            except Exception as exc:
                # A channel may publish only Shorts or only ordinary videos.
                if f"This channel does not have a {tab} tab" not in str(exc):
                    result["errors"][tab] = str(exc)
    return result


def lookup_authors(channel_urls: list[str], latest_count: int) -> list[dict[str, Any]]:
    # Collapse duplicate homepage/tab URLs while retaining invalid rows as errors.
    unique: dict[str, str] = {}
    for value in channel_urls:
        cleaned = value.strip()
        try:
            key = normalize_channel_url(cleaned)
        except ValueError:
            key = cleaned
        unique.setdefault(key, cleaned)

    def fetch(url: str) -> dict[str, Any]:
        try:
            return fetch_author_videos(url, latest_count)
        except Exception as exc:
            return {"input_url": url, "channel_url": url, "channel_id": None, "title": url,
                    "videos": [], "shorts": [], "errors": {"channel": str(exc)}}

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(fetch, unique.values()))
    videos = [video for channel in results for tab in ("videos", "shorts") for video in channel.get(tab, [])]
    if videos:
        cached = database.get_youtube_video_metadata([video["video_id"] for video in videos])
        tasks = database.youtube_task_snapshot()
        for video in videos:
            metadata = cached.get(video["video_id"], {})
            video.update({
                "published_at": metadata.get("published_at"),
                "published_date": metadata.get("published_date"),
                "metadata_checked": bool(metadata.get("published_at") or metadata.get("published_date")),
                **tasks.get(video["video_id"], {}),
            })
    return results
