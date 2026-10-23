"""独立的 B 站上传后台队列。"""

from __future__ import annotations

import os
import queue
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import database

_queue: "queue.Queue[str]" = queue.Queue()
_thread: threading.Thread | None = None
_scheduler_thread: threading.Thread | None = None
_lock = threading.Lock()
BATCH_UPLOAD_INTERVAL_SECONDS = 60
BATCH_UPLOAD_READY_AT_SETTING = "bilibili_upload.batch_ready_at"
SCHEDULE_POLL_SECONDS = max(5, int(os.getenv("BILIBILI_UPLOAD_SCHEDULE_POLL_SECONDS", "30")))
BILIBILI_SEASON_SWITCH_INITIAL_DELAY_SECONDS = max(
    0.0,
    float(os.getenv("BILIBILI_SEASON_SWITCH_INITIAL_DELAY_SECONDS", "30")),
)


def enqueue(job_id: str) -> None:
    _queue.put(job_id)


def start() -> None:
    global _thread, _scheduler_thread
    with _lock:
        if _thread is not None:
            return
        _thread = threading.Thread(target=_loop, daemon=True)
        _thread.start()
        _scheduler_thread = threading.Thread(target=_schedule_loop, daemon=True)
        _scheduler_thread.start()
    for job in database.list_queued_bilibili_upload_jobs():
        enqueue(job["id"])
    _enqueue_due_scheduled_jobs()


def _enqueue_due_scheduled_jobs() -> None:
    for job in database.queue_due_scheduled_bilibili_upload_jobs():
        enqueue(job["id"])


def _append_log(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(content)
        if content and not content.endswith("\n"):
            handle.write("\n")


def _notify_completion(job_id: str, log_path: Path) -> None:
    from .notifications import notify_bilibili_upload_completion

    notify_bilibili_upload_completion(job_id, lambda message: _append_log(log_path, message))


def _wait_before_season_switch(upload_succeeded_at: float, log_path: Path) -> None:
    remaining_seconds = BILIBILI_SEASON_SWITCH_INITIAL_DELAY_SECONDS - (
        time.monotonic() - upload_succeeded_at
    )
    if remaining_seconds <= 0:
        return
    _append_log(log_path, f"[合集] 投稿成功后等待 {round(remaining_seconds)} 秒再首次加入合集")
    time.sleep(remaining_seconds)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _wait_before_batch_upload(job_id: str, log_path: Path) -> bool:
    """Keep the queued job cancellable while honoring the persisted batch interval."""
    raw = database.get_setting(BATCH_UPLOAD_READY_AT_SETTING)
    if not raw:
        return True
    ready_at = datetime.fromisoformat(raw)
    logged = False
    while True:
        job = database.get_bilibili_upload_job(job_id)
        if not job or job["status"] != "queued":
            return False
        remaining = (ready_at - _utc_now()).total_seconds()
        if remaining <= 0:
            return True
        if not logged:
            _append_log(log_path, f"[批量上传] 上一个任务已结束，等待 {remaining:.1f} 秒后执行下一个任务")
            logged = True
        time.sleep(min(remaining, 1.0))


def _run_job(job_id: str) -> None:
    job = database.get_bilibili_upload_job(job_id)
    if not job:
        return
    if job["status"] != "queued":
        return
    task = database.get_task(job["task_id"])
    log_path = Path(job["log_path"] or database.bilibili_upload_log_path(job_id))
    if job.get("batch_id") and not _wait_before_batch_upload(job_id, log_path):
        return
    if not database.claim_bilibili_upload_job(job_id):
        return

    try:
        _execute_job(job, task, log_path)
    finally:
        if job.get("batch_id"):
            ready_at = _utc_now() + timedelta(seconds=BATCH_UPLOAD_INTERVAL_SECONDS)
            database.set_setting(BATCH_UPLOAD_READY_AT_SETTING, ready_at.isoformat(timespec="microseconds"))


def _execute_job(job: dict, task: dict | None, log_path: Path) -> None:
    job_id = job["id"]

    if not task:
        message = "任务不存在，无法上传到 B 站。"
        _append_log(log_path, message)
        database.update_bilibili_upload_job(
            job_id,
            status="failed",
            error_message=message,
            completed_at=database.now_iso(),
        )
        _notify_completion(job_id, log_path)
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
        _notify_completion(job_id, log_path)
        return

    from scripts.upload_bilibili import (
        UploadBilibiliError,
        UploadBilibiliOptions,
        add_bilibili_season_episode,
        resolve_archive_aid,
        resolve_archive_cid,
        parse_uploaded_archive,
        upload_bilibili,
    )

    try:
        if job.get("batch_id"):
            from .bilibili_schedule import MIN_LEAD, fingerprint, now_utc, publication_time

            if publication_time(job["dtime"]) < now_utc() + MIN_LEAD:
                raise ValueError("原定发布时间的提前量已不足；请重新排期。不会改成立即公开。")
            if not job.get("video_path") or fingerprint(job["video_path"]) != job.get("video_fingerprint"):
                raise ValueError("排期后的成品视频已变化或丢失，请重新生成预览并排期。")
        bilibili_settings = database.get_bilibili_settings()
        topic_name = (job.get("topic_name") or "").strip()
        if topic_name:
            _append_log(log_path, f"[话题] 正在按完整名称查询话题：{topic_name}")
        result = upload_bilibili(
            UploadBilibiliOptions(
                path=job.get("video_path") or session_path,
                title=job["title"],
                desc=job.get("description"),
                tag=job.get("tags"),
                tid=job.get("tid"),
                topic_name=topic_name,
                topic_id=job.get("topic_id"),
                topic_mission_id=job.get("topic_mission_id"),
                dtime=job["dtime"],
                title_is_final=bool(job.get("batch_id")),
                use_default_cover=not bool(job.get("batch_id")),
                cover=job.get("cover_path"),
                author_config_json=bilibili_settings["author_config_json"],
                author_tags_json=bilibili_settings["author_tags_json"],
                author_tids_json=bilibili_settings["author_tids_json"],
            )
        )
        upload_succeeded_at = time.monotonic()
        season_id = job.get("season_id")
        section_id = job.get("section_id")
        aid = getattr(result, "aid", None)
        bvid = getattr(result, "bvid", None)
        topic_id = getattr(result, "topic_id", None)
        topic_mission_id = getattr(result, "topic_mission_id", None)
        if aid or bvid:
            database.update_bilibili_upload_job(
                job_id,
                aid=aid,
                bvid=bvid,
            )
        if topic_id:
            _append_log(log_path, f"[话题] 已匹配 topic_id={topic_id}")
            database.update_bilibili_upload_job(
                job_id,
                topic_name=getattr(result, "topic_name", "") or topic_name,
                topic_id=topic_id,
                topic_mission_id=topic_mission_id,
            )
            database.update_task(
                job["task_id"],
                bilibili_topic_name=getattr(result, "topic_name", "") or topic_name,
                bilibili_topic_id=topic_id,
                bilibili_topic_mission_id=topic_mission_id,
            )
        if season_id and section_id:
            _append_log(log_path, f"[合集] 准备加入合集 season_id={season_id}, section_id={section_id}")
            aid = resolve_archive_aid(aid=aid, bvid=bvid)
            cid = resolve_archive_cid(aid=aid, bvid=bvid)
            database.update_bilibili_upload_job(
                job_id,
                aid=aid,
                bvid=bvid,
            )
            _wait_before_season_switch(upload_succeeded_at, log_path)
            _append_log(
                log_path,
                f"[合集] 正在调用合集新增接口 season_id={season_id}, section_id={section_id}, aid={aid}, cid={cid}",
            )
            add_bilibili_season_episode(
                aid=aid,
                cid=cid,
                section_id=int(section_id),
                title=result.title,
            )
            _append_log(log_path, f"[合集] 已加入合集，aid={aid}")
    except UploadBilibiliError as exc:
        # A submission can return an error after creating an archive. Keep its IDs
        # so a user retry cannot create a second video.
        archive_aid, archive_bvid = parse_uploaded_archive(exc.stdout or "", exc.stderr or "")
        if archive_aid or archive_bvid:
            database.update_bilibili_upload_job(job_id, aid=archive_aid, bvid=archive_bvid)
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
        _notify_completion(job_id, log_path)
        return
    except Exception as exc:  # noqa: BLE001 - 后台 worker 不能因未知异常退出
        _append_log(log_path, str(exc))
        database.update_bilibili_upload_job(
            job_id,
            status="failed",
            error_message=str(exc),
            completed_at=database.now_iso(),
        )
        _notify_completion(job_id, log_path)
        return

    if result.stdout:
        _append_log(log_path, result.stdout)
    if result.stderr:
        _append_log(log_path, result.stderr)
    database.update_bilibili_upload_job(
        job_id,
        status="succeeded",
        return_code=result.return_code,
        aid=aid,
        bvid=bvid,
        topic_name=getattr(result, "topic_name", "") or job.get("topic_name"),
        topic_id=getattr(result, "topic_id", None) or job.get("topic_id"),
        topic_mission_id=getattr(result, "topic_mission_id", None) or job.get("topic_mission_id"),
        completed_at=database.now_iso(),
    )
    _notify_completion(job_id, log_path)


def _loop() -> None:
    while True:
        job_id = _queue.get()
        try:
            _run_job(job_id)
        finally:
            _queue.task_done()


def _schedule_loop() -> None:
    while True:
        _enqueue_due_scheduled_jobs()
        threading.Event().wait(SCHEDULE_POLL_SECONDS)
