from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import bilibili_uploads, database, main
from backend.tests.test_settings_and_api import configure_tmp_runtime
from scripts.upload_bilibili import BilibiliTopic, UploadBilibiliError


@dataclass(frozen=True)
class FakeUploadResult:
    return_code: int = 0
    stdout: str = "uploaded"
    stderr: str = ""
    aid: int | None = None
    bvid: str | None = None
    title: str = "标题WolfeyVGC"
    topic_name: str = ""
    topic_id: int | None = None
    topic_mission_id: int | None = None


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
        (
            '{"id":"biliupload01","uploader":"WolfeyVGC","upload_date":"20260731",'
            '"webpage_url":"https://www.youtube.com/watch?v=biliupload01"}'
        ),
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
        json={
            "title": "测试标题",
            "description": "手动投稿简介",
            "tags": "标签一,标签二",
            "tid": 171,
            "topic_name": "宝可梦集结",
            "publish_mode": "now",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["task_id"] == task_id
    assert body["title"] == "测试标题"
    assert body["description"] == "手动投稿简介"
    assert body["tags"] == "标签一,标签二"
    assert body["tid"] == 171
    assert body["topic_name"] == "宝可梦集结"
    assert body["topic_id"] is None
    assert body["season_id"] is None
    assert body["section_id"] is None
    assert body["publish_mode"] == "now"
    assert body["cover_path"] is None
    assert body["status"] == "queued"
    assert enqueued == [body["id"]]


def test_bilibili_cover_editor_persists_priority_and_upload_snapshot(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(main.bilibili_uploads, "enqueue", lambda task_id: None)
    task_id = _make_succeeded_task(tmp_path)
    session = Path(database.get_task(task_id)["session_path"])
    (session / "cover.jpg").write_bytes(b"source-cover")
    client = TestClient(main.app)

    initial = client.get(f"/api/tasks/{task_id}/bilibili-cover")
    assert initial.status_code == 200
    assert initial.json()["source"] == "source"

    manual = client.put(
        f"/api/tasks/{task_id}/bilibili-cover/manual",
        files={"file": ("manual.png", b"manual-cover", "image/png")},
    )
    assert manual.status_code == 200
    assert manual.json()["source"] == "manual"
    assert (session / "bilibili_cover" / "manual.png").read_bytes() == b"manual-cover"

    edited = client.put(
        f"/api/tasks/{task_id}/bilibili-cover/edited",
        data={
            "project_json": (
                '{"text":"Hello","font_family":"Arial","font_size":88,'
                '"color":"#ffffff","x":0.5,"y":0.2}'
            )
        },
        files={"file": ("edited.jpg", b"edited-cover", "image/jpeg")},
    )
    assert edited.status_code == 200
    assert edited.json()["source"] == "edited"
    assert edited.json()["project"]["text"] == "Hello"
    assert client.get(f"/api/tasks/{task_id}/artifact/bilibili-cover").content == b"edited-cover"

    upload = client.post(
        f"/api/tasks/{task_id}/bilibili-upload",
        json={"title": "测试标题", "publish_mode": "now"},
    )
    assert upload.status_code == 202
    job_cover = Path(upload.json()["cover_path"])
    assert job_cover == session / "bilibili_cover" / "jobs" / f"{upload.json()['id']}.jpg"
    assert job_cover.read_bytes() == b"edited-cover"

    reset = client.delete(f"/api/tasks/{task_id}/bilibili-cover/edited")
    assert reset.status_code == 200
    assert reset.json()["source"] == "manual"
    assert client.get(f"/api/tasks/{task_id}/artifact/bilibili-cover").content == b"manual-cover"

    clear_manual = client.delete(f"/api/tasks/{task_id}/bilibili-cover/manual")
    assert clear_manual.status_code == 200
    assert clear_manual.json()["source"] == "source"
    assert client.get(f"/api/tasks/{task_id}/artifact/bilibili-cover").content == b"source-cover"


def test_create_bilibili_upload_job_with_season(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(main.bilibili_uploads, "enqueue", lambda task_id: None)
    task_id = _make_succeeded_task(tmp_path)
    client = TestClient(main.app)

    response = client.post(
        f"/api/tasks/{task_id}/bilibili-upload",
        json={
            "title": "测试标题",
            "publish_mode": "now",
            "season_id": 7320255,
            "section_id": 8081933,
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["season_id"] == 7320255
    assert body["section_id"] == 8081933


def test_create_bilibili_upload_job_with_resolved_topic(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(main.bilibili_uploads, "enqueue", lambda task_id: None)
    task_id = _make_succeeded_task(tmp_path)
    client = TestClient(main.app)

    response = client.post(
        f"/api/tasks/{task_id}/bilibili-upload",
        json={
            "title": "测试标题",
            "publish_mode": "now",
            "topic_name": "宝可梦集结",
            "topic_id": 1245603,
            "topic_mission_id": 456,
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["topic_name"] == "宝可梦集结"
    assert body["topic_id"] == 1245603
    assert body["topic_mission_id"] == 456


def test_resolve_bilibili_topic_api(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)

    def fake_resolve(name):
        assert name == "宝可梦集结"
        return BilibiliTopic(id=1245603, name="宝可梦集结", mission_id=456)

    import scripts.upload_bilibili as upload_script

    monkeypatch.setattr(upload_script, "resolve_bilibili_topic_by_name", fake_resolve)
    client = TestClient(main.app)

    response = client.get("/api/bilibili/topics/resolve", params={"name": "宝可梦集结"})

    assert response.status_code == 200
    assert response.json() == {
        "topic_id": 1245603,
        "topic_name": "宝可梦集结",
        "mission_id": 456,
    }


def test_create_bilibili_upload_job_uses_configured_author_defaults(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(main.bilibili_uploads, "enqueue", lambda task_id: None)
    database.save_bilibili_settings(
        author_config_json='{"WolfeyVGC":{"tags":"作者标签,宝可梦","tid":171}}'
    )
    task_id = _make_succeeded_task(tmp_path)
    client = TestClient(main.app)

    response = client.post(
        f"/api/tasks/{task_id}/bilibili-upload",
        json={"title": "测试标题", "publish_mode": "now"},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["tags"] == "作者标签,宝可梦"
    assert body["tid"] == 171
    assert body["description"] == (
        "请大家多多关注、点赞、投币、收藏，有什么想看的也可以评论区说\n"
        "2026年7月31日\n"
        "https://www.youtube.com/watch?v=biliupload01"
    )


def test_create_bilibili_upload_job_schedules_upload_action(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    enqueued: list[str] = []
    monkeypatch.setattr(main.bilibili_uploads, "enqueue", enqueued.append)
    task_id = _make_succeeded_task(tmp_path)
    upload_after = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(timespec="minutes")
    client = TestClient(main.app)

    response = client.post(
        f"/api/tasks/{task_id}/bilibili-upload",
        json={"title": "测试标题", "publish_mode": "now", "upload_after": upload_after},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "scheduled"
    assert body["upload_after"].startswith(upload_after[:16])
    assert body["dtime"] is None
    assert enqueued == []


def test_create_bilibili_upload_job_keeps_upload_and_publish_schedules_separate(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    enqueued: list[str] = []
    monkeypatch.setattr(main.bilibili_uploads, "enqueue", enqueued.append)
    task_id = _make_succeeded_task(tmp_path)
    upload_after = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(timespec="minutes")
    publish_at = (datetime.now() + timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M")
    client = TestClient(main.app)

    response = client.post(
        f"/api/tasks/{task_id}/bilibili-upload",
        json={
            "title": "测试标题",
            "publish_mode": "scheduled",
            "dtime": publish_at,
            "upload_after": upload_after,
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "scheduled"
    assert body["dtime"] == publish_at
    assert body["upload_after"].startswith(upload_after[:16])
    assert enqueued == []


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


def test_create_bilibili_upload_rejects_past_upload_after(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    task_id = _make_succeeded_task(tmp_path)
    upload_after = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="minutes")
    client = TestClient(main.app)

    response = client.post(
        f"/api/tasks/{task_id}/bilibili-upload",
        json={"title": "测试标题", "publish_mode": "now", "upload_after": upload_after},
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


def test_bilibili_upload_scheduler_queues_due_jobs(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    enqueued: list[str] = []
    monkeypatch.setattr(bilibili_uploads, "enqueue", enqueued.append)
    task_id = _make_succeeded_task(tmp_path)
    job_id = database.create_bilibili_upload_job(
        task_id,
        title="标题",
        publish_mode="now",
        upload_after=database.now_iso(),
    )

    bilibili_uploads._enqueue_due_scheduled_jobs()

    assert enqueued == [job_id]
    assert database.get_bilibili_upload_job(job_id)["status"] == "queued"


def test_bilibili_upload_worker_marks_success(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    task_id = _make_succeeded_task(tmp_path)
    job_id = database.create_bilibili_upload_job(
        task_id,
        title="标题",
        description="自定义简介",
        tags="自定义,标签",
        tid=171,
        publish_mode="now",
    )
    database.save_bilibili_settings(
        author_config_json='{"WolfeyVGC":{"tags":"狼叔,宝可梦,VGC","tid":201}}'
    )

    def fake_upload(options):
        assert options.title == "标题"
        assert options.desc == "自定义简介"
        assert options.tag == "自定义,标签"
        assert options.tid == 171
        assert options.topic_name == ""
        assert options.author_config_json == '{"WolfeyVGC":{"tags":"狼叔,宝可梦,VGC","tid":201}}'
        return FakeUploadResult()

    import scripts.upload_bilibili as upload_script

    monkeypatch.setattr(upload_script, "upload_bilibili", fake_upload)

    bilibili_uploads._run_job(job_id)

    job = database.get_bilibili_upload_job(job_id)
    assert job["status"] == "succeeded"
    assert job["return_code"] == 0
    assert Path(job["log_path"]).read_text(encoding="utf-8").startswith("uploaded\n")


def test_bilibili_upload_worker_passes_topic_name_and_stores_resolved_id(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    task_id = _make_succeeded_task(tmp_path)
    job_id = database.create_bilibili_upload_job(
        task_id,
        title="标题",
        topic_name="宝可梦集结",
        publish_mode="now",
    )

    def fake_upload(options):
        assert options.topic_name == "宝可梦集结"
        return FakeUploadResult(topic_name="宝可梦集结", topic_id=1245603, topic_mission_id=None)

    import scripts.upload_bilibili as upload_script

    monkeypatch.setattr(upload_script, "upload_bilibili", fake_upload)

    bilibili_uploads._run_job(job_id)

    job = database.get_bilibili_upload_job(job_id)
    task = database.get_task(task_id)
    assert job["status"] == "succeeded"
    assert job["topic_name"] == "宝可梦集结"
    assert job["topic_id"] == 1245603
    assert task["bilibili_topic_id"] == 1245603
    log = Path(job["log_path"]).read_text(encoding="utf-8")
    assert "[话题] 正在按完整名称查询话题：宝可梦集结" in log
    assert "[话题] 已匹配 topic_id=1245603" in log


def test_bilibili_upload_worker_sends_completion_notification(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    task_id = _make_succeeded_task(tmp_path)
    job_id = database.create_bilibili_upload_job(task_id, title="标题", publish_mode="now")
    sent: list[str] = []

    def fake_upload(options):
        return FakeUploadResult()

    import scripts.upload_bilibili as upload_script

    monkeypatch.setattr(upload_script, "upload_bilibili", fake_upload)
    monkeypatch.setattr(
        "backend.app.notifications.send_bilibili_upload_notification",
        lambda job: sent.append(job["status"]) or "sent",
    )

    bilibili_uploads._run_job(job_id)

    assert sent == ["succeeded"]
    assert "Mail notification sent" in Path(database.get_bilibili_upload_job(job_id)["log_path"]).read_text(
        encoding="utf-8"
    )


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


def test_bilibili_upload_worker_adds_uploaded_video_to_selected_season(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(bilibili_uploads, "BILIBILI_SEASON_SWITCH_INITIAL_DELAY_SECONDS", 0)
    task_id = _make_succeeded_task(tmp_path)
    job_id = database.create_bilibili_upload_job(
        task_id,
        title="标题",
        publish_mode="now",
        season_id=7320255,
        section_id=8081933,
    )
    switched: list[dict] = []

    def fake_upload(options):
        return FakeUploadResult(aid=123456, bvid="BV1abcDEF234")

    import scripts.upload_bilibili as upload_script

    monkeypatch.setattr(upload_script, "upload_bilibili", fake_upload)
    monkeypatch.setattr(upload_script, "resolve_archive_cid", lambda **kwargs: 41125020234)
    monkeypatch.setattr(upload_script, "add_bilibili_season_episode", lambda **kwargs: switched.append(kwargs) or {})

    bilibili_uploads._run_job(job_id)

    job = database.get_bilibili_upload_job(job_id)
    assert job["status"] == "succeeded"
    assert job["aid"] == 123456
    assert job["bvid"] == "BV1abcDEF234"
    assert switched == [
        {
            "aid": 123456,
            "cid": 41125020234,
            "section_id": 8081933,
            "title": "标题WolfeyVGC",
        }
    ]
    assert "[合集] 已加入合集" in Path(job["log_path"]).read_text(encoding="utf-8")


def test_bilibili_upload_worker_waits_before_first_season_switch(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(bilibili_uploads, "BILIBILI_SEASON_SWITCH_INITIAL_DELAY_SECONDS", 30)
    monotonic_values = iter([100.0, 100.0])
    sleeps: list[float] = []
    task_id = _make_succeeded_task(tmp_path)
    job_id = database.create_bilibili_upload_job(
        task_id,
        title="标题",
        publish_mode="now",
        season_id=7320255,
        section_id=8081933,
    )

    def fake_upload(options):
        return FakeUploadResult(aid=123456, bvid="BV1abcDEF234")

    import scripts.upload_bilibili as upload_script

    monkeypatch.setattr(upload_script, "upload_bilibili", fake_upload)
    monkeypatch.setattr(upload_script, "resolve_archive_cid", lambda **kwargs: 41125020234)
    monkeypatch.setattr(upload_script, "add_bilibili_season_episode", lambda **kwargs: {})
    monkeypatch.setattr(bilibili_uploads.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(bilibili_uploads.time, "sleep", sleeps.append)

    bilibili_uploads._run_job(job_id)

    job = database.get_bilibili_upload_job(job_id)
    assert job["status"] == "succeeded"
    assert sleeps == [30.0]
    assert "[合集] 投稿成功后等待 30 秒再首次加入合集" in Path(job["log_path"]).read_text(
        encoding="utf-8"
    )


def test_bilibili_upload_worker_keeps_archive_ids_when_season_switch_fails(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(bilibili_uploads, "BILIBILI_SEASON_SWITCH_INITIAL_DELAY_SECONDS", 0)
    task_id = _make_succeeded_task(tmp_path)
    job_id = database.create_bilibili_upload_job(
        task_id,
        title="标题",
        publish_mode="now",
        season_id=7320255,
        section_id=8081933,
    )

    def fake_upload(options):
        return FakeUploadResult(bvid="BV1aZbr61E2c")

    def fake_add_bilibili_season_episode(**kwargs):
        raise UploadBilibiliError("switch failed")

    import scripts.upload_bilibili as upload_script

    monkeypatch.setattr(upload_script, "upload_bilibili", fake_upload)
    monkeypatch.setattr(upload_script, "resolve_archive_aid", lambda **kwargs: 117114245487540)
    monkeypatch.setattr(upload_script, "resolve_archive_cid", lambda **kwargs: 41125020234)
    monkeypatch.setattr(upload_script, "add_bilibili_season_episode", fake_add_bilibili_season_episode)

    bilibili_uploads._run_job(job_id)

    job = database.get_bilibili_upload_job(job_id)
    assert job["status"] == "failed"
    assert job["aid"] == 117114245487540
    assert job["bvid"] == "BV1aZbr61E2c"
    assert job["error_message"] == "switch failed"


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
