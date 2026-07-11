from __future__ import annotations

import os
import shutil
import uuid
import json as _json
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from . import bilibili_uploads, database, watermark_masks, worker
from .adapters.local_subtitles import parse_srt, uploaded_subtitle_dir
from .adapters.local_video import remove_upload, uploaded_video_dir
from .adapters.openai_translate import list_models as list_openai_models
from .config import WORKFOLDER, YOUTUBE_COOKIE_PATH, ensure_runtime_dirs
from .pipeline import run_task
from .runtime_checks import validate_runtime_device
from .sanitize import sanitize_text
from .sources import detect_source
from .stage_reset import remove_stage_artifacts
from .stages import STAGE_NAMES
from .youtube import LOCAL_UPLOAD_DIRECTIONS, extract_video_id, is_local_upload_url

ALLOWED_VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".flv", ".wmv"}
ALLOWED_SUBTITLE_SUFFIXES = {".srt"}
LOCAL_UPLOAD_CHUNK_SIZE = 1024 * 1024
MAX_LOCAL_UPLOAD_BYTES = int(os.getenv("LOCAL_UPLOAD_MAX_BYTES", str(4 * 1024 * 1024 * 1024)))
MAX_LOCAL_SUBTITLE_BYTES = int(os.getenv("LOCAL_SUBTITLE_MAX_BYTES", str(20 * 1024 * 1024)))


def mask_secret(value: str) -> str:
    if not value:
        return ""
    return "********"


class TaskCreate(BaseModel):
    url: str
    execution_mode: str = "auto"
    auto_bilibili_upload: bool = False


class ContinueTaskRequest(BaseModel):
    execution_mode: str | None = None


class BilibiliUploadRequest(BaseModel):
    title: str
    publish_mode: str = "now"
    dtime: str | None = None


class WatermarkMaskRequest(BaseModel):
    mode: str = "patch"


class CutInterval(BaseModel):
    start: float
    end: float


class CutIntervalsRequest(BaseModel):
    cut_intervals: list[CutInterval] = Field(default_factory=list)


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
    author_tags_json: str = ""


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
    return []


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


def _task_with_source(task: dict | None) -> dict | None:
    if not task:
        return None
    result = dict(task)
    result["auto_bilibili_upload"] = bool(result.get("auto_bilibili_upload"))
    try:
        result["source_name"] = detect_source(result["url"]).name
    except ValueError:
        result["source_name"] = "unknown"
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


@app.post("/api/tasks", status_code=201)
def create_task(payload: TaskCreate) -> dict:
    try:
        video_id = extract_video_id(payload.url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    existing_id = database.find_task_by_video_id(video_id)
    if existing_id:
        return _task_with_source(database.get_task(existing_id))

    _ensure_runtime_ready()
    task_id = database.create_task(
        payload.url.strip(),
        task_id=video_id,
        execution_mode=normalize_execution_mode(payload.execution_mode),
        auto_bilibili_upload=payload.auto_bilibili_upload,
    )
    worker.enqueue(task_id)
    return _task_with_source(database.get_task(task_id))


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
    return _task_with_source(task)


def _is_inside_workfolder(path: Path) -> bool:
    workfolder = WORKFOLDER.resolve()
    try:
        path.resolve().relative_to(workfolder)
    except ValueError:
        return False
    return True


def _purge_task(task: dict) -> None:
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


@app.post("/api/tasks/{task_id}/trim")
def trim_video_task(task_id: str, payload: CutIntervalsRequest | None = None) -> dict:
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
        serialized = _json.dumps(intervals, ensure_ascii=False)
        database.save_task_cut_intervals(task_id, serialized)
        intervals_file.write_text(serialized, encoding="utf-8")
    else:
        intervals = task_cut_intervals(task)
        if intervals:
            intervals_file.write_text(_json.dumps(intervals, ensure_ascii=False), encoding="utf-8")

    if not intervals_file.exists():
        raise HTTPException(status_code=400, detail="No cut intervals provided and cut_intervals.json not found.")

    trimmed = session / "media" / "video_final_trimmed.mp4"
    if trimmed.exists():
        trimmed.unlink()

    _ensure_runtime_ready()
    database.reset_stages_from(task_id, "trim_video")
    database.queue_task_for_continue(task_id)
    worker.enqueue(task_id)
    return _task_with_source(database.get_task(task_id))


def _normalize_bilibili_publish_mode(value: str) -> str:
    mode = value.strip().lower()
    if mode not in {"now", "scheduled"}:
        raise HTTPException(status_code=422, detail="publish_mode must be one of: now, scheduled")
    return mode


def _normalize_watermark_mask_mode(value: str) -> str:
    mode = value.strip().lower()
    if mode not in {"patch", "blur"}:
        raise HTTPException(status_code=422, detail="mode must be one of: patch, blur")
    return mode


@app.post("/api/tasks/{task_id}/bilibili-upload", status_code=202)
def create_bilibili_upload(task_id: str, payload: BilibiliUploadRequest) -> dict:
    task = database.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    if task["status"] != "succeeded":
        raise HTTPException(status_code=409, detail="Only succeeded tasks can be published to Bilibili.")
    session_path = task.get("session_path")
    if not session_path:
        raise HTTPException(status_code=400, detail="Task has no session path; run the pipeline first.")
    final_path = task.get("final_video_path")
    if not final_path or not Path(final_path).exists():
        raise HTTPException(status_code=404, detail="Final video is not available.")

    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="Bilibili title is required.")
    publish_mode = _normalize_bilibili_publish_mode(payload.publish_mode)
    dtime = payload.dtime.strip() if payload.dtime else None
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

    active = database.get_active_bilibili_upload_job(task_id)
    if active:
        raise HTTPException(status_code=409, detail="This task already has a Bilibili upload in progress.")

    job_id = database.create_bilibili_upload_job(
        task_id,
        title=title,
        publish_mode=publish_mode,
        dtime=dtime,
    )
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


@app.get("/api/tasks/{task_id}/log", response_class=PlainTextResponse)
def task_log(task_id: str) -> str:
    task = database.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    path = database.log_path(task_id)
    return path.read_text(encoding="utf-8") if path.exists() else ""


@app.get("/api/tasks/{task_id}/artifact/final-video")
def final_video(task_id: str, download: bool = False) -> FileResponse:
    task = database.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    final_path = task.get("final_video_path")
    if not final_path or not Path(final_path).exists():
        raise HTTPException(status_code=404, detail="Final video is not available.")
    name = Path(final_path).name
    if download:
        return FileResponse(final_path, media_type="video/mp4", filename=name)
    headers = {"Content-Disposition": f'inline; filename="{name}"'}
    return FileResponse(final_path, media_type="video/mp4", headers=headers)


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
    database.save_bilibili_settings(normalize_bilibili_author_tags_json(payload.author_tags_json))
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
