"""Persisted, preview-first Bilibili publication schedules (all slots are Beijing time)."""

from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from . import bilibili_cover, database

BEIJING = timezone(timedelta(hours=8))
# Application scheduling policy, shared by preview, confirmation, retry and upload.
MIN_LEAD = timedelta(minutes=30)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def fingerprint(path: str | Path | None) -> str | None:
    if not path:
        return None
    stat = Path(path).stat()
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def publication_time(raw: str) -> datetime:
    if raw.isdigit():
        return datetime.fromtimestamp(int(raw), timezone.utc)
    value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return value.replace(tzinfo=BEIJING) if value.tzinfo is None else value


def occupied_slots(conn) -> set[int]:
    result = set()
    for row in conn.execute(
        "SELECT dtime FROM bilibili_upload_jobs WHERE publish_mode = 'scheduled' "
        "AND status IN ('scheduled', 'queued', 'running', 'succeeded') AND dtime IS NOT NULL"
    ):
        try:
            result.add(int(publication_time(row["dtime"]).timestamp()))
        except (ValueError, OverflowError, OSError):
            continue
    return result


def validate_task(task: dict | None, conn) -> None:
    if not task or task["status"] != "succeeded":
        raise ValueError("请选择处理成功的任务；任务状态已变化时请重新生成预览。")
    if conn.execute(
        "SELECT 1 FROM bilibili_upload_jobs WHERE task_id = ? "
        "AND status IN ('scheduled', 'queued', 'running', 'succeeded') LIMIT 1", (task["id"],)
    ).fetchone():
        raise ValueError(f"任务 {task.get('title') or task['id']} 已有投稿或发布安排，请先核对已有投稿。")


def create_preview(task_ids: list[str], start_date: str, daily_times: list[str], build_content, *, start_time: str = "00:00") -> dict:
    if not task_ids or len(task_ids) > 500 or len(set(task_ids)) != len(task_ids):
        raise ValueError("请选择 1～500 个不重复的任务。")
    try:
        first_day = date.fromisoformat(start_date)
    except ValueError as exc:
        raise ValueError("开始日期格式应为 YYYY-MM-DD。") from exc
    now = now_utc()
    if first_day < now.astimezone(BEIJING).date():
        raise ValueError("开始日期不能早于北京时间的今天。")
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", start_time):
        raise ValueError("开始时间格式应为 HH:MM。")
    starts_at = datetime.combine(first_day, time.fromisoformat(start_time), BEIJING)
    if not daily_times or len(daily_times) > 24 or any(
        not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", slot) for slot in daily_times
    ):
        raise ValueError("请设置 1～24 个有效的每日发布时间（HH:MM）。")
    slots = sorted(set(daily_times))
    with database.connect() as conn:
        placeholders = ",".join("?" for _ in task_ids)
        tasks = conn.execute(
            f"SELECT * FROM tasks WHERE id IN ({placeholders}) ORDER BY created_at ASC, rowid ASC", task_ids
        ).fetchall()
        if len(tasks) != len(task_ids):
            raise ValueError("部分任务已不存在，请刷新任务列表。")
        for task in tasks:
            validate_task(dict(task), conn)
        occupied = occupied_slots(conn)
    # Resolve all publishing fields before creating a draft. Preview never queues uploads.
    contents = [build_content(dict(task)) for task in tasks]
    items, skipped = [], []
    day = first_day
    while len(items) < len(contents):
        for slot in slots:
            publish_at = datetime.combine(day, time.fromisoformat(slot), BEIJING)
            timestamp = int(publish_at.timestamp())
            reason = None
            if publish_at < starts_at:
                reason = "before_start"
            elif publish_at <= now:
                reason = "past"
            elif publish_at < now + MIN_LEAD:
                reason = "lead"
            elif timestamp in occupied:
                reason = "occupied"
            if reason:
                skipped.append({"publish_at": publish_at.isoformat(), "reason": reason})
                continue
            items.append({
                "position": len(items) + 1,
                "task_id": tasks[len(items)]["id"],
                "publish_at": publish_at.astimezone(timezone.utc).isoformat(timespec="seconds"),
                "content": contents[len(items)],
            })
            if len(items) == len(contents):
                break
        day += timedelta(days=1)
    preview = {"start_date": first_day.isoformat(), "start_time": start_time, "daily_times": slots,
               "timezone": "Asia/Shanghai", "min_lead_hours": MIN_LEAD.total_seconds() / 3600,
               "skipped": skipped, "items": items}
    batch_id = str(uuid.uuid4())
    with database.connect() as conn:
        conn.execute("INSERT INTO bilibili_publish_batches (id, preview_json, created_at) VALUES (?, ?, ?)",
                     (batch_id, json.dumps(preview, ensure_ascii=False), database.now_iso()))
    return get_batch(batch_id)


def get_batch(batch_id: str) -> dict | None:
    with database.connect() as conn:
        row = conn.execute("SELECT * FROM bilibili_publish_batches WHERE id = ?", (batch_id,)).fetchone()
        if not row:
            return None
        jobs = conn.execute("SELECT * FROM bilibili_upload_jobs WHERE batch_id = ? ORDER BY batch_position", (batch_id,)).fetchall()
    preview = json.loads(row["preview_json"])
    # Older previews may contain a retired delay setting. Submission now always queues uploads.
    preview.pop("upload_lead_hours", None)
    preview.setdefault("start_time", "00:00")
    by_position = {job["batch_position"]: dict(job) for job in jobs}
    items = []
    for item in preview["items"]:
        content = item["content"]
        job = by_position.get(item["position"])
        items.append({**{key: value for key, value in item.items() if key != "content"},
                      "title": content["publish_title"], "description": content["description"], "tags": content["tags"],
                      "tid": content["tid"], "topic_name": content["topic_name"],
                      "season_id": content["season_id"], "section_id": content["section_id"],
                      "task_created_at": content["task_created_at"],
                      "has_cover": bool(content["cover_path"]),
                      "job_id": job["id"] if job else None,
                      "status": job["status"] if job else ("preview" if row["status"] == "draft" else "removed"),
                      "error_message": job["error_message"] if job else None,
                      "bvid": job["bvid"] if job else None,
                      "can_retry": bool(job and job["status"] == "failed" and not job["aid"] and not job["bvid"]
                                        and publication_time(item["publish_at"]) >= now_utc() + MIN_LEAD)})
    return {**preview, "items": items, "id": batch_id, "status": row["status"],
            "created_at": row["created_at"], "confirmed_at": row["confirmed_at"]}


def list_batches(limit: int = 100) -> list[dict]:
    with database.connect() as conn:
        ids = [row["id"] for row in conn.execute(
            "SELECT id FROM bilibili_publish_batches WHERE status = 'confirmed' ORDER BY created_at DESC LIMIT ?", (limit,)
        )]
    return [get_batch(batch_id) for batch_id in ids]


def confirm_batch(batch_id: str, build_content) -> dict | None:
    snapshots = []
    try:
        with database.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM bilibili_publish_batches WHERE id = ?", (batch_id,)).fetchone()
            if not row:
                return None
            if row["status"] == "confirmed":
                return get_batch(batch_id)  # Idempotent even if the client lost the first response.
            preview = json.loads(row["preview_json"])
            now = now_utc()
            occupied = occupied_slots(conn)
            prepared = []
            for item in preview["items"]:
                task_row = conn.execute("SELECT * FROM tasks WHERE id = ?", (item["task_id"],)).fetchone()
                task = dict(task_row) if task_row else None
                validate_task(task, conn)
                if build_content(task) != item["content"]:
                    raise ValueError("任务发布信息、视频或封面已变化，请重新生成排期预览。")
                publish_at = publication_time(item["publish_at"])
                if publish_at < now + MIN_LEAD or int(publish_at.timestamp()) in occupied:
                    raise ValueError("预览中的发布时间已不可用，请重新生成排期预览。")
                prepared.append((item, task, publish_at))
            for item, task, publish_at in prepared:
                job_id = str(uuid.uuid4())
                cover = bilibili_cover.snapshot_cover_for_job(task["session_path"], job_id)
                if cover:
                    snapshots.append(Path(cover))
                content = item["content"]
                if fingerprint(cover) != content["cover_fingerprint"]:
                    raise ValueError("封面已变化，请重新生成排期预览。")
                fields = {key: content[key] for key in ("title", "description", "tags", "tid", "topic_name", "topic_id",
                                                       "topic_mission_id", "season_id", "section_id", "video_path", "video_fingerprint")}
                fields["title"] = content["publish_title"]
                database.create_bilibili_upload_job(
                    task["id"], **fields, job_id=job_id, batch_id=batch_id, batch_position=item["position"],
                    publish_mode="scheduled", dtime=str(int(publish_at.timestamp())),
                    upload_after=None,
                    cover_path=cover, connection=conn,
                )
            conn.execute("UPDATE bilibili_publish_batches SET status = 'confirmed', confirmed_at = ? WHERE id = ?",
                         (database.now_iso(), batch_id))
    except Exception:
        for path in snapshots:
            path.unlink(missing_ok=True)
        raise
    return get_batch(batch_id)


def cancel_pending(batch_id: str) -> dict | None:
    with database.connect() as conn:
        conn.execute("UPDATE bilibili_upload_jobs SET status = 'cancelled', completed_at = ? "
                     "WHERE batch_id = ? AND status IN ('scheduled', 'queued')", (database.now_iso(), batch_id))
    return get_batch(batch_id)


def retry_job(batch_id: str, job_id: str) -> dict | None:
    with database.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM bilibili_upload_jobs WHERE id = ? AND batch_id = ?", (job_id, batch_id)).fetchone()
        if not row:
            return None
        job = dict(row)
        if job["status"] != "failed" or job["aid"] or job["bvid"]:
            raise ValueError("仅可重试尚未取得稿件 ID 的失败项；已有稿件请先前往 B 站核对。")
        task = conn.execute("SELECT * FROM tasks WHERE id = ?", (job["task_id"],)).fetchone()
        validate_task(dict(task) if task else None, conn)
        if publication_time(job["dtime"]) < now_utc() + MIN_LEAD:
            raise ValueError("原定发布时间提前量不足，请重新生成排期。")
        if int(publication_time(job["dtime"]).timestamp()) in occupied_slots(conn):
            raise ValueError("原定发布时间已有其他投稿安排，请重新生成排期。")
        if fingerprint(job["video_path"]) != job["video_fingerprint"]:
            raise ValueError("成品视频已变化，请重新生成排期。")
        conn.execute("UPDATE bilibili_upload_jobs SET status = 'queued', error_message = NULL, return_code = NULL, "
                     "started_at = NULL, completed_at = NULL, upload_after = NULL WHERE id = ?", (job_id,))
    return get_batch(batch_id)
