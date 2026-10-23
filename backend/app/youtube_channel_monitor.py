from __future__ import annotations

import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import requests

from . import database
from .adapters.ytdlp import DEFAULT_USER_AGENT
from .config import YOUTUBE_COOKIE_PATH
from .youtube import YOUTUBE_ID_RE


DEFAULT_CHECK_INTERVAL_MINUTES = 30
DEFAULT_LATEST_COUNT = 2
MIN_CHECK_INTERVAL_MINUTES = 5
MAX_CHECK_INTERVAL_MINUTES = 1440
MIN_LATEST_COUNT = 1
MAX_LATEST_COUNT = 20
POLL_SECONDS = 60
YOUTUBE_FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
ATOM_NS = "{http://www.w3.org/2005/Atom}"
YT_NS = "{http://www.youtube.com/xml/schemas/2015}"

_started = False
_lock = threading.Lock()


@dataclass(frozen=True)
class ChannelInfo:
    channel_id: str
    title: str
    channel_url: str


@dataclass(frozen=True)
class FeedEntry:
    video_id: str
    url: str
    title: str
    published_at: str | None


def normalize_check_interval_minutes(value: int | None) -> int:
    interval = value or DEFAULT_CHECK_INTERVAL_MINUTES
    if interval < MIN_CHECK_INTERVAL_MINUTES or interval > MAX_CHECK_INTERVAL_MINUTES:
        raise ValueError(
            f"check_interval_minutes must be between {MIN_CHECK_INTERVAL_MINUTES} and {MAX_CHECK_INTERVAL_MINUTES}."
        )
    return interval


def normalize_latest_count(value: int | None) -> int:
    latest_count = value or DEFAULT_LATEST_COUNT
    if latest_count < MIN_LATEST_COUNT or latest_count > MAX_LATEST_COUNT:
        raise ValueError(f"latest_count must be between {MIN_LATEST_COUNT} and {MAX_LATEST_COUNT}.")
    return latest_count


def _proxy_url() -> str:
    proxy_port = database.get_ytdlp_settings()["proxy_port"].strip()
    if proxy_port:
        return f"http://127.0.0.1:{proxy_port}"
    return ""


def _request_proxies() -> dict[str, str] | None:
    proxy = _proxy_url()
    if not proxy:
        return None
    return {"http": proxy, "https": proxy}


def _youtube_ydl_opts(*, noplaylist: bool) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": noplaylist,
        "js_runtimes": {"node": {}},
        "http_headers": {"User-Agent": DEFAULT_USER_AGENT},
    }
    if YOUTUBE_COOKIE_PATH.exists() and YOUTUBE_COOKIE_PATH.stat().st_size > 0:
        opts["cookiefile"] = str(YOUTUBE_COOKIE_PATH)
    proxy = _proxy_url()
    if proxy:
        opts["proxy"] = proxy
    return opts


def _channel_id_from_url(url: str) -> str | None:
    parsed = urlparse(url.strip())
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0] == "channel" and parts[1].startswith("UC"):
        return parts[1]
    return None


def resolve_channel_info(channel_url: str) -> ChannelInfo:
    cleaned = channel_url.strip()
    if not cleaned:
        raise ValueError("YouTube channel URL is required.")
    parsed = urlparse(cleaned)
    if "youtube.com" not in parsed.netloc.lower():
        raise ValueError("Only YouTube channel URLs are supported.")
    if parsed.path.strip("/").split("/", maxsplit=1)[0] in {"watch", "shorts", "embed", "live"}:
        raise ValueError("Use a YouTube channel URL, not a single-video URL.")

    direct_channel_id = _channel_id_from_url(cleaned)
    if direct_channel_id:
        return ChannelInfo(
            channel_id=direct_channel_id,
            title="",
            channel_url=f"https://www.youtube.com/channel/{direct_channel_id}",
        )

    import yt_dlp

    opts = {
        **_youtube_ydl_opts(noplaylist=False),
        "extract_flat": "in_playlist",
        "playlistend": 1,
        "skip_download": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(cleaned, download=False)
    channel_id = str(info.get("channel_id") or info.get("id") or "")
    if not channel_id.startswith("UC"):
        entries = info.get("entries") or []
        for entry in entries:
            candidate = str((entry or {}).get("channel_id") or "")
            if candidate.startswith("UC"):
                channel_id = candidate
                break
    if not channel_id.startswith("UC"):
        raise ValueError("Could not resolve YouTube channel id.")
    title = str(info.get("channel") or info.get("uploader") or info.get("title") or "").strip()
    return ChannelInfo(
        channel_id=channel_id,
        title=title,
        channel_url=f"https://www.youtube.com/channel/{channel_id}",
    )


def fetch_channel_feed(channel_id: str, limit: int) -> list[FeedEntry]:
    response = requests.get(
        YOUTUBE_FEED_URL.format(channel_id=channel_id),
        headers={"User-Agent": DEFAULT_USER_AGENT},
        proxies=_request_proxies(),
        timeout=15,
    )
    response.raise_for_status()
    root = ET.fromstring(response.content)
    entries: list[FeedEntry] = []
    for entry in root.findall(f"{ATOM_NS}entry"):
        video_id = (entry.findtext(f"{YT_NS}videoId") or "").strip()
        if not YOUTUBE_ID_RE.match(video_id):
            continue
        title = (entry.findtext(f"{ATOM_NS}title") or "").strip()
        published_at = (entry.findtext(f"{ATOM_NS}published") or "").strip() or None
        link_url = ""
        for link in entry.findall(f"{ATOM_NS}link"):
            if link.attrib.get("rel") == "alternate" and link.attrib.get("href"):
                link_url = link.attrib["href"]
                break
        entries.append(
            FeedEntry(
                video_id=video_id,
                url=link_url or f"https://www.youtube.com/watch?v={video_id}",
                title=title,
                published_at=published_at,
            )
        )
        if len(entries) >= limit:
            break
    return entries


def fetch_video_duration_seconds(url: str) -> int | None:
    import yt_dlp

    with yt_dlp.YoutubeDL(_youtube_ydl_opts(noplaylist=True)) as ydl:
        info = ydl.extract_info(url, download=False)
    duration = info.get("duration")
    if duration is None:
        return None
    try:
        return max(0, int(round(float(duration))))
    except (TypeError, ValueError):
        return None


def seed_monitor_seen(monitor: dict) -> int:
    entries = fetch_channel_feed(monitor["channel_id"], normalize_latest_count(monitor.get("latest_count")))
    for entry in entries:
        if database.has_seen_youtube_channel_video(monitor["id"], entry.video_id):
            continue
        database.mark_youtube_channel_video_seen(
            monitor_id=monitor["id"],
            video_id=entry.video_id,
            url=entry.url,
            title=entry.title,
            published_at=entry.published_at,
        )
    database.update_youtube_channel_monitor_check(monitor["id"], last_error=None)
    return len(entries)


def check_monitor(monitor: dict) -> dict[str, int]:
    latest_count = normalize_latest_count(monitor.get("latest_count"))
    entries = fetch_channel_feed(monitor["channel_id"], latest_count)
    added = 0
    seen = 0
    for entry in entries:
        if database.has_seen_youtube_channel_video(monitor["id"], entry.video_id):
            seen += 1
            continue
        duration_seconds: int | None = None
        try:
            duration_seconds = fetch_video_duration_seconds(entry.url)
        except Exception:
            duration_seconds = None
        if database.find_task_by_video_id(entry.video_id) is None:
            database.upsert_video_link_record(
                entry.url,
                record_id=entry.video_id,
                duration_seconds=duration_seconds,
            )
            added += 1
        database.mark_youtube_channel_video_seen(
            monitor_id=monitor["id"],
            video_id=entry.video_id,
            url=entry.url,
            title=entry.title,
            duration_seconds=duration_seconds,
            published_at=entry.published_at,
        )
    database.update_youtube_channel_monitor_check(monitor["id"], last_error=None)
    return {"checked": len(entries), "added": added, "seen": seen}


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_due(monitor: dict, now: datetime) -> bool:
    last_checked = _parse_iso(monitor.get("last_checked_at"))
    if last_checked is None:
        return True
    interval = normalize_check_interval_minutes(monitor.get("check_interval_minutes"))
    return now - last_checked >= timedelta(minutes=interval)


def run_due_checks_once() -> None:
    now = datetime.now(timezone.utc)
    for monitor in database.list_enabled_youtube_channel_monitors():
        if not _is_due(monitor, now):
            continue
        try:
            check_monitor(monitor)
        except Exception as exc:
            database.update_youtube_channel_monitor_check(monitor["id"], last_error=str(exc))


def _loop() -> None:
    while True:
        run_due_checks_once()
        time.sleep(POLL_SECONDS)


def start() -> None:
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, daemon=True, name="youtube-channel-monitor").start()
