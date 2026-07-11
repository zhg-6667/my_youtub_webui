"""独立的 B 站上传后台队列。"""

from __future__ import annotations

import queue
import threading
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
    for job in database.list_queued_bilibili_upload_jobs():
        enqueue(job["id"])


def _append_log(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(content)
        if content and not content.endswith("\n"):
            handle.write("\n")


def _run_job(job_id: str) -> None:
    job = database.get_bilibili_upload_job(job_id)
    if not job:
        return
    task = database.get_task(job["task_id"])
    log_path = Path(job["log_path"] or database.bilibili_upload_log_path(job_id))
    database.update_bilibili_upload_job(job_id, status="running", started_at=database.now_iso())

    if not task:
        message = "任务不存在，无法上传到 B 站。"
        _append_log(log_path, message)
        database.update_bilibili_upload_job(
            job_id,
            status="failed",
            error_message=message,
            completed_at=database.now_iso(),
        )
        return

    session_path = task.get("session_path")
    if not session_path:
        message = "任务没有会话目录，无法上传到 B 站。"
        _append_log(log_path, message)
        database.update_bilibili_upload_job(
            job_id,
            status="failed",
            error_message=message,
            completed_at=database.now_iso(),
        )
        return

    from scripts.upload_bilibili import UploadBilibiliError, UploadBilibiliOptions, upload_bilibili

    try:
        result = upload_bilibili(
            UploadBilibiliOptions(
                path=session_path,
                title=job["title"],
                dtime=job["dtime"],
            )
        )
    except UploadBilibiliError as exc:
        if exc.stdout:
            _append_log(log_path, exc.stdout)
        if exc.stderr:
            _append_log(log_path, exc.stderr)
        _append_log(log_path, str(exc))
        database.update_bilibili_upload_job(
            job_id,
            status="failed",
            error_message=str(exc),
            return_code=exc.return_code,
            completed_at=database.now_iso(),
        )
        return
    except Exception as exc:  # noqa: BLE001 - 后台 worker 不能因未知异常退出
        _append_log(log_path, str(exc))
        database.update_bilibili_upload_job(
            job_id,
            status="failed",
            error_message=str(exc),
            completed_at=database.now_iso(),
        )
        return

    if result.stdout:
        _append_log(log_path, result.stdout)
    if result.stderr:
        _append_log(log_path, result.stderr)
    database.update_bilibili_upload_job(
        job_id,
        status="succeeded",
        return_code=result.return_code,
        completed_at=database.now_iso(),
    )


def _loop() -> None:
    while True:
        job_id = _queue.get()
        try:
            _run_job(job_id)
        finally:
            _queue.task_done()
