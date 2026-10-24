from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import uuid
import json as _json
import time
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field, ValidationError

from . import bilibili_cover, bilibili_schedule, bilibili_uploads, database, watermark_masks, worker, youtube_channel_lookup, youtube_channel_monitor, youtube_video_metadata
from .adapters.local_subtitles import parse_srt, uploaded_subtitle_dir
from .adapters.local_video import remove_upload, uploaded_video_dir
from .adapters.openai_translate import list_models as list_openai_models
from .adapters.openai_translate import translate_title_to_chinese
from .config import WORKFOLDER, YOUTUBE_COOKIE_PATH, ensure_runtime_dirs
from .cut_intervals import default_cut_intervals, serialize_cut_intervals
from .pipeline import PipelineRunner, run_task
from .runtime_checks import validate_runtime_device
from .sanitize import sanitize_text
from .sources import detect_source
from .stage_reset import remove_stage_artifacts
from .stages import STAGE_NAMES
from .youtube import LOCAL_UPLOAD_DIRECTIONS, extract_video_id, is_local_upload_url

ALLOWED_VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".flv", ".wmv"}
ALLOWED_SUBTITLE_SUFFIXES = {".srt"}
ALLOWED_BILIBILI_COVER_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
LOCAL_UPLOAD_CHUNK_SIZE = 1024 * 1024
MAX_LOCAL_UPLOAD_BYTES = int(os.getenv("LOCAL_UPLOAD_MAX_BYTES", str(4 * 1024 * 1024 * 1024)))
MAX_LOCAL_SUBTITLE_BYTES = int(os.getenv("LOCAL_SUBTITLE_MAX_BYTES", str(20 * 1024 * 1024)))
MAX_BILIBILI_COVER_BYTES = int(os.getenv("BILIBILI_COVER_MAX_BYTES", str(20 * 1024 * 1024)))
BILIBILI_UPLOAD_VIDEO_CANDIDATES = (
    "video_final_trimmed.mp4",
    "video_final_with_cta.mp4",
    "video_final.mp4",
    "video_source.mp4",
)
_task_creation_lock = threading.Lock()


def mask_secret(value: str) -> str:
    if not value:
        return ""
    return "********"


def bilibili_upload_video_path(session_path: str) -> Path | None:
    if not session_path:
        return None
    media_dir = Path(session_path) / "media"
    for name in BILIBILI_UPLOAD_VIDEO_CANDIDATES:
        candidate = media_dir / name
        if candidate.is_file():
            return candidate
    return None


def concat_input_video_path(session_path: str) -> Path | None:
    media_dir = Path(session_path) / "media"
    for name in ("video_final.mp4", "video_source.mp4"):
        candidate = media_dir / name
        if candidate.is_file():
            return candidate
    return None


def trim_input_video_path(session_path: str) -> Path | None:
    media_dir = Path(session_path) / "media"
    for name in (
        "video_final_with_cta.mp4",
        "video_final.mp4",
        "video_source.mp4",
    ):
        candidate = media_dir / name
        if candidate.is_file():
            return candidate
    return None


class TaskCreate(BaseModel):
    url: str
    execution_mode: str = "auto"
    auto_bilibili_upload: bool = False
    bilibili_upload_title: str | None = None
    bilibili_publish_mode: str = "now"
    bilibili_dtime: str | None = None
    bilibili_upload_after: str | None = None


class VideoLinkRecordCreate(BaseModel):
    url: str


class YouTubeChannelMonitorCreate(BaseModel):
    channel_url: str
    enabled: bool = True
    check_interval_minutes: int = youtube_channel_monitor.DEFAULT_CHECK_INTERVAL_MINUTES
    latest_count: int = youtube_channel_monitor.DEFAULT_LATEST_COUNT


class YouTubeChannelMonitorUpdate(BaseModel):
    enabled: bool | None = None
    check_interval_minutes: int | None = None
    latest_count: int | None = None


class YouTubeAuthorLookupRequest(BaseModel):
    channel_urls: list[str] = Field(min_length=1, max_length=youtube_channel_lookup.MAX_CHANNELS)
    latest_count: int = Field(default=5, ge=1, le=youtube_channel_lookup.MAX_LATEST_COUNT)


class YouTubeVideoMetadataItem(BaseModel):
    video_id: str = Field(pattern=r"^[A-Za-z0-9_-]{11}$")
    channel_id: str | None = Field(default=None, pattern=r"^UC[A-Za-z0-9_-]{22}$")


class YouTubeVideoMetadataRequest(BaseModel):
    items: list[YouTubeVideoMetadataItem] = Field(min_length=1, max_length=12)


class BatchTaskCreate(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=100)
    execution_mode: str = "auto"
    auto_bilibili_upload: bool = False


class ContinueTaskRequest(BaseModel):
    execution_mode: str | None = None


class BilibiliUploadRequest(BaseModel):
    title: str
    description: str | None = None
    tags: str | None = None
    tid: int | None = None
    topic_name: str | None = None
    topic_id: int | None = None
    topic_mission_id: int | None = None
    season_id: int | None = None
    section_id: int | None = None
    publish_mode: str = "now"
    dtime: str | None = None
    upload_after: str | None = None


class BilibiliUploadSettingsRequest(BaseModel):
    auto_bilibili_upload: bool = False
    title: str | None = None
    description: str | None = None
    tags: str | None = None
    tid: int | None = None
    topic_name: str | None = None
    topic_id: int | None = None
    topic_mission_id: int | None = None
    season_id: int | None = None
    section_id: int | None = None
    publish_mode: str = "now"
    dtime: str | None = None
    upload_after: str | None = None


class BilibiliTitleTranslateRequest(BaseModel):
    title: str | None = None


class BilibiliBatchPreviewRequest(BaseModel):
    task_ids: list[str] = Field(min_length=1, max_length=500)
    start_date: str
    start_time: str = "00:00"
    daily_times: list[str] = Field(min_length=1, max_length=24)


class WatermarkMaskRequest(BaseModel):
    mode: str = "patch"


class CutInterval(BaseModel):
    start: float
    end: float


class CutIntervalsRequest(BaseModel):
    cut_intervals: list[CutInterval] = Field(default_factory=list)


class TrimVideoRequest(CutIntervalsRequest):
    source_video_path: str | None = None


class YouTubeCookieUpdate(BaseModel):
    content: str


class OpenAISettingsUpdate(BaseModel):
    base_url: str
    api_key: str = ""
    clear_api_key: bool = False
    model: str
    translate_concurrency: str = ""


class OpenAIModelsRequest(BaseModel):
    base_url: str = ""
    api_key: str = ""


class YtdlpSettingsUpdate(BaseModel):
    proxy_port: str = ""


class BilibiliSettingsUpdate(BaseModel):
    author_config_json: str = ""
    author_tags_json: str = ""
    author_tids_json: str = ""
    pokemon_topic_config_json: str = ""


class MailSettingsUpdate(BaseModel):
    enabled: bool = False
    smtp_host: str = ""
    smtp_port: str = "587"
    smtp_username: str = ""
    smtp_password: str = ""
    clear_smtp_password: bool = False
    from_address: str = ""
    to_addresses: str = ""
    smtp_security: str = "tls"
    notify_on_success: bool = True
    notify_on_failure: bool = True


def bool_setting(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def setting_bool(value: bool) -> str:
    return "true" if value else "false"


def normalize_smtp_port(value: str) -> str:
    smtp_port = value.strip() or "587"
    if not smtp_port.isdigit():
        raise HTTPException(status_code=422, detail="SMTP port must be numeric.")
    port = int(smtp_port)
    if port < 1 or port > 65535:
        raise HTTPException(status_code=422, detail="SMTP port must be between 1 and 65535.")
    return str(port)


def normalize_smtp_security(value: str) -> str:
    security = value.strip().lower() or "tls"
    if security not in {"none", "tls", "ssl"}:
        raise HTTPException(status_code=422, detail="SMTP security must be one of: none, tls, ssl.")
    return security


def normalize_mail_addresses(value: str) -> str:
    addresses = [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]
    for address in addresses:
        if "@" not in address or address.startswith("@") or address.endswith("@"):
            raise HTTPException(status_code=422, detail=f"Invalid email address: {address}")
    return ",".join(addresses)


def normalize_mail_sender(value: str) -> str:
    sender = value.strip()
    if sender and ("@" not in sender or sender.startswith("@") or sender.endswith("@")):
        raise HTTPException(status_code=422, detail="Invalid from address.")
    return sender


def normalize_proxy_port(value: str) -> str:
    proxy_port = value.strip()
    if not proxy_port:
        return ""
    if not proxy_port.isdigit():
        raise HTTPException(status_code=422, detail="Proxy port must be numeric.")
    port = int(proxy_port)
    if port < 1 or port > 65535:
        raise HTTPException(status_code=422, detail="Proxy port must be between 1 and 65535.")
    return str(port)


def normalize_cut_intervals(intervals: list[CutInterval]) -> list[dict[str, float]]:
    normalized: list[dict[str, float]] = []
    for interval in intervals:
        start = float(interval.start)
        end = float(interval.end)
        if start < 0 or end < 0:
            raise HTTPException(status_code=422, detail="Cut interval times must be non-negative.")
        if end <= start:
            raise HTTPException(status_code=422, detail="Cut interval end must be after start.")
        normalized.append({"start": round(start, 3), "end": round(end, 3)})
    normalized.sort(key=lambda item: item["start"])
    return normalized


def cut_intervals_file(session_path: str | None) -> Path | None:
    if not session_path:
        return None
    return Path(session_path) / "metadata" / "cut_intervals.json"


def sync_cut_intervals_to_session(task: dict, intervals: list[dict[str, float]]) -> None:
    intervals_file = cut_intervals_file(task.get("session_path"))
    if intervals_file is None:
        return
    intervals_file.parent.mkdir(parents=True, exist_ok=True)
    intervals_file.write_text(_json.dumps(intervals, ensure_ascii=False), encoding="utf-8")


def task_cut_intervals(task: dict) -> list[dict[str, float]]:
    saved = task.get("cut_intervals_json") or database.get_task_cut_intervals(task["id"])
    if saved:
        try:
            return normalize_cut_intervals([CutInterval(**item) for item in _json.loads(saved)])
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="Stored cut intervals are invalid.") from exc
    intervals_file = cut_intervals_file(task.get("session_path"))
    if intervals_file and intervals_file.exists():
        try:
            return normalize_cut_intervals([CutInterval(**item) for item in _json.loads(intervals_file.read_text(encoding="utf-8"))])
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="cut_intervals.json is invalid.") from exc
    return default_cut_intervals()


def normalize_translate_concurrency(value: str) -> str:
    concurrency = value.strip()
    if not concurrency:
        return ""
    if not all("0" <= char <= "9" for char in concurrency):
        raise HTTPException(status_code=422, detail="Translate concurrency must be numeric.")
    workers = int(concurrency)
    if workers < 1 or workers > 200:
        raise HTTPException(
            status_code=422, detail="Translate concurrency must be between 1 and 200."
        )
    return concurrency


def normalize_bilibili_author_tags_json(value: str) -> str:
    raw = value.strip()
    if not raw:
        return ""
    try:
        parsed = _json.loads(raw)
    except _json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"Bilibili author tags must be valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=422, detail="Bilibili author tags must be a JSON object.")
    normalized: dict[str, str] = {}
    for author, tags in parsed.items():
        if not isinstance(author, str) or not isinstance(tags, str):
            raise HTTPException(status_code=422, detail="Bilibili author tags keys and values must be strings.")
        key = author.strip()
        if key:
            normalized[key] = tags.strip()
    return _json.dumps(normalized, ensure_ascii=False)


def normalize_bilibili_author_tids_json(value: str) -> str:
    raw = value.strip()
    if not raw:
        return ""
    try:
        parsed = _json.loads(raw)
    except _json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"Bilibili author tids must be valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=422, detail="Bilibili author tids must be a JSON object.")
    normalized: dict[str, int] = {}
    for author, tid in parsed.items():
        if not isinstance(author, str):
            raise HTTPException(status_code=422, detail="Bilibili author tids keys must be strings.")
        if isinstance(tid, bool) or not isinstance(tid, int):
            raise HTTPException(status_code=422, detail="Bilibili author tids values must be integer category IDs.")
        if tid <= 0:
            raise HTTPException(status_code=422, detail="Bilibili author tids values must be greater than 0.")
        key = author.strip()
        if key:
            normalized[key] = tid
    return _json.dumps(normalized, ensure_ascii=False)


def normalize_bilibili_author_config_json(value: str) -> str:
    raw = value.strip()
    if not raw:
        return ""
    try:
        parsed = _json.loads(raw)
    except _json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"Bilibili author config must be valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=422, detail="Bilibili author config must be a JSON object.")
    normalized: dict[str, dict[str, str | int]] = {}
    for author, profile in parsed.items():
        if not isinstance(author, str):
            raise HTTPException(status_code=422, detail="Bilibili author config keys must be strings.")
        if not isinstance(profile, dict):
            raise HTTPException(status_code=422, detail="Bilibili author config values must be objects.")
        item: dict[str, str | int] = {}
        if "tags" in profile:
            tags = profile["tags"]
            if not isinstance(tags, str):
                raise HTTPException(status_code=422, detail="Bilibili author config tags values must be strings.")
            item["tags"] = tags.strip()
        if "tid" in profile:
            tid = profile["tid"]
            if isinstance(tid, bool) or not isinstance(tid, int):
                raise HTTPException(status_code=422, detail="Bilibili author config tid values must be integer category IDs.")
            if tid <= 0:
                raise HTTPException(status_code=422, detail="Bilibili author config tid values must be greater than 0.")
            item["tid"] = tid
        if "topic_name" in profile:
            topic_name = profile["topic_name"]
            if not isinstance(topic_name, str):
                raise HTTPException(status_code=422, detail="Bilibili author config topic_name values must be strings.")
            item["topic_name"] = topic_name.strip()
        for field in ("topic_id", "topic_mission_id", "season_id", "section_id"):
            if field not in profile:
                continue
            number = profile[field]
            if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
                raise HTTPException(status_code=422, detail=f"Bilibili author config {field} must be a positive integer.")
            item[field] = number
        if ("season_id" in item) != ("section_id" in item):
            raise HTTPException(status_code=422, detail="Bilibili author config season_id and section_id must be set together.")
        key = author.strip()
        if key:
            normalized[key] = item
    return _json.dumps(normalized, ensure_ascii=False)


def normalize_bilibili_pokemon_topic_config_json(value: str) -> str:
    raw = value.strip()
    if not raw:
        return ""
    try:
        parsed = _json.loads(raw)
    except _json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"Bilibili Pokemon topic config must be valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=422, detail="Bilibili Pokemon topic config must be a JSON object.")
    normalized: dict[str, int] = {}
    for topic_name, topic_id in parsed.items():
        if not isinstance(topic_name, str):
            raise HTTPException(status_code=422, detail="Bilibili Pokemon topic config keys must be strings.")
        if isinstance(topic_id, bool) or not isinstance(topic_id, int):
            raise HTTPException(status_code=422, detail="Bilibili Pokemon topic config values must be integer topic IDs.")
        if topic_id <= 0:
            raise HTTPException(status_code=422, detail="Bilibili Pokemon topic IDs must be greater than 0.")
        key = topic_name.strip()
        if key:
            normalized[key] = topic_id
    return _json.dumps(normalized, ensure_ascii=False)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_runtime_dirs()
    database.init_db()
    database.backfill_titles_from_metadata()
    database.fail_stale_active_tasks()
    database.fail_stale_running_bilibili_upload_jobs()
    database.fail_stale_running_watermark_mask_jobs()
    worker.start(run_task)
    bilibili_uploads.start()
    watermark_masks.start()
    youtube_channel_monitor.start()
    yield


app = FastAPI(title="YouDub API", lifespan=lifespan)


DEFAULT_CORS_ORIGIN_REGEX = (
    r"^https?://("
    r"localhost|"
    r"127(?:\.\d{1,3}){3}|"
    r"0\.0\.0\.0|"
    r"10(?:\.\d{1,3}){3}|"
    r"192\.168(?:\.\d{1,3}){2}|"
    r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}|"
    r"100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])(?:\.\d{1,3}){2}|"
    r"\[::1\]"
    r"):3000$"
)


def cors_origins() -> list[str]:
    defaults = ["http://localhost:3000", "http://127.0.0.1:3000"]
    configured = os.getenv("CORS_ALLOW_ORIGINS", "")
    extra = [origin.strip() for origin in configured.split(",") if origin.strip()]
    return [*defaults, *extra]


def cors_origin_regex() -> str:
    configured = os.getenv("CORS_ALLOW_ORIGIN_REGEX", "").strip()
    return configured or DEFAULT_CORS_ORIGIN_REGEX


app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_origin_regex=cors_origin_regex(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _metadata_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _metadata_number(value: object) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _bilibili_publish_info(task: dict) -> dict | None:
    session_path = task.get("session_path")
    if not session_path:
        return None
    session = Path(session_path)
    metadata_path = session / "metadata" / "ytdlp_info.json"
    if not metadata_path.is_file():
        return None
    try:
        info = _json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError):
        return None
    if not isinstance(info, dict):
        return None

    from scripts.upload_bilibili import (
        DEFAULT_TID,
        author_name,
        author_upload_config,
        build_desc,
    )

    author = author_name(info)
    settings = database.get_bilibili_settings()
    profiles = author_upload_config(
        settings["author_config_json"],
        legacy_tags_json=settings["author_tags_json"],
        legacy_tids_json=settings["author_tids_json"],
    )
    profile = profiles.get(author, {})
    tags = str(profile.get("tags") or "")
    tid = int(profile.get("tid") or DEFAULT_TID)
    return {
        "source_title": str(info.get("title") or "").strip() or None,
        "source_description": str(info.get("description") or "").strip() or None,
        "source_url": str(info.get("webpage_url") or info.get("original_url") or "").strip() or None,
        "source_upload_date": str(info.get("upload_date") or "").strip() or None,
        "author": author or None,
        "author_id": str(info.get("uploader_id") or info.get("channel_id") or "").strip() or None,
        "author_url": str(info.get("uploader_url") or info.get("channel_url") or "").strip() or None,
        "duration_seconds": _metadata_number(info.get("duration")),
        "view_count": _metadata_number(info.get("view_count")),
        "like_count": _metadata_number(info.get("like_count")),
        "comment_count": _metadata_number(info.get("comment_count")),
        "source_categories": _metadata_string_list(info.get("categories")),
        "source_tags": _metadata_string_list(info.get("tags")),
        "publish_description": build_desc(info),
        "publish_tags": [item.strip() for item in tags.split(",") if item.strip()],
        "publish_tid": tid,
        "cover_available": (session / "cover.jpg").is_file(),
    }


def _task_with_source(task: dict | None, *, include_publish_info: bool = False) -> dict | None:
    if not task:
        return None
    result = dict(task)
    result["auto_bilibili_upload"] = bool(result.get("auto_bilibili_upload"))
    result["bilibili_publish_mode"] = result.get("bilibili_publish_mode") or "now"
    display_video = bilibili_upload_video_path(result.get("session_path") or "")
    result["display_video_path"] = str(display_video) if display_video else None
    try:
        result["source_name"] = detect_source(result["url"]).name
    except ValueError:
        result["source_name"] = "unknown"
    if include_publish_info:
        result["bilibili_publish_info"] = _bilibili_publish_info(result)
    return result


def _video_link_record_with_source(record: dict) -> dict:
    result = dict(record)
    try:
        result["source_name"] = detect_source(result["url"]).name
    except ValueError:
        result["source_name"] = "unknown"
    return result


def _youtube_channel_monitor_response(monitor: dict) -> dict:
    result = dict(monitor)
    result["enabled"] = bool(result.get("enabled"))
    return result


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _ensure_runtime_ready() -> None:
    try:
        validate_runtime_device()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def normalize_execution_mode(value: str) -> str:
    try:
        return database.normalize_execution_mode(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def normalize_video_link_url(url: str) -> tuple[str, str]:
    cleaned = url.strip()
    if not cleaned:
        raise HTTPException(status_code=422, detail="Video URL is required.")
    try:
        video_id = extract_video_id(cleaned)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return cleaned, video_id


@app.get("/api/video-links")
def list_video_link_records(limit: int = 100) -> dict:
    return {
        "video_links": [
            _video_link_record_with_source(record)
            for record in database.list_video_link_records(limit=limit)
        ]
    }


@app.post("/api/video-links", status_code=201)
def create_video_link_record(payload: VideoLinkRecordCreate) -> dict:
    url, video_id = normalize_video_link_url(payload.url)
    return _video_link_record_with_source(database.upsert_video_link_record(url, record_id=video_id))


@app.delete("/api/video-links/{record_id}", status_code=204)
def delete_video_link_record(record_id: str) -> Response:
    if not database.delete_video_link_record(record_id):
        raise HTTPException(status_code=404, detail="Video link record not found.")
    return Response(status_code=204)


def normalize_monitor_interval(value: int | None) -> int:
    try:
        return youtube_channel_monitor.normalize_check_interval_minutes(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def normalize_monitor_latest_count(value: int | None) -> int:
    try:
        return youtube_channel_monitor.normalize_latest_count(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/youtube-authors/lookup")
def lookup_youtube_authors(payload: YouTubeAuthorLookupRequest) -> dict:
    return {"channels": youtube_channel_lookup.lookup_authors(payload.channel_urls, payload.latest_count)}


@app.post("/api/youtube-videos/metadata")
def youtube_videos_metadata(payload: YouTubeVideoMetadataRequest) -> dict:
    return {"videos": youtube_video_metadata.fetch_video_metadata([item.model_dump() for item in payload.items])}


@app.get("/api/youtube-videos/{video_id}/thumbnail")
def youtube_video_thumbnail(video_id: str) -> FileResponse:
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise HTTPException(status_code=422, detail="Invalid YouTube video ID.")
    try:
        path = youtube_video_metadata.thumbnail_path(video_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Thumbnail unavailable.") from exc
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/youtube-channel-monitors")
def list_youtube_channel_monitors() -> dict:
    return {
        "monitors": [
            _youtube_channel_monitor_response(monitor)
            for monitor in database.list_youtube_channel_monitors()
        ]
    }


@app.post("/api/youtube-channel-monitors", status_code=201)
def create_youtube_channel_monitor(payload: YouTubeChannelMonitorCreate) -> dict:
    try:
        channel = youtube_channel_monitor.resolve_channel_info(payload.channel_url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to resolve YouTube channel: {exc}") from exc

    monitor = database.create_youtube_channel_monitor(
        channel_url=channel.channel_url,
        channel_id=channel.channel_id,
        title=channel.title,
        enabled=payload.enabled,
        check_interval_minutes=normalize_monitor_interval(payload.check_interval_minutes),
        latest_count=normalize_monitor_latest_count(payload.latest_count),
    )
    try:
        youtube_channel_monitor.seed_monitor_seen(monitor)
    except Exception as exc:
        database.update_youtube_channel_monitor_check(monitor["id"], last_error=str(exc))
    return _youtube_channel_monitor_response(database.get_youtube_channel_monitor(monitor["id"]) or monitor)


@app.patch("/api/youtube-channel-monitors/{monitor_id}")
def update_youtube_channel_monitor(monitor_id: str, payload: YouTubeChannelMonitorUpdate) -> dict:
    if not database.get_youtube_channel_monitor(monitor_id):
        raise HTTPException(status_code=404, detail="YouTube channel monitor not found.")
    fields: dict[str, object] = {}
    if payload.enabled is not None:
        fields["enabled"] = 1 if payload.enabled else 0
    if payload.check_interval_minutes is not None:
        fields["check_interval_minutes"] = normalize_monitor_interval(payload.check_interval_minutes)
    if payload.latest_count is not None:
        fields["latest_count"] = normalize_monitor_latest_count(payload.latest_count)
    monitor = database.update_youtube_channel_monitor(monitor_id, **fields)
    return _youtube_channel_monitor_response(monitor)


@app.delete("/api/youtube-channel-monitors/{monitor_id}", status_code=204)
def delete_youtube_channel_monitor(monitor_id: str) -> Response:
    if not database.delete_youtube_channel_monitor(monitor_id):
        raise HTTPException(status_code=404, detail="YouTube channel monitor not found.")
    return Response(status_code=204)


@app.post("/api/youtube-channel-monitors/{monitor_id}/check")
def check_youtube_channel_monitor(monitor_id: str) -> dict:
    monitor = database.get_youtube_channel_monitor(monitor_id)
    if not monitor:
        raise HTTPException(status_code=404, detail="YouTube channel monitor not found.")
    try:
        result = youtube_channel_monitor.check_monitor(monitor)
    except Exception as exc:
        database.update_youtube_channel_monitor_check(monitor_id, last_error=str(exc))
        raise HTTPException(status_code=502, detail=f"Failed to check YouTube channel: {exc}") from exc
    updated = database.get_youtube_channel_monitor(monitor_id) or monitor
    return {"monitor": _youtube_channel_monitor_response(updated), **result}


@app.post("/api/tasks", status_code=201)
def create_task(payload: TaskCreate) -> dict:
    return _create_task_once(payload)[0]


@app.post("/api/tasks/batch")
def create_tasks_batch(payload: BatchTaskCreate) -> dict:
    mode = normalize_execution_mode(payload.execution_mode)
    results = []
    for url in payload.urls:
        try:
            task, created = _create_task_once(TaskCreate(
                url=url, execution_mode=mode, auto_bilibili_upload=payload.auto_bilibili_upload,
            ))
            results.append({"url": url, "video_id": extract_video_id(url), "task_id": task["id"],
                            "task_status": task["status"], "status": "created" if created else "existing", "error": None})
        except Exception as exc:
            error = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
            results.append({"url": url, "status": "failed", "task_id": None, "task_status": None, "error": error})
    return {"results": results, **{status: sum(item["status"] == status for item in results)
                                   for status in ("created", "existing", "failed")}}


def _create_task_once(payload: TaskCreate) -> tuple[dict, bool]:
    # Serialize the check/insert/enqueue sequence, including concurrent single-task requests.
    with _task_creation_lock:
        return _create_task_impl(payload)


def _create_task_impl(payload: TaskCreate) -> tuple[dict, bool]:
    url, video_id = normalize_video_link_url(payload.url)

    existing_id = database.find_task_by_video_id(video_id)
    if existing_id:
        database.mark_video_link_record_task(video_id, existing_id)
        return _task_with_source(database.get_task(existing_id)), False

    _ensure_runtime_ready()
    publish_mode, dtime, upload_after = _normalize_bilibili_upload_settings(
        payload.bilibili_publish_mode,
        payload.bilibili_dtime,
        payload.bilibili_upload_after,
    )
    task_id = database.create_task(
        url,
        task_id=video_id,
        execution_mode=normalize_execution_mode(payload.execution_mode),
        auto_bilibili_upload=payload.auto_bilibili_upload,
        bilibili_upload_title=(payload.bilibili_upload_title or "").strip() or None,
        bilibili_publish_mode=publish_mode,
        bilibili_dtime=dtime,
        bilibili_upload_after=upload_after,
    )
    database.mark_video_link_record_task(video_id, task_id)
    worker.enqueue(task_id)
    return _task_with_source(database.get_task(task_id)), True


def _clean_upload_filename(filename: str | None) -> str:
    original = Path(filename or "").name.strip()
    if not original:
        raise HTTPException(status_code=422, detail="Video filename is required.")
    suffix = Path(original).suffix.lower()
    if suffix not in ALLOWED_VIDEO_SUFFIXES:
        raise HTTPException(status_code=422, detail="Unsupported video file type.")
    safe_stem = sanitize_text(Path(original).stem) or "video"
    return f"{safe_stem}{suffix}"


def _clean_subtitle_filename(filename: str | None) -> str:
    original = Path(filename or "").name.strip()
    if not original:
        raise HTTPException(status_code=422, detail="Subtitle filename is required.")
    suffix = Path(original).suffix.lower()
    if suffix not in ALLOWED_SUBTITLE_SUFFIXES:
        raise HTTPException(status_code=422, detail="Only .srt subtitle files are supported.")
    safe_stem = sanitize_text(Path(original).stem) or "subtitles"
    return f"{safe_stem}{suffix}"


def _bilibili_cover_suffix(filename: str | None) -> str:
    original = Path(filename or "").name.strip()
    if not original:
        raise HTTPException(status_code=422, detail="Cover filename is required.")
    suffix = Path(original).suffix.lower()
    if suffix not in ALLOWED_BILIBILI_COVER_SUFFIXES:
        raise HTTPException(status_code=422, detail="Only .jpg, .jpeg, .png, or .webp cover images are supported.")
    return suffix


def _task_session_path(task_id: str) -> tuple[dict, Path]:
    task = database.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    session_path = task.get("session_path")
    if not session_path:
        raise HTTPException(status_code=400, detail="Task has no session path; run the download stage first.")
    session = Path(session_path)
    if not session.is_dir():
        raise HTTPException(status_code=404, detail="Task session directory not found.")
    return task, session


def _image_media_type(path: Path) -> str:
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(path.suffix.lower(), "application/octet-stream")


def _save_uploaded_file(file: UploadFile, destination: Path, *, max_bytes: int, too_large_detail: str) -> int:
    total = 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as handle:
        while True:
            chunk = file.file.read(LOCAL_UPLOAD_CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                destination.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail=too_large_detail)
            handle.write(chunk)
    if total == 0:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="Uploaded file is empty.")
    return total


def _validate_uploaded_srt(path: Path) -> None:
    try:
        parse_srt(path.read_text(encoding="utf-8-sig"))
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid SRT subtitle file encoding.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid SRT subtitle file: {exc}") from exc


@app.post("/api/tasks/upload", status_code=201)
def upload_local_video(
    direction: str = Form("en-zh"),
    file: UploadFile = File(...),
    subtitle_file: UploadFile | None = File(None),
    execution_mode: str = Form("auto"),
    auto_bilibili_upload: bool = Form(False),
) -> dict:
    if direction not in LOCAL_UPLOAD_DIRECTIONS:
        raise HTTPException(status_code=422, detail="Unsupported local video direction.")

    _ensure_runtime_ready()
    original_name = Path(file.filename or "").name.strip()
    stored_name = _clean_upload_filename(original_name)
    task_id = str(uuid.uuid4())
    try:
        _save_uploaded_file(
            file,
            uploaded_video_dir(WORKFOLDER, task_id) / stored_name,
            max_bytes=MAX_LOCAL_UPLOAD_BYTES,
            too_large_detail="Uploaded video is too large.",
        )
        if subtitle_file is not None and subtitle_file.filename:
            subtitle_name = _clean_subtitle_filename(subtitle_file.filename)
            subtitle_path = uploaded_subtitle_dir(WORKFOLDER, task_id) / subtitle_name
            _save_uploaded_file(
                subtitle_file,
                subtitle_path,
                max_bytes=MAX_LOCAL_SUBTITLE_BYTES,
                too_large_detail="Uploaded subtitle is too large.",
            )
            _validate_uploaded_srt(subtitle_path)
    except HTTPException:
        remove_upload(WORKFOLDER, task_id)
        raise

    url = f"local://upload/{task_id}?direction={direction}&filename={quote(original_name)}"
    database.create_task(
        url,
        task_id=task_id,
        execution_mode=normalize_execution_mode(execution_mode),
        auto_bilibili_upload=auto_bilibili_upload,
    )
    database.update_task(task_id, title=Path(original_name).stem)
    worker.enqueue(task_id)
    return _task_with_source(database.get_task(task_id))


@app.get("/api/tasks/current")
def current_task() -> dict | None:
    return _task_with_source(database.get_current_task())


@app.get("/api/tasks")
def list_tasks(limit: int = 100) -> dict:
    return {"tasks": [_task_with_source(task) for task in database.list_tasks(limit=limit)]}


@app.get("/api/tasks/{task_id}")
def task_detail(task_id: str) -> dict:
    task = database.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    return _task_with_source(task, include_publish_info=True)


def _open_directory(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


@app.post("/api/tasks/{task_id}/open-folder")
def open_task_folder(task_id: str) -> dict:
    task = database.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    session_path = task.get("session_path")
    if not session_path:
        raise HTTPException(status_code=400, detail="Task has no session directory yet.")
    session_dir = Path(session_path)
    if not session_dir.is_dir():
        raise HTTPException(status_code=404, detail="Task session directory not found.")
    try:
        _open_directory(session_dir)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to open task directory: {exc}") from exc
    return {"path": str(session_dir)}


def _is_inside_workfolder(path: Path) -> bool:
    workfolder = WORKFOLDER.resolve()
    try:
        path.resolve().relative_to(workfolder)
    except ValueError:
        return False
    return True


def _purge_task(task: dict) -> None:
    if database.get_active_bilibili_upload_job(task["id"]):
        raise HTTPException(status_code=409, detail="该任务已有待上传或上传中的投稿，请先取消排期或等待上传完成。")
    session_path = task.get("session_path")
    if session_path:
        session_dir = Path(session_path)
        if session_dir.exists() and _is_inside_workfolder(session_dir):
            shutil.rmtree(session_dir)
    log_file = database.log_path(task["id"])
    if log_file.exists():
        log_file.unlink()
    database.delete_task(task["id"])


@app.delete("/api/tasks/{task_id}", status_code=204)
def delete_task(task_id: str) -> Response:
    task = database.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    if task["status"] == "running":
        raise HTTPException(status_code=409, detail="Cannot delete a running task.")
    _purge_task(task)
    if is_local_upload_url(task["url"]):
        remove_upload(WORKFOLDER, task["id"])
    return Response(status_code=204)


@app.post("/api/tasks/{task_id}/rerun")
def rerun_task(task_id: str) -> dict:
    task = database.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    if task["status"] == "running":
        raise HTTPException(status_code=409, detail="Cannot rerun a running task.")

    _ensure_runtime_ready()
    url = task["url"]
    execution_mode = task.get("execution_mode") or database.DEFAULT_EXECUTION_MODE
    auto_bilibili_upload = bool(task.get("auto_bilibili_upload"))
    _purge_task(task)
    new_id = database.create_task(
        url,
        task_id=task_id,
        execution_mode=execution_mode,
        auto_bilibili_upload=auto_bilibili_upload,
    )
    worker.enqueue(new_id)
    return _task_with_source(database.get_task(new_id))


@app.post("/api/tasks/{task_id}/stages/{stage_name}/redo")
def redo_stage(task_id: str, stage_name: str) -> dict:
    if stage_name not in STAGE_NAMES:
        raise HTTPException(status_code=404, detail="Stage not found.")
    task = database.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    if (task.get("execution_mode") or database.DEFAULT_EXECUTION_MODE) != "manual":
        raise HTTPException(status_code=409, detail="Only manual tasks support per-stage redo.")
    if task["status"] in {"running", "queued"}:
        raise HTTPException(status_code=409, detail="Task is already running or queued.")
    stage = next((item for item in task["stages"] if item["name"] == stage_name), None)
    if not stage:
        raise HTTPException(status_code=404, detail="Stage not found.")
    if stage["status"] not in {"succeeded", "failed"}:
        raise HTTPException(status_code=409, detail="Only completed or failed stages can be redone.")
    _ensure_runtime_ready()
    session_path = task.get("session_path")
    if session_path:
        remove_stage_artifacts(Path(session_path), stage_name, detect_source(task["url"]))
    database.reset_stages_from(task_id, stage_name)
    worker.enqueue(task_id)
    return _task_with_source(database.get_task(task_id))


@app.get("/api/tasks/{task_id}/cut-intervals")
def get_cut_intervals(task_id: str) -> dict:
    task = database.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    return {"cut_intervals": task_cut_intervals(task)}


@app.put("/api/tasks/{task_id}/cut-intervals")
def save_cut_intervals(task_id: str, payload: CutIntervalsRequest) -> dict:
    task = database.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    intervals = normalize_cut_intervals(payload.cut_intervals)
    serialized = _json.dumps(intervals, ensure_ascii=False)
    database.save_task_cut_intervals(task_id, serialized)
    sync_cut_intervals_to_session(task, intervals)
    return {"cut_intervals": intervals}


def _trim_source_video_from_request(session: Path, source_video_path: str | None) -> Path:
    raw = (source_video_path or "").strip()
    if not raw:
        default_input = trim_input_video_path(str(session))
        if not default_input:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Task has no video_final_with_cta.mp4, video_final.mp4, "
                    "or video_source.mp4 to trim."
                ),
            )
        return default_input

    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        session_candidate = session / candidate
        candidate = session_candidate if session_candidate.is_file() else candidate
    if not candidate.is_file():
        raise HTTPException(status_code=400, detail="Trim source video not found.")
    if candidate.suffix.lower() not in ALLOWED_VIDEO_SUFFIXES:
        raise HTTPException(status_code=422, detail="Trim source video must be a supported video file.")
    return candidate


def _save_trim_source_video(session: Path, file: UploadFile) -> Path:
    stored_name = _clean_upload_filename(file.filename)
    destination = session / "tmp" / "trim_sources" / f"{uuid.uuid4()}{Path(stored_name).suffix.lower()}"
    _save_uploaded_file(
        file,
        destination,
        max_bytes=MAX_LOCAL_UPLOAD_BYTES,
        too_large_detail="Trim source video is too large.",
    )
    return destination


def _prepare_trim_input_for_output(input_video: Path, output_video: Path, session: Path) -> Path:
    if not output_video.exists() or input_video.resolve() != output_video.resolve():
        return input_video
    source_copy = session / "tmp" / "trim_sources" / f"{uuid.uuid4()}{input_video.suffix.lower()}"
    source_copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(input_video, source_copy)
    return source_copy


def _save_fast_trim_intervals(task_id: str, task: dict, session: Path, payload: CutIntervalsRequest | None) -> list[dict[str, float]]:
    metadata_dir = session / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    intervals_file = metadata_dir / "cut_intervals.json"

    if payload is not None:
        intervals = normalize_cut_intervals(payload.cut_intervals)
        serialized = serialize_cut_intervals(intervals)
        database.save_task_cut_intervals(task_id, serialized)
        intervals_file.write_text(serialized, encoding="utf-8")
    else:
        intervals = task_cut_intervals(task)
        if intervals:
            serialized = serialize_cut_intervals(intervals)
            database.save_task_cut_intervals(task_id, serialized)
            intervals_file.write_text(serialized, encoding="utf-8")

    if not intervals_file.exists():
        raise HTTPException(status_code=400, detail="No cut intervals provided and cut_intervals.json not found.")
    return intervals


def _run_fast_trim(task_id: str, task: dict, session: Path, payload: CutIntervalsRequest | None) -> Path:
    intervals = _save_fast_trim_intervals(task_id, task, session, payload)

    input_video = trim_input_video_path(str(session))
    if not input_video:
        raise HTTPException(
            status_code=400,
            detail=(
                "Task has no video_final_with_cta.mp4, video_final.mp4, "
                "or video_source.mp4 to trim."
            ),
        )

    trimmed = session / "media" / "video_final_trimmed.mp4"
    input_video = _prepare_trim_input_for_output(input_video, trimmed, session)
    if trimmed.exists():
        trimmed.unlink()

    _ensure_runtime_ready()
    database.update_task(task_id, current_stage="trim_video")
    database.update_stage(
        task_id,
        "trim_video",
        status="running",
        progress=None,
        started_at=database.now_iso(),
        completed_at=None,
        error_message=None,
        last_message="Fast trim started",
    )
    try:
        from .adapters.ffmpeg import fast_trim_video

        trimmed_video = fast_trim_video(input_video, intervals, session)
        size_mb = trimmed_video.stat().st_size / (1024 * 1024)
        database.update_stage(
            task_id,
            "trim_video",
            status="succeeded",
            progress=100,
            completed_at=database.now_iso(),
            error_message=None,
            last_message=f"Fast trimmed video: {trimmed_video} ({size_mb:.1f} MB)",
        )
        return trimmed_video
    except Exception as exc:
        database.update_stage(
            task_id,
            "trim_video",
            status="failed",
            completed_at=database.now_iso(),
            error_message=str(exc),
            last_message="Fast trim failed",
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/tasks/{task_id}/trim")
async def trim_video_task(task_id: str, request: Request) -> dict:
    payload: TrimVideoRequest | None
    source_video_file: UploadFile | None = None
    content_type = request.headers.get("content-type", "").lower()
    if content_type.startswith("multipart/form-data") or content_type.startswith("application/x-www-form-urlencoded"):
        form = await request.form()
        raw_intervals = str(form.get("cut_intervals") or "[]")
        try:
            intervals_payload = _json.loads(raw_intervals)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="cut_intervals must be valid JSON.") from exc
        payload = TrimVideoRequest(
            cut_intervals=intervals_payload,
            source_video_path=str(form.get("source_video_path") or "") or None,
        )
        maybe_source = form.get("source_video_file")
        if maybe_source is not None and getattr(maybe_source, "filename", ""):
            source_video_file = maybe_source  # type: ignore[assignment]
    else:
        raw_body = await request.body()
        if raw_body.strip():
            try:
                payload = TrimVideoRequest(**_json.loads(raw_body))
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="Request body must be valid JSON.") from exc
            except ValidationError as exc:
                raise HTTPException(status_code=422, detail=exc.errors()) from exc
        else:
            payload = None

    task = database.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    if task["status"] in {"running", "queued"}:
        raise HTTPException(status_code=409, detail="Task is already running or queued.")
    session_path = task.get("session_path")
    if not session_path:
        raise HTTPException(status_code=400, detail="Task has no session path; run the pipeline first.")

    session = Path(session_path)
    metadata_dir = session / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    intervals_file = metadata_dir / "cut_intervals.json"

    if payload is not None:
        intervals = normalize_cut_intervals(payload.cut_intervals)
        serialized = serialize_cut_intervals(intervals)
        database.save_task_cut_intervals(task_id, serialized)
        intervals_file.write_text(serialized, encoding="utf-8")
    else:
        intervals = task_cut_intervals(task)
        if intervals:
            serialized = serialize_cut_intervals(intervals)
            database.save_task_cut_intervals(task_id, serialized)
            intervals_file.write_text(serialized, encoding="utf-8")

    if not intervals_file.exists():
        raise HTTPException(status_code=400, detail="No cut intervals provided and cut_intervals.json not found.")

    trimmed = session / "media" / "video_final_trimmed.mp4"

    _ensure_runtime_ready()
    input_video = (
        _save_trim_source_video(session, source_video_file)
        if source_video_file
        else _trim_source_video_from_request(session, payload.source_video_path if payload else None)
    )
    input_video = _prepare_trim_input_for_output(input_video, trimmed, session)
    if trimmed.exists():
        trimmed.unlink()
    database.update_stage(
        task_id,
        "trim_video",
        status="pending",
        progress=None,
        started_at=None,
        completed_at=None,
        error_message=None,
        last_message=None,
    )
    try:
        runner = PipelineRunner(task_id)
        runner.artifacts.session = session
        runner.artifacts.trim_source_video = input_video
        runner._run_stage("trim_video")
        trimmed_video = runner.artifacts.trimmed_video
        if not trimmed_video:
            raise RuntimeError("Trim stage did not produce a video.")
        database.update_task(task_id, final_video_path=str(trimmed_video), error_message=None)
    except Exception as exc:
        database.update_stage(
            task_id,
            "trim_video",
            status="failed",
            completed_at=database.now_iso(),
            error_message=str(exc),
            last_message="Failed",
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _task_with_source(database.get_task(task_id))


@app.post("/api/tasks/{task_id}/trim/fast")
def fast_trim_video_task(task_id: str, payload: CutIntervalsRequest | None = None) -> dict:
    task = database.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    if task["status"] in {"running", "queued"}:
        raise HTTPException(status_code=409, detail="Task is already running or queued.")
    session_path = task.get("session_path")
    if not session_path:
        raise HTTPException(status_code=400, detail="Task has no session path; run the pipeline first.")

    session = Path(session_path)
    trimmed_video = _run_fast_trim(task_id, task, session, payload)
    database.update_task(task_id, final_video_path=str(trimmed_video), error_message=None)
    return _task_with_source(database.get_task(task_id))


@app.post("/api/tasks/{task_id}/trim/fast-replace-source")
def fast_trim_replace_source_task(task_id: str, payload: CutIntervalsRequest | None = None) -> dict:
    task = database.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    if task["status"] in {"running", "queued"}:
        raise HTTPException(status_code=409, detail="Task is already running or queued.")
    session_path = task.get("session_path")
    if not session_path:
        raise HTTPException(status_code=400, detail="Task has no session path; run the pipeline first.")

    session = Path(session_path)
    source_video = session / "media" / "video_source.mp4"
    trimmed_video = _run_fast_trim(task_id, task, session, payload)
    try:
        source_video.parent.mkdir(parents=True, exist_ok=True)
        if source_video.exists():
            source_video.unlink()
        trimmed_video.replace(source_video)
        size_mb = source_video.stat().st_size / (1024 * 1024)
        database.update_stage(
            task_id,
            "trim_video",
            status="succeeded",
            progress=100,
            completed_at=database.now_iso(),
            error_message=None,
            last_message=f"Fast trim replaced source video: {source_video} ({size_mb:.1f} MB)",
        )
        database.update_task(task_id, final_video_path=str(source_video), error_message=None)
    except Exception as exc:
        database.update_stage(
            task_id,
            "trim_video",
            status="failed",
            completed_at=database.now_iso(),
            error_message=str(exc),
            last_message="Fast trim source replacement failed",
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _task_with_source(database.get_task(task_id))


@app.post("/api/tasks/{task_id}/concat")
def concat_video_task(task_id: str) -> dict:
    task = database.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    if task["status"] in {"running", "queued"}:
        raise HTTPException(status_code=409, detail="Task is already running or queued.")
    session_path = task.get("session_path")
    if not session_path:
        raise HTTPException(status_code=400, detail="Task has no session path; run the pipeline first.")

    session = Path(session_path)
    input_video = concat_input_video_path(session_path)
    if not input_video:
        raise HTTPException(status_code=400, detail="Task has no final or source video to concatenate.")

    _ensure_runtime_ready()
    source = detect_source(task["url"])
    remove_stage_artifacts(session, "concat_video", source)
    database.update_stage(
        task_id,
        "trim_video",
        status="pending",
        progress=None,
        started_at=None,
        completed_at=None,
        error_message=None,
        last_message=None,
    )
    try:
        runner = PipelineRunner(task_id)
        runner.artifacts.session = session
        runner._run_stage("concat_video")
        concatenated = runner.artifacts.concatenated_video
        if not concatenated:
            raise RuntimeError("Concat stage did not produce a video.")
        database.update_task(task_id, final_video_path=str(concatenated))
    except Exception as exc:
        database.update_stage(
            task_id,
            "concat_video",
            status="failed",
            completed_at=database.now_iso(),
            error_message=str(exc),
            last_message="Failed",
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _task_with_source(database.get_task(task_id))


def _normalize_bilibili_publish_mode(value: str) -> str:
    mode = value.strip().lower()
    if mode not in {"now", "scheduled"}:
        raise HTTPException(status_code=422, detail="publish_mode must be one of: now, scheduled")
    return mode


def _normalize_bilibili_upload_after(value: str | None) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        if len(raw) == 10 and raw.isdigit():
            timestamp = int(raw)
        else:
            normalized = raw.replace("Z", "+00:00")
            parsed: datetime | None = None
            try:
                parsed = datetime.fromisoformat(normalized)
            except ValueError:
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                    try:
                        parsed = datetime.strptime(raw, fmt)
                        break
                    except ValueError:
                        continue
            if parsed is None:
                raise ValueError
            if parsed.tzinfo is None:
                timestamp = int(parsed.timestamp())
            else:
                timestamp = int(parsed.astimezone(timezone.utc).timestamp())
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="Scheduled upload time must be a datetime or 10-digit Unix timestamp.",
        ) from exc
    if timestamp <= int(time.time()):
        raise HTTPException(status_code=422, detail="Scheduled upload time must be in the future.")
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds")


def _normalize_bilibili_upload_settings(
    publish_mode_value: str,
    dtime_value: str | None,
    upload_after_value: str | None,
) -> tuple[str, str | None, str | None]:
    publish_mode = _normalize_bilibili_publish_mode(publish_mode_value)
    dtime = dtime_value.strip() if dtime_value else None
    upload_after = _normalize_bilibili_upload_after(upload_after_value)
    if publish_mode == "scheduled":
        if not dtime:
            raise HTTPException(status_code=422, detail="Scheduled publish time is required.")
        from scripts.upload_bilibili import UploadBilibiliError, parse_dtime

        try:
            parse_dtime(dtime)
        except UploadBilibiliError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    else:
        dtime = None
    return publish_mode, dtime, upload_after


def _normalize_bilibili_upload_content(
    task: dict,
    *,
    description: str | None,
    tags: str | None,
    tid: int | None,
) -> tuple[str, str, int]:
    defaults = _bilibili_publish_info(task) or {}
    if description is None:
        saved_description = task.get("bilibili_upload_description")
        description = (
            saved_description
            if saved_description is not None
            else str(defaults.get("publish_description") or "")
        )
    if tags is None:
        saved_tags = task.get("bilibili_upload_tags")
        tags = (
            saved_tags
            if saved_tags is not None
            else ",".join(defaults.get("publish_tags") or [])
        )
    if tid is None:
        saved_tid = task.get("bilibili_upload_tid")
        tid = int(saved_tid or defaults.get("publish_tid") or 17)
    if tid <= 0:
        raise HTTPException(status_code=422, detail="Bilibili category ID must be greater than 0.")
    return description.strip(), tags.strip(), tid


def _normalize_bilibili_topic_name(value: str | None) -> str | None:
    topic_name = (value or "").strip()
    return topic_name or None


def _normalize_bilibili_topic(
    *,
    topic_name: str | None,
    topic_id: int | None,
    topic_mission_id: int | None,
) -> tuple[str | None, int | None, int | None]:
    normalized_name = _normalize_bilibili_topic_name(topic_name)
    if normalized_name is None:
        return None, None, None
    if topic_id is not None and topic_id <= 0:
        raise HTTPException(status_code=422, detail="Bilibili topic ID must be greater than 0.")
    if topic_mission_id is not None and topic_mission_id <= 0:
        raise HTTPException(status_code=422, detail="Bilibili topic mission ID must be greater than 0.")
    return normalized_name, topic_id, topic_mission_id


def _normalize_bilibili_season(
    *,
    season_id: int | None,
    section_id: int | None,
) -> tuple[int | None, int | None]:
    if season_id is None and section_id is None:
        return None, None
    if season_id is None or section_id is None:
        raise HTTPException(status_code=422, detail="Bilibili season and section must be selected together.")
    if season_id <= 0 or section_id <= 0:
        raise HTTPException(status_code=422, detail="Bilibili season and section IDs must be greater than 0.")
    return season_id, section_id


def _normalize_watermark_mask_mode(value: str) -> str:
    mode = value.strip().lower()
    if mode not in {"patch", "blur"}:
        raise HTTPException(status_code=422, detail="mode must be one of: patch, blur")
    return mode


@app.post("/api/tasks/{task_id}/bilibili-title/translate")
def translate_bilibili_title(task_id: str, payload: BilibiliTitleTranslateRequest) -> dict:
    task = database.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    title = (payload.title or task.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=422, detail="Title is required.")
    settings = database.get_openai_settings()
    try:
        translated = translate_title_to_chinese(title, **{key: settings[key] for key in ("base_url", "api_key", "model")})
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to translate title: {exc}") from exc
    return {"title": translated}


@app.get("/api/bilibili/seasons")
def get_bilibili_seasons() -> dict:
    from scripts.upload_bilibili import UploadBilibiliError, list_bilibili_seasons

    try:
        return {"seasons": list_bilibili_seasons()}
    except UploadBilibiliError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/bilibili/topics/resolve")
def resolve_bilibili_topic(name: str) -> dict:
    from scripts.upload_bilibili import UploadBilibiliError, resolve_bilibili_topic_by_name

    topic_name = _normalize_bilibili_topic_name(name)
    if topic_name is None:
        raise HTTPException(status_code=422, detail="Bilibili topic name is required.")
    try:
        topic = resolve_bilibili_topic_by_name(topic_name)
    except UploadBilibiliError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "topic_id": topic.id,
        "topic_name": topic.name,
        "mission_id": topic.mission_id,
    }


@app.put("/api/tasks/{task_id}/bilibili-upload-settings")
def update_bilibili_upload_settings(task_id: str, payload: BilibiliUploadSettingsRequest) -> dict:
    task = database.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    publish_mode, dtime, upload_after = _normalize_bilibili_upload_settings(
        payload.publish_mode,
        payload.dtime,
        payload.upload_after,
    )
    description, tags, tid = _normalize_bilibili_upload_content(
        task,
        description=payload.description,
        tags=payload.tags,
        tid=payload.tid,
    )
    season_id, section_id = _normalize_bilibili_season(
        season_id=payload.season_id,
        section_id=payload.section_id,
    )
    topic_name, topic_id, topic_mission_id = _normalize_bilibili_topic(
        topic_name=payload.topic_name,
        topic_id=payload.topic_id,
        topic_mission_id=payload.topic_mission_id,
    )
    database.update_task(
        task_id,
        auto_bilibili_upload=1 if payload.auto_bilibili_upload else 0,
        bilibili_upload_title=(payload.title or "").strip() or None,
        bilibili_upload_description=description,
        bilibili_upload_tags=tags,
        bilibili_upload_tid=tid,
        bilibili_topic_name=topic_name,
        bilibili_topic_id=topic_id,
        bilibili_topic_mission_id=topic_mission_id,
        bilibili_season_id=season_id,
        bilibili_section_id=section_id,
        bilibili_publish_mode=publish_mode,
        bilibili_dtime=dtime,
        bilibili_upload_after=upload_after,
    )
    return _task_with_source(database.get_task(task_id), include_publish_info=True)


@app.post("/api/tasks/{task_id}/bilibili-upload", status_code=202)
async def create_bilibili_upload(task_id: str, request: Request) -> dict:
    cover_file: UploadFile | None = None
    content_type = request.headers.get("content-type", "").lower()
    if content_type.startswith("multipart/form-data") or content_type.startswith("application/x-www-form-urlencoded"):
        form = await request.form()
        payload = BilibiliUploadRequest(
            title=str(form.get("title") or ""),
            description=str(form.get("description")) if "description" in form else None,
            tags=str(form.get("tags")) if "tags" in form else None,
            tid=form.get("tid"),
            topic_name=str(form.get("topic_name")) if "topic_name" in form else None,
            topic_id=form.get("topic_id") or None,
            topic_mission_id=form.get("topic_mission_id") or None,
            season_id=form.get("season_id") or None,
            section_id=form.get("section_id") or None,
            publish_mode=str(form.get("publish_mode") or "now"),
            dtime=str(form.get("dtime") or "") or None,
            upload_after=str(form.get("upload_after") or "") or None,
        )
        maybe_cover = form.get("cover_file")
        if maybe_cover is not None and getattr(maybe_cover, "filename", ""):
            cover_file = maybe_cover  # type: ignore[assignment]
    else:
        try:
            payload = BilibiliUploadRequest(**(await request.json()))
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc

    task = database.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    session_path = task.get("session_path")
    if not session_path:
        raise HTTPException(status_code=400, detail="Task has no session path; run the pipeline first.")
    final_path = bilibili_upload_video_path(session_path)
    if not final_path:
        raise HTTPException(status_code=404, detail="Final video is not available.")

    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="Bilibili title is required.")
    publish_mode, dtime, upload_after = _normalize_bilibili_upload_settings(
        payload.publish_mode,
        payload.dtime,
        payload.upload_after,
    )
    description, tags, tid = _normalize_bilibili_upload_content(
        task,
        description=payload.description,
        tags=payload.tags,
        tid=payload.tid,
    )
    season_id, section_id = _normalize_bilibili_season(
        season_id=payload.season_id,
        section_id=payload.section_id,
    )
    topic_name, topic_id, topic_mission_id = _normalize_bilibili_topic(
        topic_name=payload.topic_name,
        topic_id=payload.topic_id,
        topic_mission_id=payload.topic_mission_id,
    )

    active = database.get_active_bilibili_upload_job(task_id)
    if active:
        raise HTTPException(status_code=409, detail="This task already has a Bilibili upload in progress.")

    job_id = str(uuid.uuid4())
    cover_path: str | None = None
    if cover_file is not None:
        suffix = _bilibili_cover_suffix(cover_file.filename)
        cover_dest = bilibili_cover.cover_dir(session_path) / "jobs" / f"{job_id}{suffix}"
        _save_uploaded_file(
            cover_file,
            cover_dest,
            max_bytes=MAX_BILIBILI_COVER_BYTES,
            too_large_detail="Uploaded Bilibili cover image is too large.",
        )
        cover_path = str(cover_dest)
    else:
        cover_path = bilibili_cover.snapshot_cover_for_job(session_path, job_id)

    job_id = database.create_bilibili_upload_job(
        task_id,
        title=title,
        description=description,
        tags=tags,
        tid=tid,
        topic_name=topic_name,
        topic_id=topic_id,
        topic_mission_id=topic_mission_id,
        publish_mode=publish_mode,
        dtime=dtime,
        upload_after=upload_after,
        cover_path=cover_path,
        season_id=season_id,
        section_id=section_id,
        job_id=job_id,
    )
    if upload_after is None:
        bilibili_uploads.enqueue(job_id)
    return database.get_bilibili_upload_job(job_id)


@app.get("/api/bilibili-upload-jobs/{job_id}")
def get_bilibili_upload_job(job_id: str) -> dict:
    job = database.get_bilibili_upload_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Bilibili upload job not found.")
    return job


@app.get("/api/bilibili-upload-jobs/{job_id}/log", response_class=PlainTextResponse)
def get_bilibili_upload_log(job_id: str) -> str:
    job = database.get_bilibili_upload_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Bilibili upload job not found.")
    path = Path(job["log_path"] or database.bilibili_upload_log_path(job_id))
    return path.read_text(encoding="utf-8") if path.exists() else ""


@app.get("/api/bilibili-upload-jobs/{job_id}/cover")
def get_bilibili_upload_cover(job_id: str):
    job = database.get_bilibili_upload_job(job_id)
    cover = Path(job["cover_path"]) if job and job.get("cover_path") else None
    if not cover or not cover.is_file():
        raise HTTPException(status_code=404, detail="投稿封面不存在。")
    return FileResponse(cover)


@app.exception_handler(database.BilibiliUploadConflict)
async def bilibili_upload_conflict_handler(request: Request, exc: database.BilibiliUploadConflict):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


def _batch_publish_content(task: dict) -> dict:
    """Resolve exactly the same defaults as single-video publishing, with fixed media."""
    video = bilibili_upload_video_path(task.get("session_path") or "")
    if not video or video.name == "video_source.mp4":
        raise ValueError(f"任务 {task.get('title') or task['id']} 的成品视频不存在。")
    from scripts.upload_bilibili import UploadBilibiliError, author_name, load_metadata

    try:
        author = author_name(load_metadata(Path(task["session_path"])))
    except (UploadBilibiliError, _json.JSONDecodeError) as exc:
        raise ValueError(f"任务 {task.get('title') or task['id']} 的投稿元数据不可用。") from exc
    title = (task.get("bilibili_upload_title") or task.get("title") or "").strip()
    if not title:
        info = _bilibili_publish_info(task) or {}
        title = str(info.get("source_title") or "").strip()
    if not title:
        raise ValueError(f"任务 {task['id']} 缺少发布标题，请先在任务详情中填写。")
    description, tags, tid = _normalize_bilibili_upload_content(task, description=None, tags=None, tid=None)
    topic_name, topic_id, topic_mission_id = _normalize_bilibili_topic(
        topic_name=task.get("bilibili_topic_name"), topic_id=task.get("bilibili_topic_id"),
        topic_mission_id=task.get("bilibili_topic_mission_id"),
    )
    season_id, section_id = _normalize_bilibili_season(
        season_id=task.get("bilibili_season_id"), section_id=task.get("bilibili_section_id"),
    )
    cover, _ = bilibili_cover.resolve_cover_path(task["session_path"])
    return {"title": title, "publish_title": f"{title}-{author}" if author else title,
            "description": description, "tags": tags, "tid": tid,
            "topic_name": topic_name, "topic_id": topic_id, "topic_mission_id": topic_mission_id,
            "season_id": season_id, "section_id": section_id, "task_created_at": task["created_at"],
            "video_path": str(video), "video_fingerprint": bilibili_schedule.fingerprint(video),
            "cover_path": str(cover) if cover else None, "cover_fingerprint": bilibili_schedule.fingerprint(cover)}


@app.post("/api/bilibili-publish-batches/preview")
def preview_bilibili_batch(payload: BilibiliBatchPreviewRequest) -> dict:
    try:
        return bilibili_schedule.create_preview(
            payload.task_ids, payload.start_date, payload.daily_times, _batch_publish_content,
            start_time=payload.start_time,
        )
    except (ValueError, OSError, OverflowError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/bilibili-publish-batches")
def list_bilibili_batches() -> dict:
    return {"batches": bilibili_schedule.list_batches()}


@app.get("/api/bilibili-publish-batches/{batch_id}")
def get_bilibili_batch(batch_id: str) -> dict:
    batch = bilibili_schedule.get_batch(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="发布批次不存在。")
    return batch


@app.post("/api/bilibili-publish-batches/{batch_id}/confirm", status_code=202)
def confirm_bilibili_batch(batch_id: str) -> dict:
    try:
        batch = bilibili_schedule.confirm_batch(batch_id, _batch_publish_content)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not batch:
        raise HTTPException(status_code=404, detail="发布批次不存在。")
    for item in batch["items"]:
        if item["status"] == "queued":
            bilibili_uploads.enqueue(item["job_id"])
    return batch


@app.post("/api/bilibili-publish-batches/{batch_id}/cancel-pending")
def cancel_bilibili_batch(batch_id: str) -> dict:
    batch = bilibili_schedule.cancel_pending(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="发布批次不存在。")
    return batch


@app.post("/api/bilibili-publish-batches/{batch_id}/jobs/{job_id}/retry", status_code=202)
def retry_bilibili_batch_job(batch_id: str, job_id: str) -> dict:
    try:
        batch = bilibili_schedule.retry_job(batch_id, job_id)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not batch:
        raise HTTPException(status_code=404, detail="发布任务不存在。")
    bilibili_uploads.enqueue(job_id)
    return batch


@app.post("/api/tasks/{task_id}/watermark-mask", status_code=202)
def create_watermark_mask(task_id: str, payload: WatermarkMaskRequest) -> dict:
    task = database.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    try:
        source_name = detect_source(task["url"]).name
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Only Bilibili tasks support watermark masking.") from exc
    if source_name != "bilibili":
        raise HTTPException(status_code=409, detail="Only Bilibili tasks support watermark masking.")
    if task["status"] in {"running", "queued"}:
        raise HTTPException(status_code=409, detail="Task is already running or queued.")
    if task["status"] != "succeeded":
        raise HTTPException(status_code=409, detail="Only succeeded tasks can be masked.")
    session_path = task.get("session_path")
    if not session_path:
        raise HTTPException(status_code=400, detail="Task has no session path; run the pipeline first.")
    final_path = task.get("final_video_path")
    if not final_path or not Path(final_path).exists():
        raise HTTPException(status_code=404, detail="Final video is not available.")

    mode = _normalize_watermark_mask_mode(payload.mode)
    active = database.get_active_watermark_mask_job(task_id)
    if active:
        raise HTTPException(status_code=409, detail="This task already has a watermark mask in progress.")

    output_path = Path(session_path) / "media" / f"video_final_watermark_masked_{mode}.mp4"
    job_id = database.create_watermark_mask_job(
        task_id,
        mode=mode,
        input_video_path=final_path,
        output_video_path=str(output_path),
    )
    watermark_masks.enqueue(job_id)
    return database.get_watermark_mask_job(job_id)


@app.get("/api/watermark-mask-jobs/{job_id}")
def get_watermark_mask_job(job_id: str) -> dict:
    job = database.get_watermark_mask_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Watermark mask job not found.")
    return job


@app.get("/api/watermark-mask-jobs/{job_id}/log", response_class=PlainTextResponse)
def get_watermark_mask_log(job_id: str) -> str:
    job = database.get_watermark_mask_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Watermark mask job not found.")
    path = Path(job["log_path"] or database.watermark_mask_log_path(job_id))
    return path.read_text(encoding="utf-8") if path.exists() else ""


@app.post("/api/tasks/{task_id}/continue")
def continue_task(task_id: str, payload: ContinueTaskRequest | None = None) -> dict:
    task = database.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    if task["status"] != "paused":
        raise HTTPException(status_code=409, detail="Only paused tasks can be continued.")
    if (task.get("execution_mode") or database.DEFAULT_EXECUTION_MODE) != "manual":
        raise HTTPException(status_code=409, detail="Only manual tasks can be continued step by step.")
    if payload and payload.execution_mode is not None:
        database.update_task(task_id, execution_mode=normalize_execution_mode(payload.execution_mode))
    _ensure_runtime_ready()
    database.queue_task_for_continue(task_id)
    worker.enqueue(task_id)
    return _task_with_source(database.get_task(task_id))


@app.post("/api/tasks/{task_id}/resume")
def resume_task(task_id: str) -> dict:
    task = database.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    if task["status"] != "failed":
        raise HTTPException(status_code=409, detail="Only failed tasks can be resumed.")
    _ensure_runtime_ready()
    database.reset_failed_for_resume(task_id)
    worker.enqueue(task_id)
    return _task_with_source(database.get_task(task_id))


_BEIJING_TIMEZONE = timezone(timedelta(hours=8))
_LOG_TIMESTAMP = re.compile(
    r"(?m)^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))\]"
)


def _log_in_beijing_time(content: str) -> str:
    def convert(match: re.Match[str]) -> str:
        try:
            timestamp = datetime.fromisoformat(match.group(1).replace("Z", "+00:00"))
        except ValueError:
            return match.group(0)
        return f"[{timestamp.astimezone(_BEIJING_TIMEZONE).isoformat()}]"

    return _LOG_TIMESTAMP.sub(convert, content)


@app.get("/api/tasks/{task_id}/log", response_class=PlainTextResponse)
def task_log(task_id: str) -> str:
    task = database.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    path = database.log_path(task_id)
    return _log_in_beijing_time(path.read_text(encoding="utf-8")) if path.exists() else ""


@app.get("/api/tasks/{task_id}/artifact/final-video")
def final_video(task_id: str, download: bool = False) -> FileResponse:
    task = database.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    session_path = task.get("session_path")
    final_path = bilibili_upload_video_path(session_path) if session_path else None
    if not final_path:
        raise HTTPException(status_code=404, detail="Final video is not available.")
    name = Path(final_path).name
    if download:
        return FileResponse(final_path, media_type="video/mp4", filename=name)
    headers = {"Content-Disposition": f'inline; filename="{name}"'}
    return FileResponse(final_path, media_type="video/mp4", headers=headers)


@app.get("/api/tasks/{task_id}/artifact/source-cover")
def source_cover(task_id: str) -> FileResponse:
    task = database.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    session_path = task.get("session_path")
    cover_path = Path(session_path) / "cover.jpg" if session_path else None
    if not cover_path or not cover_path.is_file():
        raise HTTPException(status_code=404, detail="Source cover is not available.")
    return FileResponse(cover_path, media_type="image/jpeg")


@app.get("/api/tasks/{task_id}/artifact/bilibili-cover")
def bilibili_cover_artifact(task_id: str, background: bool = False) -> FileResponse:
    _, session = _task_session_path(task_id)
    if background:
        cover_path = bilibili_cover.manual_cover_path(session) or bilibili_cover.source_cover_path(session)
    else:
        cover_path, _ = bilibili_cover.resolve_cover_path(session)
    if not cover_path:
        raise HTTPException(status_code=404, detail="Bilibili cover is not available.")
    return FileResponse(cover_path, media_type=_image_media_type(cover_path))


@app.get("/api/tasks/{task_id}/bilibili-cover")
def get_bilibili_cover(task_id: str) -> dict:
    _, session = _task_session_path(task_id)
    resolved, source = bilibili_cover.resolve_cover_path(session)
    project_path = bilibili_cover.editor_project_path(session)
    project: dict | None = None
    if project_path.is_file():
        try:
            parsed = _json.loads(project_path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                project = parsed
        except (OSError, _json.JSONDecodeError):
            pass
    return {
        "available": bool(resolved),
        "source": source,
        "manual_available": bilibili_cover.manual_cover_path(session) is not None,
        "edited_available": bilibili_cover.edited_cover_path(session) is not None,
        "project": project,
    }


@app.put("/api/tasks/{task_id}/bilibili-cover/manual")
def save_bilibili_manual_cover(task_id: str, file: UploadFile = File(...)) -> dict:
    _, session = _task_session_path(task_id)
    suffix = _bilibili_cover_suffix(file.filename)
    bilibili_cover.remove_manual_cover(session)
    destination = bilibili_cover.cover_dir(session) / f"{bilibili_cover.MANUAL_COVER_STEM}{suffix}"
    _save_uploaded_file(
        file,
        destination,
        max_bytes=MAX_BILIBILI_COVER_BYTES,
        too_large_detail="Uploaded Bilibili cover image is too large.",
    )
    return get_bilibili_cover(task_id)


@app.delete("/api/tasks/{task_id}/bilibili-cover/manual")
def delete_bilibili_manual_cover(task_id: str) -> dict:
    _, session = _task_session_path(task_id)
    bilibili_cover.remove_manual_cover(session)
    return get_bilibili_cover(task_id)


@app.put("/api/tasks/{task_id}/bilibili-cover/edited")
def save_bilibili_edited_cover(
    task_id: str,
    file: UploadFile = File(...),
    project_json: str = Form(...),
) -> dict:
    _, session = _task_session_path(task_id)
    try:
        project = _json.loads(project_json)
    except _json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="Invalid Bilibili cover editor project.") from exc
    if not isinstance(project, dict):
        raise HTTPException(status_code=422, detail="Bilibili cover editor project must be an object.")
    _bilibili_cover_suffix(file.filename)
    directory = bilibili_cover.cover_dir(session)
    destination = directory / bilibili_cover.EDITED_COVER_NAME
    _save_uploaded_file(
        file,
        destination,
        max_bytes=MAX_BILIBILI_COVER_BYTES,
        too_large_detail="Edited Bilibili cover image is too large.",
    )
    bilibili_cover.editor_project_path(session).write_text(
        _json.dumps(project, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return get_bilibili_cover(task_id)


@app.delete("/api/tasks/{task_id}/bilibili-cover/edited")
def delete_bilibili_edited_cover(task_id: str) -> dict:
    _, session = _task_session_path(task_id)
    bilibili_cover.remove_edited_cover(session)
    return get_bilibili_cover(task_id)


@app.get("/api/cookies/youtube")
def get_youtube_cookie() -> dict:
    exists = YOUTUBE_COOKIE_PATH.exists()
    size = YOUTUBE_COOKIE_PATH.stat().st_size if exists else 0
    updated_at = YOUTUBE_COOKIE_PATH.stat().st_mtime if exists else None
    return {"exists": exists, "size": size, "updated_at": updated_at, "content": ""}


@app.post("/api/cookies/youtube")
def save_youtube_cookie(payload: YouTubeCookieUpdate) -> dict:
    YOUTUBE_COOKIE_PATH.parent.mkdir(parents=True, exist_ok=True)
    content = payload.content.strip()
    if content:
        YOUTUBE_COOKIE_PATH.write_text(content + "\n", encoding="utf-8")
    elif YOUTUBE_COOKIE_PATH.exists():
        YOUTUBE_COOKIE_PATH.unlink()
    return get_youtube_cookie()


@app.get("/api/settings/openai")
def get_openai_settings() -> dict:
    settings = database.get_openai_settings()
    return {
        "base_url": settings["base_url"],
        "api_key": mask_secret(settings["api_key"]),
        "has_api_key": bool(settings["api_key"]),
        "model": settings["model"],
        "translate_concurrency": settings["translate_concurrency"],
    }


@app.post("/api/settings/openai")
def save_openai_settings(payload: OpenAISettingsUpdate) -> dict:
    database.save_openai_settings(
        payload.base_url,
        payload.api_key,
        payload.model,
        normalize_translate_concurrency(payload.translate_concurrency),
        clear_api_key=payload.clear_api_key,
    )
    return get_openai_settings()


@app.post("/api/settings/openai/models")
def get_openai_models(payload: OpenAIModelsRequest) -> dict:
    settings = database.get_openai_settings()
    base_url = payload.base_url.strip() or settings["base_url"]
    api_key = payload.api_key.strip() or settings["api_key"]
    try:
        models = list_openai_models(base_url=base_url, api_key=api_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch models: {exc}") from exc
    return {"models": models}


@app.get("/api/settings/ytdlp")
def get_ytdlp_settings() -> dict:
    return database.get_ytdlp_settings()


@app.post("/api/settings/ytdlp")
def save_ytdlp_settings(payload: YtdlpSettingsUpdate) -> dict:
    database.save_ytdlp_settings(normalize_proxy_port(payload.proxy_port))
    return get_ytdlp_settings()


@app.get("/api/settings/bilibili")
def get_bilibili_settings() -> dict:
    return database.get_bilibili_settings()


@app.post("/api/settings/bilibili")
def save_bilibili_settings(payload: BilibiliSettingsUpdate) -> dict:
    database.save_bilibili_settings(
        normalize_bilibili_author_tags_json(payload.author_tags_json),
        normalize_bilibili_author_tids_json(payload.author_tids_json),
        author_config_json=normalize_bilibili_author_config_json(payload.author_config_json),
        pokemon_topic_config_json=normalize_bilibili_pokemon_topic_config_json(payload.pokemon_topic_config_json),
    )
    return get_bilibili_settings()


@app.get("/api/settings/mail")
def get_mail_settings() -> dict:
    settings = database.get_mail_settings()
    return {
        "enabled": bool_setting(settings["enabled"]),
        "smtp_host": settings["smtp_host"],
        "smtp_port": settings["smtp_port"],
        "smtp_username": settings["smtp_username"],
        "smtp_password": mask_secret(settings["smtp_password"]),
        "has_smtp_password": bool(settings["smtp_password"]),
        "from_address": settings["from_address"],
        "to_addresses": settings["to_addresses"],
        "smtp_security": settings["smtp_security"],
        "notify_on_success": bool_setting(settings["notify_on_success"]),
        "notify_on_failure": bool_setting(settings["notify_on_failure"]),
    }


@app.post("/api/settings/mail")
def save_mail_settings(payload: MailSettingsUpdate) -> dict:
    database.save_mail_settings(
        enabled=setting_bool(payload.enabled),
        smtp_host=payload.smtp_host,
        smtp_port=normalize_smtp_port(payload.smtp_port),
        smtp_username=payload.smtp_username,
        smtp_password=payload.smtp_password,
        from_address=normalize_mail_sender(payload.from_address),
        to_addresses=normalize_mail_addresses(payload.to_addresses),
        smtp_security=normalize_smtp_security(payload.smtp_security),
        notify_on_success=setting_bool(payload.notify_on_success),
        notify_on_failure=setting_bool(payload.notify_on_failure),
        clear_smtp_password=payload.clear_smtp_password,
    )
    return get_mail_settings()
