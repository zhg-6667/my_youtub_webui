from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import bilibili_uploads, database, main
from backend.tests.test_settings_and_api import configure_tmp_runtime
from scripts.upload_bilibili import UploadBilibiliError


@dataclass(frozen=True)
class FakeUploadResult:
    return_code: int = 0
    stdout: str = "uploaded"
    stderr: str = ""


def _make_succeeded_task(tmp_path: Path, task_id: str = "biliupload01") -> str:
    task_id = database.create_task(f"https://www.youtube.com/watch?v={task_id}", task_id=task_id)
    session = tmp_path / "workfolder" / "Uploader" / f"Title__{task_id}"
    media = session / "media"
    metadata = session / "metadata"
    media.mkdir(parents=True)
    metadata.mkdir()
    final_video = media / "video_final_trimmed.mp4"
    final_video.write_bytes(b"video")
    (metadata / "ytdlp_info.json").write_text(
        '{"id":"biliupload01","uploader":"WolfeyVGC","webpage_url":"https://www.youtube.com/watch?v=biliupload01"}',
        encoding="utf-8",
    )
    database.update_task(
        task_id,
        status="succeeded",
        session_path=str(session),
        final_video_path=str(final_video),
        completed_at=database.now_iso(),
    )
    return task_id


def test_create_bilibili_upload_job(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    enqueued: list[str] = []
    monkeypatch.setattr(main.bilibili_uploads, "enqueue", enqueued.append)
    task_id = _make_succeeded_task(tmp_path)
    client = TestClient(main.app)

    response = client.post(
        f"/api/tasks/{task_id}/bilibili-upload",
        json={"title": "测试标题", "publish_mode": "now"},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["task_id"] == task_id
    assert body["title"] == "测试标题"
    assert body["publish_mode"] == "now"
    assert body["cover_path"] is None
    assert body["status"] == "queued"
    assert enqueued == [body["id"]]


def test_create_bilibili_upload_job_with_cover(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    enqueued: list[str] = []
    monkeypatch.setattr(main.bilibili_uploads, "enqueue", enqueued.append)
    task_id = _make_succeeded_task(tmp_path)
    client = TestClient(main.app)

    response = client.post(
        f"/api/tasks/{task_id}/bilibili-upload",
        data={"title": "测试标题", "publish_mode": "now"},
        files={"cover_file": ("cover.png", b"cover image", "image/png")},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["task_id"] == task_id
    assert body["cover_path"].endswith(".png")
    assert Path(body["cover_path"]).read_bytes() == b"cover image"
    assert enqueued == [body["id"]]


def test_create_bilibili_upload_job_uses_concat_video_when_trimmed_missing(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    enqueued: list[str] = []
    monkeypatch.setattr(main.bilibili_uploads, "enqueue", enqueued.append)
    task_id = database.create_task("https://www.youtube.com/watch?v=concat01", task_id="concat01")
    session = tmp_path / "workfolder" / "Uploader" / "Title__concat01"
    media = session / "media"
    media.mkdir(parents=True)
    (media / "video_final_with_cta.mp4").write_bytes(b"concat")
    database.update_task(
        task_id,
        status="succeeded",
        session_path=str(session),
        final_video_path=None,
        completed_at=database.now_iso(),
    )
    client = TestClient(main.app)

    response = client.post(
        f"/api/tasks/{task_id}/bilibili-upload",
        json={"title": "测试标题", "publish_mode": "now"},
    )

    assert response.status_code == 202
    assert enqueued == [response.json()["id"]]


def test_create_bilibili_upload_job_uses_source_video_when_outputs_missing(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    enqueued: list[str] = []
    monkeypatch.setattr(main.bilibili_uploads, "enqueue", enqueued.append)
    task_id = database.create_task("https://www.youtube.com/watch?v=source01", task_id="source01")
    session = tmp_path / "workfolder" / "Uploader" / "Title__source01"
    media = session / "media"
    media.mkdir(parents=True)
    (media / "video_source.mp4").write_bytes(b"source")
    database.update_task(
        task_id,
        status="succeeded",
        session_path=str(session),
        final_video_path=None,
        completed_at=database.now_iso(),
    )
    client = TestClient(main.app)

    response = client.post(
        f"/api/tasks/{task_id}/bilibili-upload",
        json={"title": "测试标题", "publish_mode": "now"},
    )

    assert response.status_code == 202
    assert enqueued == [response.json()["id"]]


def test_create_bilibili_upload_allows_non_succeeded_task_with_video(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    enqueued: list[str] = []
    monkeypatch.setattr(main.bilibili_uploads, "enqueue", enqueued.append)
    task_id = database.create_task("https://www.youtube.com/watch?v=notdone12345", task_id="notdone12345")
    session = tmp_path / "workfolder" / "Uploader" / "Title__notdone12345"
    media = session / "media"
    media.mkdir(parents=True)
    (media / "video_source.mp4").write_bytes(b"source")
    database.update_task(task_id, status="paused", session_path=str(session))
    client = TestClient(main.app)

    response = client.post(
        f"/api/tasks/{task_id}/bilibili-upload",
        json={"title": "测试标题", "publish_mode": "now"},
    )

    assert response.status_code == 202
    assert enqueued == [response.json()["id"]]


def test_create_bilibili_upload_requires_title(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    task_id = _make_succeeded_task(tmp_path)
    client = TestClient(main.app)

    response = client.post(
        f"/api/tasks/{task_id}/bilibili-upload",
        json={"title": "   ", "publish_mode": "now"},
    )

    assert response.status_code == 422


def test_create_bilibili_upload_requires_dtime_for_scheduled(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    task_id = _make_succeeded_task(tmp_path)
    client = TestClient(main.app)

    response = client.post(
        f"/api/tasks/{task_id}/bilibili-upload",
        json={"title": "测试标题", "publish_mode": "scheduled"},
    )

    assert response.status_code == 422


def test_create_bilibili_upload_rejects_duplicate_active_job(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    task_id = _make_succeeded_task(tmp_path)
    database.create_bilibili_upload_job(task_id, title="旧标题", publish_mode="now")
    client = TestClient(main.app)

    response = client.post(
        f"/api/tasks/{task_id}/bilibili-upload",
        json={"title": "新标题", "publish_mode": "now"},
    )

    assert response.status_code == 409


def test_get_bilibili_upload_job_and_log(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    task_id = _make_succeeded_task(tmp_path)
    job_id = database.create_bilibili_upload_job(task_id, title="标题", publish_mode="now")
    log_path = database.bilibili_upload_log_path(job_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("hello", encoding="utf-8")
    client = TestClient(main.app)

    job_response = client.get(f"/api/bilibili-upload-jobs/{job_id}")
    log_response = client.get(f"/api/bilibili-upload-jobs/{job_id}/log")

    assert job_response.status_code == 200
    assert job_response.json()["id"] == job_id
    assert log_response.status_code == 200
    assert log_response.text == "hello"


def test_bilibili_upload_worker_marks_success(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    task_id = _make_succeeded_task(tmp_path)
    job_id = database.create_bilibili_upload_job(task_id, title="标题", publish_mode="now")
    database.save_bilibili_settings(
        author_config_json='{"WolfeyVGC":{"tags":"狼叔,宝可梦,VGC","tid":201}}'
    )

    def fake_upload(options):
        assert options.title == "标题"
        assert options.author_config_json == '{"WolfeyVGC":{"tags":"狼叔,宝可梦,VGC","tid":201}}'
        return FakeUploadResult()

    import scripts.upload_bilibili as upload_script

    monkeypatch.setattr(upload_script, "upload_bilibili", fake_upload)

    bilibili_uploads._run_job(job_id)

    job = database.get_bilibili_upload_job(job_id)
    assert job["status"] == "succeeded"
    assert job["return_code"] == 0
    assert Path(job["log_path"]).read_text(encoding="utf-8") == "uploaded\n"


def test_bilibili_upload_worker_passes_cover(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    task_id = _make_succeeded_task(tmp_path)
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"cover")
    job_id = database.create_bilibili_upload_job(
        task_id,
        title="标题",
        publish_mode="now",
        cover_path=str(cover),
    )

    def fake_upload(options):
        assert options.cover == str(cover)
        return FakeUploadResult()

    import scripts.upload_bilibili as upload_script

    monkeypatch.setattr(upload_script, "upload_bilibili", fake_upload)

    bilibili_uploads._run_job(job_id)

    assert database.get_bilibili_upload_job(job_id)["status"] == "succeeded"


def test_bilibili_upload_worker_marks_failure(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    task_id = _make_succeeded_task(tmp_path)
    job_id = database.create_bilibili_upload_job(task_id, title="标题", publish_mode="now")

    def fake_upload(options):
        raise UploadBilibiliError("boom", return_code=2, stderr="bad")

    import scripts.upload_bilibili as upload_script

    monkeypatch.setattr(upload_script, "upload_bilibili", fake_upload)

    bilibili_uploads._run_job(job_id)

    job = database.get_bilibili_upload_job(job_id)
    assert job["status"] == "failed"
    assert job["return_code"] == 2
    assert job["error_message"] == "boom"
    assert "bad" in Path(job["log_path"]).read_text(encoding="utf-8")
