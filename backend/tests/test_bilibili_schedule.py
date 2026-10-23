from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.app import bilibili_cover, bilibili_schedule, bilibili_uploads, database, main
from backend.tests.test_bilibili_upload_api import FakeUploadResult, _make_succeeded_task
from backend.tests.test_settings_and_api import configure_tmp_runtime


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    # Beijing 2026-10-20 05:00: first release at 11:00 has six hours of lead.
    now = datetime(2026, 10, 19, 21, tzinfo=timezone.utc)
    monkeypatch.setattr(bilibili_schedule, "now_utc", lambda: now)
    enqueued = []
    monkeypatch.setattr(bilibili_uploads, "enqueue", enqueued.append)
    monkeypatch.setattr(bilibili_uploads, "_notify_completion", lambda *args: None)
    ids = [_make_succeeded_task(tmp_path, task_id) for task_id in ("old", "middle", "new")]
    for position, task_id in enumerate(ids):
        database.update_task(task_id, title=f"Video {task_id}", created_at=f"2026-10-{17 + position}T00:00:00+00:00")
    return TestClient(main.app), ids, enqueued, now


def preview(client, ids, **kwargs):
    response = client.post("/api/bilibili-publish-batches/preview", json={
        "task_ids": list(reversed(ids)), "start_date": "2026-10-20", "daily_times": ["19:00", "11:00", "11:00"], **kwargs,
    })
    assert response.status_code == 200, response.text
    return response.json()


def confirm(client, batch):
    return client.post(f"/api/bilibili-publish-batches/{batch['id']}/confirm")


def jobs():
    with database.connect() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM bilibili_upload_jobs ORDER BY rowid")]


@pytest.fixture
def batch_clock(runtime, monkeypatch):
    clock = {"now": runtime[3]}
    sleeps = []

    def advance(seconds):
        clock["now"] += timedelta(seconds=seconds)

    def sleep(seconds):
        sleeps.append(seconds)
        advance(seconds)

    monkeypatch.setattr(bilibili_uploads, "_utc_now", lambda: clock["now"])
    monkeypatch.setattr(bilibili_schedule, "now_utc", lambda: clock["now"])
    monkeypatch.setattr(bilibili_uploads, "time", SimpleNamespace(
        sleep=sleep, monotonic=lambda: clock["now"].timestamp(),
    ))
    return clock, sleeps, advance


def test_full_preview_is_oldest_first_and_does_not_upload(runtime):
    client, ids, enqueued, _ = runtime
    batch = preview(client, ids)
    assert batch["status"] == "draft"
    assert batch["timezone"] == "Asia/Shanghai"
    assert batch["start_time"] == "00:00"
    assert batch["daily_times"] == ["11:00", "19:00"]
    assert [item["task_id"] for item in batch["items"]] == ids
    assert [item["publish_at"] for item in batch["items"]] == [
        "2026-10-20T03:00:00+00:00", "2026-10-20T11:00:00+00:00", "2026-10-21T03:00:00+00:00",
    ]
    assert not jobs() and not enqueued
    assert client.get("/api/bilibili-publish-batches").json() == {"batches": []}


def test_confirm_keeps_publication_separate_from_upload_and_is_idempotent(runtime):
    client, ids, enqueued, _ = runtime
    batch = preview(client, ids)
    first = confirm(client, batch)
    assert first.status_code == 202, first.text
    assert enqueued == [item["job_id"] for item in first.json()["items"]]
    assert confirm(client, batch).json() == first.json()
    actual = jobs()
    assert len(actual) == 3
    assert [job["status"] for job in actual] == ["queued", "queued", "queued"]
    for job, item in zip(actual, batch["items"]):
        assert job["publish_mode"] == "scheduled"
        assert job["dtime"] == str(int(datetime.fromisoformat(item["publish_at"]).timestamp()))
        assert Path(job["video_path"]).name == "video_final_trimmed.mp4"
    assert all(job["upload_after"] is None for job in actual)
    assert set(enqueued) == {job["id"] for job in actual}
    summary = client.get("/api/tasks").json()["tasks"]
    assert all(task["bilibili_upload_blocked"] for task in summary)
    assert all(task["display_video_path"] for task in summary)
    assert len(client.get("/api/bilibili-publish-batches").json()["batches"]) == 1


def test_equal_creation_times_use_insertion_order(runtime):
    client, ids, _, _ = runtime
    for task_id in ids:
        database.update_task(task_id, created_at="2026-10-19T00:00:00+00:00")
    assert [item["task_id"] for item in preview(client, ids)["items"]] == ids


def test_skip_passed_insufficient_lead_and_occupied_slots(runtime, monkeypatch):
    client, ids, _, _ = runtime
    monkeypatch.setattr(bilibili_schedule, "now_utc", lambda: datetime(2026, 10, 20, 2, 40, tzinfo=timezone.utc))  # 10:40 Beijing
    database.create_bilibili_upload_job(
        "unrelated", title="Reserved", publish_mode="scheduled",
        dtime=str(int(datetime(2026, 10, 20, 11, tzinfo=timezone.utc).timestamp())),
    )
    batch = preview(client, ids[:1], daily_times=["09:00", "11:00", "19:00"])
    assert [slot["reason"] for slot in batch["skipped"]] == ["past", "lead", "occupied"]
    assert batch["items"][0]["publish_at"] == "2026-10-21T01:00:00+00:00"


@pytest.mark.parametrize("change", [
    {"daily_times": []}, {"daily_times": ["24:00"]}, {"daily_times": ["9:00"]},
    {"daily_times": ["11:60"]}, {"start_date": "2026-02-30"}, {"start_date": "2026-10-19"},
    {"task_ids": ["missing"]}, {"task_ids": ["old", "old"]},
    {"start_time": ""}, {"start_time": "9:00"}, {"start_time": "24:00"},
    {"start_time": "11:60"}, {"start_time": "11:00:00"},
])
def test_invalid_requests_create_no_jobs(runtime, change):
    client, ids, _, _ = runtime
    response = client.post("/api/bilibili-publish-batches/preview", json={
        "task_ids": ids, "start_date": "2026-10-20", "daily_times": ["11:00"], **change,
    })
    assert response.status_code == 422
    assert not jobs()


@pytest.mark.parametrize("status", ["queued", "running", "failed", "paused"])
def test_unfinished_tasks_are_ineligible(runtime, status):
    client, ids, _, _ = runtime
    database.update_task(ids[0], status=status)
    assert client.post("/api/bilibili-publish-batches/preview", json={
        "task_ids": ids, "start_date": "2026-10-20", "daily_times": ["11:00"],
    }).status_code == 422


def test_preview_expiring_does_not_silently_move_publication(runtime, monkeypatch):
    client, ids, enqueued, now = runtime
    batch = preview(client, ids)
    monkeypatch.setattr(bilibili_schedule, "now_utc", lambda: now + timedelta(hours=5, minutes=31))
    response = confirm(client, batch)
    assert response.status_code == 409
    assert not jobs() and not enqueued
    unchanged = client.get(f"/api/bilibili-publish-batches/{batch['id']}").json()
    assert unchanged["items"] == batch["items"]


@pytest.mark.parametrize("change", ["title", "video", "cover", "missing", "status"])
def test_confirmation_revalidates_entire_preview(runtime, change):
    client, ids, enqueued, _ = runtime
    batch = preview(client, ids)
    task = database.get_task(ids[1])
    if change == "title":
        database.update_task(ids[1], bilibili_upload_title="Changed")
    elif change == "video":
        Path(task["final_video_path"]).write_bytes(b"changed video")
    elif change == "missing":
        Path(task["final_video_path"]).unlink()
    elif change == "cover":
        (Path(task["session_path"]) / "cover.jpg").write_bytes(b"new cover")
    else:
        database.update_task(ids[1], status="running")
    assert confirm(client, batch).status_code == 409
    assert not jobs() and not enqueued


def test_cover_snapshot_failure_rolls_back_whole_batch(runtime, monkeypatch):
    client, ids, enqueued, _ = runtime
    for task_id in ids:
        (Path(database.get_task(task_id)["session_path"]) / "cover.jpg").write_bytes(b"cover")
    batch = preview(client, ids)
    snapshot = bilibili_cover.snapshot_cover_for_job
    calls = []

    def fail_second(session, job_id):
        calls.append(job_id)
        if len(calls) == 2:
            raise OSError("Simulated copy failure")
        return snapshot(session, job_id)

    monkeypatch.setattr(bilibili_cover, "snapshot_cover_for_job", fail_second)
    assert confirm(client, batch).status_code == 409
    assert not jobs() and not enqueued
    assert not list(Path(database.get_task(ids[0])["session_path"]).glob("bilibili_cover/jobs/*"))


def test_second_preview_cannot_duplicate_committed_uploads(runtime):
    client, ids, _, _ = runtime
    first, second = preview(client, ids), preview(client, ids)
    assert confirm(client, first).status_code == 202
    assert confirm(client, second).status_code == 409
    assert len(jobs()) == 3


def test_conflicting_slot_requires_new_preview(runtime):
    client, ids, _, _ = runtime
    first, second = preview(client, ids[:1]), preview(client, ids[1:2])
    assert confirm(client, first).status_code == 202
    assert confirm(client, second).status_code == 409
    replacement = preview(client, ids[1:2])
    assert replacement["skipped"][0]["reason"] == "occupied"
    assert replacement["items"][0]["publish_at"] == "2026-10-20T11:00:00+00:00"


def test_cancel_pending_blocks_stale_queue_delivery_and_running_jobs_are_kept(runtime, monkeypatch):
    client, ids, _, _ = runtime
    batch = confirm(client, preview(client, ids)).json()
    running = batch["items"][0]["job_id"]
    assert database.claim_bilibili_upload_job(running)
    assert not database.claim_bilibili_upload_job(running)
    response = client.post(f"/api/bilibili-publish-batches/{batch['id']}/cancel-pending")
    assert response.status_code == 200
    assert [item["status"] for item in response.json()["items"]] == ["running", "cancelled", "cancelled"]
    for item in response.json()["items"][1:]:
        bilibili_uploads._run_job(item["job_id"])
        assert database.get_bilibili_upload_job(item["job_id"])["status"] == "cancelled"


def test_worker_uploads_pinned_video_with_exact_publication_timestamp(runtime, monkeypatch):
    client, ids, _, _ = runtime
    batch = confirm(client, preview(client, ids)).json()
    job_id = batch["items"][0]["job_id"]
    captured = []
    monkeypatch.setattr("scripts.upload_bilibili.upload_bilibili", lambda options: captured.append(options) or FakeUploadResult())
    bilibili_uploads._run_job(job_id)
    bilibili_uploads._run_job(job_id)
    assert len(captured) == 1
    assert captured[0].path == database.get_task(ids[0])["final_video_path"]
    assert captured[0].dtime == str(int(datetime.fromisoformat(batch["items"][0]["publish_at"]).timestamp()))
    assert captured[0].title == batch["items"][0]["title"]
    assert captured[0].title_is_final
    assert not captured[0].use_default_cover
    assert database.get_bilibili_upload_job(job_id)["status"] == "succeeded"


@pytest.mark.parametrize("problem", ["late", "video"])
def test_worker_never_falls_back_to_immediate_publication(runtime, monkeypatch, problem):
    client, ids, _, now = runtime
    batch = confirm(client, preview(client, ids)).json()
    job_id = batch["items"][0]["job_id"]
    if problem == "late":
        monkeypatch.setattr(bilibili_schedule, "now_utc", lambda: now + timedelta(hours=5, minutes=31))
    else:
        Path(database.get_task(ids[0])["final_video_path"]).write_bytes(b"changed")
    monkeypatch.setattr("scripts.upload_bilibili.upload_bilibili", lambda options: pytest.fail("Must not submit"))
    bilibili_uploads._run_job(job_id)
    assert database.get_bilibili_upload_job(job_id)["status"] == "failed"


def test_retry_preserves_publication_time_and_rejects_known_submission(runtime):
    client, ids, _, _ = runtime
    batch = confirm(client, preview(client, ids)).json()
    job_id = batch["items"][0]["job_id"]
    original = database.get_bilibili_upload_job(job_id)["dtime"]
    database.update_bilibili_upload_job(job_id, status="failed", error_message="Temporary failure")
    url = f"/api/bilibili-publish-batches/{batch['id']}/jobs/{job_id}/retry"
    assert client.post(url).status_code == 202
    assert database.get_bilibili_upload_job(job_id)["dtime"] == original
    database.update_bilibili_upload_job(job_id, status="failed", bvid="BV123")
    assert client.post(url).status_code == 409


def test_restart_recovers_queued_and_legacy_waiting_batch_uploads(runtime, monkeypatch):
    client, ids, enqueued, _ = runtime
    batch = confirm(client, preview(client, ids)).json()
    last_job = batch["items"][-1]["job_id"]
    database.update_bilibili_upload_job(last_job, status="scheduled", upload_after="2999-07-01T01:00:00+00:00")

    class DormantThread:
        def __init__(self, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(bilibili_uploads, "threading", SimpleNamespace(Thread=DormantThread))
    monkeypatch.setattr(bilibili_uploads, "_thread", None)
    monkeypatch.setattr(bilibili_uploads, "_scheduler_thread", None)
    enqueued.clear()
    bilibili_uploads.start()
    assert enqueued == [item["job_id"] for item in batch["items"]]
    assert database.get_bilibili_upload_job(last_job)["status"] == "queued"
    assert database.get_bilibili_upload_job(last_job)["upload_after"] is None
    assert database.get_bilibili_upload_job(last_job)["dtime"] == "1792551600"
    assert database.queue_due_scheduled_bilibili_upload_jobs() == []
    enqueued.clear()
    bilibili_uploads.start()
    assert enqueued == []


def test_pending_schedules_protect_task_files(runtime):
    client, ids, _, _ = runtime
    confirm(client, preview(client, ids))
    assert client.delete(f"/api/tasks/{ids[0]}").status_code == 409
    assert Path(database.get_task(ids[0])["final_video_path"]).exists()


def test_missing_batch_returns_404(runtime):
    client, _, _, _ = runtime
    for path in ("/missing", "/missing/confirm", "/missing/cancel-pending"):
        call = client.get if path == "/missing" else client.post
        assert call(f"/api/bilibili-publish-batches{path}").status_code == 404


def test_confirm_snapshots_cover_and_task_publishing_defaults(runtime):
    client, ids, _, _ = runtime
    session = Path(database.get_task(ids[0])["session_path"])
    (session / "cover.jpg").write_bytes(b"original cover")
    database.update_task(ids[0], bilibili_upload_title="Saved title", bilibili_upload_description="Saved description",
                         bilibili_upload_tags="one,two", bilibili_upload_tid=171,
                         bilibili_topic_name="Saved topic", bilibili_topic_id=12, bilibili_topic_mission_id=34,
                         bilibili_season_id=56, bilibili_section_id=78)
    batch = preview(client, ids[:1])
    assert batch["items"][0]["title"] == "Saved title-WolfeyVGC"
    assert confirm(client, batch).status_code == 202
    (session / "cover.jpg").write_bytes(b"later cover")
    job = jobs()[0]
    assert Path(job["cover_path"]).read_bytes() == b"original cover"
    assert job["description"] == "Saved description"
    assert job["tags"] == "one,two"
    assert (job["tid"], job["topic_id"], job["topic_mission_id"], job["season_id"], job["section_id"]) == (171, 12, 34, 56, 78)


def test_retry_rejects_slot_claimed_after_failure(runtime):
    client, ids, _, _ = runtime
    batch = confirm(client, preview(client, ids[:1])).json()
    job_id = batch["items"][0]["job_id"]
    database.update_bilibili_upload_job(job_id, status="failed")
    assert confirm(client, preview(client, ids[1:2])).status_code == 202
    assert client.post(f"/api/bilibili-publish-batches/{batch['id']}/jobs/{job_id}/retry").status_code == 409


def test_cli_uses_previewed_title_and_does_not_pick_up_later_cover(runtime):
    from scripts.upload_bilibili import UploadBilibiliOptions, build_upload_command

    _, ids, _, _ = runtime
    task = database.get_task(ids[0])
    (Path(task["session_path"]) / "cover.jpg").write_bytes(b"cover added after confirmation")
    prepared = build_upload_command(UploadBilibiliOptions(
        path=task["final_video_path"], title="Fixed title-WolfeyVGC", title_is_final=True,
        use_default_cover=False, dtime="9999999999", biliup="biliup",
    ))
    assert prepared.title == "Fixed title-WolfeyVGC"
    assert "--cover" not in prepared.command
    assert prepared.command[prepared.command.index("--dtime") + 1] == "9999999999"


def test_submission_ids_survive_cli_error_to_prevent_duplicate_retry(runtime, monkeypatch):
    from scripts.upload_bilibili import UploadBilibiliError

    client, ids, _, _ = runtime
    batch = confirm(client, preview(client, ids[:1])).json()
    job_id = batch["items"][0]["job_id"]

    def fail(options):
        raise UploadBilibiliError("Error after submission", return_code=1, stdout='{"aid": 12345, "bvid": "BV1xx411c7mD"}', stderr="")

    monkeypatch.setattr("scripts.upload_bilibili.upload_bilibili", fail)
    bilibili_uploads._run_job(job_id)
    job = database.get_bilibili_upload_job(job_id)
    assert job["status"] == "failed"
    assert job["aid"] == 12345 and job["bvid"] == "BV1xx411c7mD"
    assert not client.get(f"/api/bilibili-publish-batches/{batch['id']}").json()["items"][0]["can_retry"]


def test_two_videos_at_1016_use_same_day_1100_and_1900(runtime, monkeypatch):
    client, ids, _, _ = runtime
    monkeypatch.setattr(bilibili_schedule, "now_utc", lambda: datetime(2026, 10, 20, 2, 16, tzinfo=timezone.utc))
    batch = preview(client, ids[:2])
    assert batch["min_lead_hours"] == 0.5
    assert not batch["skipped"]
    assert [item["publish_at"] for item in batch["items"]] == [
        "2026-10-20T03:00:00+00:00", "2026-10-20T11:00:00+00:00",
    ]
    assert confirm(client, batch).status_code == 202


@pytest.mark.parametrize("seconds_ahead,eligible", [(1799, False), (1800, True), (1801, True)])
def test_thirty_minute_boundary_applies_to_preview_confirm_and_worker(runtime, monkeypatch, seconds_ahead, eligible):
    client, ids, _, _ = runtime
    release = datetime(2026, 10, 20, 3, tzinfo=timezone.utc)
    monkeypatch.setattr(bilibili_schedule, "now_utc", lambda: release - timedelta(seconds=seconds_ahead))
    batch = preview(client, ids[:1])
    assert batch["items"][0]["publish_at"] == (
        "2026-10-20T03:00:00+00:00" if eligible else "2026-10-20T11:00:00+00:00"
    )
    assert [slot["reason"] for slot in batch["skipped"]] == ([] if eligible else ["lead"])
    confirmed = confirm(client, batch)
    assert confirmed.status_code == 202, confirmed.text
    captured = []
    monkeypatch.setattr("scripts.upload_bilibili.upload_bilibili", lambda options: captured.append(options) or FakeUploadResult())
    job_id = confirmed.json()["items"][0]["job_id"]
    bilibili_uploads._run_job(job_id)
    assert len(captured) == 1
    assert database.get_bilibili_upload_job(job_id)["status"] == "succeeded"


def test_retry_uses_thirty_minute_boundary(runtime, monkeypatch):
    client, ids, _, _ = runtime
    batch = confirm(client, preview(client, ids[:1])).json()
    job_id = batch["items"][0]["job_id"]
    url = f"/api/bilibili-publish-batches/{batch['id']}/jobs/{job_id}/retry"
    release = datetime.fromisoformat(batch["items"][0]["publish_at"])
    database.update_bilibili_upload_job(job_id, status="failed")
    monkeypatch.setattr(bilibili_schedule, "now_utc", lambda: release - timedelta(minutes=30))
    assert bilibili_schedule.get_batch(batch["id"])["items"][0]["can_retry"]
    assert client.post(url).status_code == 202
    database.update_bilibili_upload_job(job_id, status="failed")
    monkeypatch.setattr(bilibili_schedule, "now_utc", lambda: release - timedelta(minutes=30) + timedelta(seconds=1))
    assert not bilibili_schedule.get_batch(batch["id"])["items"][0]["can_retry"]
    assert client.post(url).status_code == 409


def test_far_future_publications_upload_immediately_on_confirmation(runtime):
    client, ids, enqueued, _ = runtime
    batch = preview(client, ids, start_date="2026-11-20")
    assert not jobs() and not enqueued
    assert "upload_lead_hours" not in batch
    response = confirm(client, batch)
    assert response.status_code == 202, response.text
    assert enqueued == [item["job_id"] for item in response.json()["items"]]
    for job, item in zip(jobs(), batch["items"]):
        assert job["status"] == "queued"
        assert job["upload_after"] is None
        assert job["publish_mode"] == "scheduled"
        assert job["dtime"] == str(int(datetime.fromisoformat(item["publish_at"]).timestamp()))


def test_legacy_waiting_batches_queue_now_without_changing_manual_upload_schedules(runtime):
    client, ids, enqueued, _ = runtime
    batch = confirm(client, preview(client, ids)).json()
    original_times = {job["id"]: job["dtime"] for job in jobs()}
    cancelled_id, *waiting_ids = [item["job_id"] for item in batch["items"]]
    future = "2999-07-01T01:00:00+00:00"
    database.update_bilibili_upload_job(cancelled_id, status="cancelled", upload_after=future)
    for job_id in waiting_ids:
        database.update_bilibili_upload_job(job_id, status="scheduled", upload_after=future)
    manual_job = database.create_bilibili_upload_job(
        "manual-upload-task", title="Manual upload", publish_mode="now", upload_after=future,
    )
    enqueued.clear()
    bilibili_uploads._enqueue_due_scheduled_jobs()
    assert enqueued == waiting_ids
    for job_id in waiting_ids:
        job = database.get_bilibili_upload_job(job_id)
        assert job["status"] == "queued"
        assert job["upload_after"] is None
        assert job["dtime"] == original_times[job_id]
    assert database.get_bilibili_upload_job(cancelled_id)["status"] == "cancelled"
    assert database.get_bilibili_upload_job(manual_job)["status"] == "scheduled"
    assert database.get_bilibili_upload_job(manual_job)["upload_after"] == future
    enqueued.clear()
    bilibili_uploads._enqueue_due_scheduled_jobs()
    assert enqueued == []


def test_existing_draft_uses_immediate_upload_policy_without_moving_publication(runtime):
    client, ids, enqueued, _ = runtime
    batch = preview(client, ids)
    with database.connect() as conn:
        row = conn.execute("SELECT preview_json FROM bilibili_publish_batches WHERE id = ?", (batch["id"],)).fetchone()
        saved = json.loads(row["preview_json"])
        saved["upload_lead_hours"] = 24
        saved.pop("start_time")
        conn.execute("UPDATE bilibili_publish_batches SET preview_json = ? WHERE id = ?", (json.dumps(saved), batch["id"]))
    assert "upload_lead_hours" not in client.get(f"/api/bilibili-publish-batches/{batch['id']}").json()
    assert client.get(f"/api/bilibili-publish-batches/{batch['id']}").json()["start_time"] == "00:00"
    response = confirm(client, batch)
    assert response.status_code == 202, response.text
    assert [item["publish_at"] for item in response.json()["items"]] == [item["publish_at"] for item in batch["items"]]
    assert enqueued == [item["job_id"] for item in response.json()["items"]]
    assert all(job["status"] == "queued" and job["upload_after"] is None for job in jobs())


@pytest.mark.parametrize("start_time,expected,skipped_count", [
    ("11:00", ["2026-10-20T03:00:00+00:00", "2026-10-20T11:00:00+00:00"], 0),
    ("12:00", ["2026-10-20T11:00:00+00:00", "2026-10-21T03:00:00+00:00"], 1),
    ("19:00", ["2026-10-20T11:00:00+00:00", "2026-10-21T03:00:00+00:00"], 1),
    ("19:01", ["2026-10-21T03:00:00+00:00", "2026-10-21T11:00:00+00:00"], 2),
])
def test_selected_start_time_limits_first_day_only(runtime, start_time, expected, skipped_count):
    client, ids, enqueued, _ = runtime
    batch = preview(client, ids[:2], start_time=start_time)
    assert batch["start_time"] == start_time
    assert [item["task_id"] for item in batch["items"]] == ids[:2]
    assert [item["publish_at"] for item in batch["items"]] == expected
    assert [slot["reason"] for slot in batch["skipped"]] == ["before_start"] * skipped_count
    assert not enqueued
    response = confirm(client, batch)
    assert response.status_code == 202, response.text
    assert response.json()["start_time"] == start_time
    assert [item["publish_at"] for item in response.json()["items"]] == expected
    assert enqueued == [item["job_id"] for item in response.json()["items"]]
    assert all(job["upload_after"] is None for job in jobs())


def test_start_date_and_time_use_beijing_time_for_future_day(runtime):
    client, ids, _, _ = runtime
    batch = preview(client, ids[:2], start_date="2026-10-21", start_time="12:00")
    assert [item["publish_at"] for item in batch["items"]] == [
        "2026-10-21T11:00:00+00:00", "2026-10-22T03:00:00+00:00",
    ]
    assert batch["skipped"] == [{"publish_at": "2026-10-21T11:00:00+08:00", "reason": "before_start"}]
    saved = client.get(f"/api/bilibili-publish-batches/{batch['id']}").json()
    assert saved["start_date"] == "2026-10-21"
    assert saved["start_time"] == "12:00"


def test_selected_start_time_does_not_bypass_minimum_publication_lead(runtime, monkeypatch):
    client, ids, _, _ = runtime
    monkeypatch.setattr(bilibili_schedule, "now_utc", lambda: datetime(2026, 10, 20, 2, 40, tzinfo=timezone.utc))
    batch = preview(client, ids[:1], start_time="10:20")
    assert batch["skipped"] == [{"publish_at": "2026-10-20T11:00:00+08:00", "reason": "lead"}]
    assert batch["items"][0]["publish_at"] == "2026-10-20T11:00:00+00:00"


@pytest.mark.parametrize("first_fails", [False, True])
def test_batch_upload_waits_sixty_seconds_after_previous_execution_finishes(runtime, batch_clock, monkeypatch, first_fails):
    from scripts.upload_bilibili import UploadBilibiliError

    client, ids, _, _ = runtime
    clock, sleeps, advance = batch_clock
    batch = confirm(client, preview(client, ids)).json()
    starts, finishes = [], []

    def upload(options):
        starts.append(clock["now"])
        advance(7)
        finishes.append(clock["now"])
        if first_fails and len(starts) == 1:
            raise UploadBilibiliError("Temporary failure")
        return FakeUploadResult()

    monkeypatch.setattr("scripts.upload_bilibili.upload_bilibili", upload)
    first, second, _ = [item["job_id"] for item in batch["items"]]
    bilibili_uploads._run_job(first)
    assert sleeps == []
    assert database.get_bilibili_upload_job(first)["status"] == ("failed" if first_fails else "succeeded")
    bilibili_uploads._run_job(second)
    assert starts[1] - finishes[0] == timedelta(seconds=60)
    assert sum(sleeps) == 60
    assert database.get_bilibili_upload_job(second)["status"] == "succeeded"
    assert "[批量上传]" in Path(database.get_bilibili_upload_job(second)["log_path"]).read_text(encoding="utf-8")


def test_batch_upload_restart_only_waits_remaining_interval(runtime, batch_clock, monkeypatch):
    client, ids, _, _ = runtime
    clock, sleeps, advance = batch_clock
    batch = confirm(client, preview(client, ids[:2])).json()
    monkeypatch.setattr("scripts.upload_bilibili.upload_bilibili", lambda options: FakeUploadResult())
    first, second = [item["job_id"] for item in batch["items"]]
    bilibili_uploads._run_job(first)
    ready_at = database.get_setting(bilibili_uploads.BATCH_UPLOAD_READY_AT_SETTING)
    assert datetime.fromisoformat(ready_at) == clock["now"] + timedelta(seconds=60)
    # Duplicate queue delivery must not move the saved deadline.
    bilibili_uploads._run_job(first)
    assert database.get_setting(bilibili_uploads.BATCH_UPLOAD_READY_AT_SETTING) == ready_at
    advance(20)
    database.init_db()
    bilibili_uploads._run_job(second)
    assert sum(sleeps) == 40


def test_new_batch_after_idle_time_starts_without_extra_delay(runtime, batch_clock, monkeypatch):
    client, ids, _, _ = runtime
    _, sleeps, advance = batch_clock
    first_batch = confirm(client, preview(client, ids[:1])).json()
    monkeypatch.setattr("scripts.upload_bilibili.upload_bilibili", lambda options: FakeUploadResult())
    bilibili_uploads._run_job(first_batch["items"][0]["job_id"])
    advance(90)
    second_batch = confirm(client, preview(client, ids[1:2])).json()
    bilibili_uploads._run_job(second_batch["items"][0]["job_id"])
    assert sleeps == []


def test_cancelling_during_interval_does_not_upload_or_extend_wait(runtime, batch_clock, monkeypatch):
    client, ids, _, _ = runtime
    clock, sleeps, advance = batch_clock
    batch = confirm(client, preview(client, ids)).json()
    first, second, third = [item["job_id"] for item in batch["items"]]
    uploads = []
    monkeypatch.setattr("scripts.upload_bilibili.upload_bilibili", lambda options: uploads.append(options.path) or FakeUploadResult())
    bilibili_uploads._run_job(first)
    ready_at = database.get_setting(bilibili_uploads.BATCH_UPLOAD_READY_AT_SETTING)

    def cancel_during_sleep(seconds):
        sleeps.append(seconds)
        advance(seconds)
        database.update_bilibili_upload_job(second, status="cancelled")

    monkeypatch.setattr(bilibili_uploads.time, "sleep", cancel_during_sleep)
    bilibili_uploads._run_job(second)
    assert sum(sleeps) == 1
    assert len(uploads) == 1
    assert database.get_bilibili_upload_job(second)["started_at"] is None
    assert database.get_setting(bilibili_uploads.BATCH_UPLOAD_READY_AT_SETTING) == ready_at
    bilibili_uploads._run_job(third)
    assert sum(sleeps) == 60
    assert len(uploads) == 2
    assert database.get_bilibili_upload_job(third)["status"] == "succeeded"


def test_retry_of_same_batch_job_honors_interval(runtime, batch_clock, monkeypatch):
    from scripts.upload_bilibili import UploadBilibiliError

    client, ids, _, _ = runtime
    _, sleeps, _ = batch_clock
    batch = confirm(client, preview(client, ids[:1])).json()
    job_id = batch["items"][0]["job_id"]

    def fail(options):
        raise UploadBilibiliError("Temporary failure")

    monkeypatch.setattr("scripts.upload_bilibili.upload_bilibili", fail)
    bilibili_uploads._run_job(job_id)
    assert client.post(f"/api/bilibili-publish-batches/{batch['id']}/jobs/{job_id}/retry").status_code == 202
    monkeypatch.setattr("scripts.upload_bilibili.upload_bilibili", lambda options: FakeUploadResult())
    bilibili_uploads._run_job(job_id)
    assert sum(sleeps) == 60
    assert database.get_bilibili_upload_job(job_id)["status"] == "succeeded"


def test_manual_upload_does_not_add_another_batch_interval(runtime, batch_clock, monkeypatch):
    client, ids, _, _ = runtime
    _, sleeps, advance = batch_clock
    batch = confirm(client, preview(client, ids[:2])).json()
    first, second = [item["job_id"] for item in batch["items"]]

    def upload(options):
        advance(10)
        return FakeUploadResult()

    monkeypatch.setattr("scripts.upload_bilibili.upload_bilibili", upload)
    bilibili_uploads._run_job(first)
    ready_at = database.get_setting(bilibili_uploads.BATCH_UPLOAD_READY_AT_SETTING)
    manual_job = database.create_bilibili_upload_job(ids[2], title="Manual", publish_mode="now")
    bilibili_uploads._run_job(manual_job)
    assert sleeps == []
    assert database.get_setting(bilibili_uploads.BATCH_UPLOAD_READY_AT_SETTING) == ready_at
    bilibili_uploads._run_job(second)
    assert sum(sleeps) == 50
