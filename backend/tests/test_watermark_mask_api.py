from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import database, main, watermark_masks
from backend.tests.test_settings_and_api import configure_tmp_runtime


@dataclass(frozen=True)
class FakeRegion:
    x: int = 10
    y: int = 20
    width: int = 100
    height: int = 30
    sample_x: int = 10
    sample_y: int = 60


def _make_succeeded_bilibili_task(tmp_path: Path, task_id: str = "BV1xx411c7mD") -> str:
    task_id = database.create_task(f"https://www.bilibili.com/video/{task_id}", task_id=task_id)
    session = tmp_path / "workfolder" / "Uploader" / f"Title__{task_id}"
    media = session / "media"
    media.mkdir(parents=True)
    final_video = media / "video_final.mp4"
    final_video.write_bytes(b"video")
    database.update_task(
        task_id,
        status="succeeded",
        session_path=str(session),
        final_video_path=str(final_video),
        completed_at=database.now_iso(),
    )
    return task_id


def test_create_watermark_mask_job_for_bilibili_task(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    enqueued: list[str] = []
    monkeypatch.setattr(main.watermark_masks, "enqueue", enqueued.append)
    task_id = _make_succeeded_bilibili_task(tmp_path)
    client = TestClient(main.app)

    response = client.post(f"/api/tasks/{task_id}/watermark-mask", json={"mode": "patch"})

    assert response.status_code == 202
    body = response.json()
    assert body["task_id"] == task_id
    assert body["mode"] == "patch"
    assert body["status"] == "queued"
    assert enqueued == [body["id"]]


def test_watermark_mask_rejects_non_bilibili_task(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    task_id = database.create_task("https://www.youtube.com/watch?v=notbilibili1", task_id="notbilibili1")
    database.update_task(task_id, status="succeeded", final_video_path=str(tmp_path / "v.mp4"))
    client = TestClient(main.app)

    response = client.post(f"/api/tasks/{task_id}/watermark-mask", json={"mode": "patch"})

    assert response.status_code == 409


def test_watermark_mask_requires_succeeded_task(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    task_id = database.create_task("https://www.bilibili.com/video/BV1xx411c7mD", task_id="BV1xx411c7mD")
    client = TestClient(main.app)

    response = client.post(f"/api/tasks/{task_id}/watermark-mask", json={"mode": "patch"})

    assert response.status_code == 409


def test_watermark_mask_requires_final_video(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    task_id = database.create_task("https://www.bilibili.com/video/BV1xx411c7mD", task_id="BV1xx411c7mD")
    session = tmp_path / "workfolder" / "session"
    session.mkdir(parents=True)
    database.update_task(task_id, status="succeeded", session_path=str(session))
    client = TestClient(main.app)

    response = client.post(f"/api/tasks/{task_id}/watermark-mask", json={"mode": "patch"})

    assert response.status_code == 404


def test_watermark_mask_rejects_invalid_mode(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    task_id = _make_succeeded_bilibili_task(tmp_path)
    client = TestClient(main.app)

    response = client.post(f"/api/tasks/{task_id}/watermark-mask", json={"mode": "bad"})

    assert response.status_code == 422


def test_watermark_mask_rejects_duplicate_active_job(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    task_id = _make_succeeded_bilibili_task(tmp_path)
    task = database.get_task(task_id)
    database.create_watermark_mask_job(
        task_id,
        mode="patch",
        input_video_path=task["final_video_path"],
        output_video_path=str(Path(task["session_path"]) / "media" / "video_final_watermark_masked_patch.mp4"),
    )
    client = TestClient(main.app)

    response = client.post(f"/api/tasks/{task_id}/watermark-mask", json={"mode": "blur"})

    assert response.status_code == 409


def test_get_watermark_mask_job_and_log(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    task_id = _make_succeeded_bilibili_task(tmp_path)
    task = database.get_task(task_id)
    job_id = database.create_watermark_mask_job(
        task_id,
        mode="patch",
        input_video_path=task["final_video_path"],
        output_video_path=str(Path(task["session_path"]) / "media" / "video_final_watermark_masked_patch.mp4"),
    )
    log_path = database.watermark_mask_log_path(job_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("mask log", encoding="utf-8")
    client = TestClient(main.app)

    job_response = client.get(f"/api/watermark-mask-jobs/{job_id}")
    log_response = client.get(f"/api/watermark-mask-jobs/{job_id}/log")

    assert job_response.status_code == 200
    assert job_response.json()["id"] == job_id
    assert log_response.status_code == 200
    assert log_response.text == "mask log"


def test_watermark_mask_worker_marks_success_and_updates_final_path(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    task_id = _make_succeeded_bilibili_task(tmp_path)
    task = database.get_task(task_id)
    output = Path(task["session_path"]) / "media" / "video_final_watermark_masked_patch.mp4"
    job_id = database.create_watermark_mask_job(
        task_id,
        mode="patch",
        input_video_path=task["final_video_path"],
        output_video_path=str(output),
    )

    def fake_mask(video, session, mode):
        assert mode == "patch"
        output.write_bytes(b"masked")
        return output, FakeRegion()

    import backend.app.adapters.watermark_mask as adapter

    monkeypatch.setattr(adapter, "mask_bilibili_watermark", fake_mask)

    watermark_masks._run_job(job_id)

    job = database.get_watermark_mask_job(job_id)
    updated_task = database.get_task(task_id)
    assert job["status"] == "succeeded"
    assert job["return_code"] == 0
    assert updated_task["final_video_path"] == str(output)


def test_watermark_mask_worker_marks_failure(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    task_id = _make_succeeded_bilibili_task(tmp_path)
    task = database.get_task(task_id)
    job_id = database.create_watermark_mask_job(
        task_id,
        mode="patch",
        input_video_path=task["final_video_path"],
        output_video_path=str(Path(task["session_path"]) / "media" / "video_final_watermark_masked_patch.mp4"),
    )

    def fake_mask(video, session, mode):
        raise RuntimeError("boom")

    import backend.app.adapters.watermark_mask as adapter

    monkeypatch.setattr(adapter, "mask_bilibili_watermark", fake_mask)

    watermark_masks._run_job(job_id)

    job = database.get_watermark_mask_job(job_id)
    assert job["status"] == "failed"
    assert job["error_message"] == "boom"
