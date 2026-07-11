"""独立的 Bilibili 水印打码后台队列。"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
from dataclasses import asdict
from pathlib import Path

from . import database

_queue: "queue.Queue[str]" = queue.Queue()
_thread: threading.Thread | None = None
_lock = threading.Lock()


def enqueue(job_id: str) -> None:
    _queue.put(job_id)


def start() -> None:
    global _thread
    with _lock:
        if _thread is not None:
            return
        _thread = threading.Thread(target=_loop, daemon=True)
        _thread.start()
    for job in database.list_queued_watermark_mask_jobs():
        enqueue(job["id"])


def _append_log(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(content)
        if content and not content.endswith("\n"):
            handle.write("\n")


def _run_job(job_id: str) -> None:
    job = database.get_watermark_mask_job(job_id)
    if not job:
        return
    task = database.get_task(job["task_id"])
    log_path = Path(job["log_path"] or database.watermark_mask_log_path(job_id))
    database.update_watermark_mask_job(job_id, status="running", started_at=database.now_iso())

    if not task:
        message = "任务不存在，无法打码水印。"
        _append_log(log_path, message)
        database.update_watermark_mask_job(
            job_id,
            status="failed",
            error_message=message,
            completed_at=database.now_iso(),
        )
        return

    session_path = task.get("session_path")
    input_video_path = job.get("input_video_path") or task.get("final_video_path")
    if not session_path or not input_video_path:
        message = "任务缺少会话目录或最终视频，无法打码水印。"
        _append_log(log_path, message)
        database.update_watermark_mask_job(
            job_id,
            status="failed",
            error_message=message,
            completed_at=database.now_iso(),
        )
        return

    from .adapters.watermark_mask import mask_bilibili_watermark

    try:
        output, region = mask_bilibili_watermark(Path(input_video_path), Path(session_path), job["mode"])
    except subprocess.CalledProcessError as exc:
        message = f"ffmpeg 水印打码失败：{exc}"
        _append_log(log_path, message)
        database.update_watermark_mask_job(
            job_id,
            status="failed",
            error_message=message,
            return_code=exc.returncode,
            completed_at=database.now_iso(),
        )
        return
    except subprocess.TimeoutExpired as exc:
        message = f"ffmpeg 水印打码超时：{exc}"
        _append_log(log_path, message)
        database.update_watermark_mask_job(
            job_id,
            status="failed",
            error_message=message,
            completed_at=database.now_iso(),
        )
        return
    except Exception as exc:  # noqa: BLE001 - 后台 worker 不能因未知异常退出
        message = str(exc)
        _append_log(log_path, message)
        database.update_watermark_mask_job(
            job_id,
            status="failed",
            error_message=message,
            completed_at=database.now_iso(),
        )
        return

    database.update_task(job["task_id"], final_video_path=str(output))
    database.update_watermark_mask_job(
        job_id,
        status="succeeded",
        output_video_path=str(output),
        region_json=json.dumps(asdict(region), ensure_ascii=False),
        return_code=0,
        completed_at=database.now_iso(),
    )
    _append_log(log_path, f"水印打码完成：{output}")


def _loop() -> None:
    while True:
        job_id = _queue.get()
        try:
            _run_job(job_id)
        finally:
            _queue.task_done()
