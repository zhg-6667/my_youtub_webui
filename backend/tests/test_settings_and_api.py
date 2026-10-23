from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.app import config, database, youtube_channel_monitor
from backend.app import main
from backend.app import worker


def configure_tmp_runtime(monkeypatch, tmp_path):
    workfolder = tmp_path / "workfolder"
    workfolder.mkdir()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.sqlite")
    monkeypatch.setattr(main, "YOUTUBE_COOKIE_PATH", tmp_path / "cookies" / "youtube.txt")
    monkeypatch.setattr(main, "WORKFOLDER", workfolder)
    monkeypatch.setattr(config, "WORKFOLDER", workfolder)
    monkeypatch.setattr(config, "LOG_DIR", log_dir)
    monkeypatch.setattr(worker, "start", lambda runner: None)
    monkeypatch.setattr(worker, "enqueue", lambda task_id: None)
    monkeypatch.setattr(main.worker, "enqueue", lambda task_id: None)
    database.init_db()


def test_openai_key_is_masked(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    database.save_openai_settings("https://example.com/v1", "sk-test-secret", "test-model")
    client = TestClient(main.app)

    response = client.get("/api/settings/openai")

    assert response.status_code == 200
    body = response.json()
    assert body["api_key"] == "********"
    assert body["has_api_key"] is True
    assert "sk-test-secret" not in str(body)


def test_masked_openai_key_is_not_saved_back(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    database.save_openai_settings("https://example.com/v1", "sk-test-secret", "test-model")
    client = TestClient(main.app)

    response = client.post(
        "/api/settings/openai",
        json={
            "base_url": "https://example.com/v1",
            "api_key": "********",
            "clear_api_key": False,
            "model": "next-model",
        },
    )

    assert response.status_code == 200
    settings = database.get_openai_settings()
    assert settings["api_key"] == "sk-test-secret"
    assert settings["model"] == "next-model"


def test_openai_key_can_be_cleared(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    database.save_openai_settings("https://example.com/v1", "sk-test-secret", "test-model")
    client = TestClient(main.app)

    response = client.post(
        "/api/settings/openai",
        json={
            "base_url": "https://example.com/v1",
            "api_key": "",
            "clear_api_key": True,
            "model": "next-model",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["api_key"] == ""
    assert body["has_api_key"] is False
    settings = database.get_openai_settings()
    assert settings["api_key"] == ""
    assert settings["model"] == "next-model"


@pytest.mark.parametrize(
    ("api_key", "expected"),
    [
        ("", "sk-test-secret"),
        ("********", "sk-test-secret"),
        ("sk-new", "sk-new"),
    ],
)
def test_openai_key_save_modes_without_clear(monkeypatch, tmp_path, api_key, expected):
    configure_tmp_runtime(monkeypatch, tmp_path)
    database.save_openai_settings("https://example.com/v1", "sk-test-secret", "test-model")
    client = TestClient(main.app)

    response = client.post(
        "/api/settings/openai",
        json={
            "base_url": "https://example.com/v1",
            "api_key": api_key,
            "clear_api_key": False,
            "model": "next-model",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["api_key"] == "********"
    assert body["has_api_key"] is True
    settings = database.get_openai_settings()
    assert settings["api_key"] == expected
    assert settings["model"] == "next-model"


def test_cookie_response_does_not_leak_content(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    client = TestClient(main.app)

    response = client.post("/api/cookies/youtube", json={"content": "secret-cookie-content"})

    assert response.status_code == 200
    assert response.json()["content"] == ""
    assert "secret-cookie-content" not in response.text


def test_task_id_is_video_id_and_dedupes_existing(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    enqueued: list[str] = []
    monkeypatch.setattr(main.worker, "enqueue", lambda task_id: enqueued.append(task_id))
    client = TestClient(main.app)
    payload = {"url": "https://www.youtube.com/watch?v=abcdefghijk"}

    first = client.post("/api/tasks", json=payload)
    second = client.post("/api/tasks", json=payload)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == "abcdefghijk"
    assert second.json()["id"] == "abcdefghijk"
    assert enqueued == ["abcdefghijk"]


def test_video_link_records_can_be_saved_listed_and_deleted(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    client = TestClient(main.app)

    saved = client.post("/api/video-links", json={"url": "https://www.youtube.com/watch?v=savedlink01"})
    duplicated = client.post("/api/video-links", json={"url": "https://youtu.be/savedlink01"})
    listed = client.get("/api/video-links")

    assert saved.status_code == 201
    assert duplicated.status_code == 201
    assert saved.json()["id"] == "savedlink01"
    assert duplicated.json()["id"] == "savedlink01"
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["video_links"]] == ["savedlink01"]
    assert listed.json()["video_links"][0]["url"] == "https://youtu.be/savedlink01"

    deleted = client.delete("/api/video-links/savedlink01")
    assert deleted.status_code == 204
    assert client.get("/api/video-links").json()["video_links"] == []


def test_video_link_records_include_duration(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    database.upsert_video_link_record(
        "https://www.youtube.com/watch?v=duration001",
        record_id="duration001",
        duration_seconds=625,
    )
    client = TestClient(main.app)

    listed = client.get("/api/video-links")

    assert listed.status_code == 200
    assert listed.json()["video_links"][0]["duration_seconds"] == 625


def test_youtube_channel_monitor_adds_new_videos_with_duration(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    feed_calls = 0

    def fake_resolve(channel_url: str) -> youtube_channel_monitor.ChannelInfo:
        return youtube_channel_monitor.ChannelInfo(
            channel_id="UCtestchannel",
            title="WolfeyVGC",
            channel_url="https://www.youtube.com/channel/UCtestchannel",
        )

    def fake_feed(channel_id: str, limit: int) -> list[youtube_channel_monitor.FeedEntry]:
        nonlocal feed_calls
        feed_calls += 1
        if feed_calls == 1:
            return [
                youtube_channel_monitor.FeedEntry(
                    video_id="oldvideo001",
                    url="https://www.youtube.com/watch?v=oldvideo001",
                    title="Old 1",
                    published_at="2026-07-16T00:00:00+00:00",
                ),
                youtube_channel_monitor.FeedEntry(
                    video_id="oldvideo002",
                    url="https://www.youtube.com/watch?v=oldvideo002",
                    title="Old 2",
                    published_at="2026-07-15T00:00:00+00:00",
                ),
            ][:limit]
        return [
            youtube_channel_monitor.FeedEntry(
                video_id="newvideo001",
                url="https://www.youtube.com/watch?v=newvideo001",
                title="New",
                published_at="2026-07-17T00:00:00+00:00",
            ),
            youtube_channel_monitor.FeedEntry(
                video_id="oldvideo001",
                url="https://www.youtube.com/watch?v=oldvideo001",
                title="Old 1",
                published_at="2026-07-16T00:00:00+00:00",
            ),
        ][:limit]

    monkeypatch.setattr(main.youtube_channel_monitor, "resolve_channel_info", fake_resolve)
    monkeypatch.setattr(main.youtube_channel_monitor, "fetch_channel_feed", fake_feed)
    monkeypatch.setattr(main.youtube_channel_monitor, "fetch_video_duration_seconds", lambda url: 742)
    client = TestClient(main.app)

    created = client.post(
        "/api/youtube-channel-monitors",
        json={"channel_url": "https://www.youtube.com/@WolfeyVGC/videos"},
    )
    assert created.status_code == 201
    assert client.get("/api/video-links").json()["video_links"] == []

    checked = client.post(f"/api/youtube-channel-monitors/{created.json()['id']}/check")
    links = client.get("/api/video-links").json()["video_links"]

    assert checked.status_code == 200
    assert checked.json()["added"] == 1
    assert links[0]["id"] == "newvideo001"
    assert links[0]["duration_seconds"] == 742


def test_translate_bilibili_title_uses_ai_settings(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    database.save_openai_settings("https://example.com/v1", "sk-test", "test-model")
    task_id = database.create_task("https://www.youtube.com/watch?v=titletran01", task_id="titletran01")
    database.update_task(task_id, title="How I Won the World Championship")
    calls: list[dict] = []

    def fake_translate(title: str, **settings):
        calls.append({"title": title, **settings})
        return "我是如何赢得世界冠军的"

    monkeypatch.setattr(main, "translate_title_to_chinese", fake_translate)
    client = TestClient(main.app)

    response = client.post(f"/api/tasks/{task_id}/bilibili-title/translate", json={})

    assert response.status_code == 200
    assert response.json() == {"title": "我是如何赢得世界冠军的"}
    assert calls == [
        {
            "title": "How I Won the World Championship",
            "base_url": "https://example.com/v1",
            "api_key": "sk-test",
            "model": "test-model",
        }
    ]


def test_translate_bilibili_title_accepts_input_override(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    task_id = database.create_task("https://www.youtube.com/watch?v=titletran02", task_id="titletran02")
    monkeypatch.setattr(main, "translate_title_to_chinese", lambda title, **settings: f"中文：{title}")
    client = TestClient(main.app)

    response = client.post(
        f"/api/tasks/{task_id}/bilibili-title/translate",
        json={"title": "Custom English Title"},
    )

    assert response.status_code == 200
    assert response.json() == {"title": "中文：Custom English Title"}


def test_video_link_record_tracks_created_task(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(main.worker, "enqueue", lambda task_id: None)
    client = TestClient(main.app)

    saved = client.post("/api/video-links", json={"url": "https://www.youtube.com/watch?v=tracktask01"})
    created = client.post("/api/tasks", json={"url": saved.json()["url"]})
    listed = client.get("/api/video-links")

    assert created.status_code == 201
    assert listed.json()["video_links"][0]["last_task_id"] == created.json()["id"]


def test_video_link_record_rejects_unsupported_url(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    client = TestClient(main.app)

    response = client.post("/api/video-links", json={"url": "https://example.com/video"})

    assert response.status_code == 422


def test_create_task_can_enable_auto_bilibili_upload(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    enqueued: list[str] = []
    monkeypatch.setattr(main.worker, "enqueue", lambda task_id: enqueued.append(task_id))
    client = TestClient(main.app)

    response = client.post(
        "/api/tasks",
        json={
            "url": "https://www.youtube.com/watch?v=autobilipub",
            "auto_bilibili_upload": True,
        },
    )

    assert response.status_code == 201
    assert response.json()["auto_bilibili_upload"] is True
    assert database.get_task("autobilipub")["auto_bilibili_upload"] == 1
    assert enqueued == ["autobilipub"]


def test_update_task_bilibili_upload_settings(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    task_id = database.create_task(
        "https://www.youtube.com/watch?v=biliopts001",
        task_id="biliopts001",
    )
    client = TestClient(main.app)
    upload_after = (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")

    response = client.put(
        f"/api/tasks/{task_id}/bilibili-upload-settings",
        json={
            "auto_bilibili_upload": True,
            "title": "预设投稿标题",
            "description": "预设投稿简介",
            "tags": "标签一,标签二",
            "tid": 171,
            "topic_name": "宝可梦集结",
            "topic_id": 1245603,
            "season_id": 7320255,
            "section_id": 8081933,
            "publish_mode": "scheduled",
            "dtime": "2999-07-01T18:00",
            "upload_after": upload_after,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["auto_bilibili_upload"] is True
    assert body["bilibili_upload_title"] == "预设投稿标题"
    assert body["bilibili_upload_description"] == "预设投稿简介"
    assert body["bilibili_upload_tags"] == "标签一,标签二"
    assert body["bilibili_upload_tid"] == 171
    assert body["bilibili_topic_name"] == "宝可梦集结"
    assert body["bilibili_topic_id"] == 1245603
    assert body["bilibili_season_id"] == 7320255
    assert body["bilibili_section_id"] == 8081933
    assert body["bilibili_publish_mode"] == "scheduled"
    assert body["bilibili_dtime"] == "2999-07-01T18:00"
    assert body["bilibili_upload_after"] is not None
    task = database.get_task(task_id)
    assert task["auto_bilibili_upload"] == 1
    assert task["bilibili_upload_title"] == "预设投稿标题"
    assert task["bilibili_upload_description"] == "预设投稿简介"
    assert task["bilibili_upload_tags"] == "标签一,标签二"
    assert task["bilibili_upload_tid"] == 171
    assert task["bilibili_topic_name"] == "宝可梦集结"
    assert task["bilibili_topic_id"] == 1245603
    assert task["bilibili_season_id"] == 7320255
    assert task["bilibili_section_id"] == 8081933


def test_different_videos_create_separate_tasks(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    enqueued: list[str] = []
    monkeypatch.setattr(main.worker, "enqueue", lambda task_id: enqueued.append(task_id))
    client = TestClient(main.app)

    a = client.post("/api/tasks", json={"url": "https://www.youtube.com/watch?v=abcdefghijk"})
    b = client.post("/api/tasks", json={"url": "https://youtu.be/zyxwvutsrqp"})

    assert a.json()["id"] == "abcdefghijk"
    assert b.json()["id"] == "zyxwvutsrqp"
    assert enqueued == ["abcdefghijk", "zyxwvutsrqp"]


def test_list_tasks_returns_history_newest_first(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    older = database.create_task("https://www.youtube.com/watch?v=oldvideoidx")
    newer = database.create_task("https://www.youtube.com/watch?v=newvideoidx")
    client = TestClient(main.app)

    response = client.get("/api/tasks")

    assert response.status_code == 200
    body = response.json()
    ids = [task["id"] for task in body["tasks"]]
    assert ids == [newer, older]
    assert "stages" not in body["tasks"][0]
    assert set(body["tasks"][0].keys()) >= {"id", "url", "title", "status", "final_video_path"}


def test_task_detail_includes_stage_progress(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    task_id = database.create_task("https://www.youtube.com/watch?v=progressapi")
    database.update_stage(task_id, "tts", progress=42, last_message="Prepared 21/50 TTS clips")
    client = TestClient(main.app)

    response = client.get(f"/api/tasks/{task_id}")

    assert response.status_code == 200
    stages = {stage["name"]: stage for stage in response.json()["stages"]}
    assert stages["tts"]["progress"] == 42
    assert stages["tts"]["last_message"] == "Prepared 21/50 TTS clips"


def test_task_detail_exposes_display_video_from_media_candidates(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    task_id = database.create_task("https://www.youtube.com/watch?v=displayvid01", task_id="displayvid01")
    session = tmp_path / "workfolder" / "Uploader" / "Title__displayvid01"
    media = session / "media"
    media.mkdir(parents=True)
    source = media / "video_source.mp4"
    source.write_bytes(b"source")
    database.update_task(task_id, status="paused", session_path=str(session), final_video_path=None)
    client = TestClient(main.app)

    response = client.get(f"/api/tasks/{task_id}")

    assert response.status_code == 200
    assert response.json()["display_video_path"] == str(source)


def test_task_detail_includes_bilibili_publish_info(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    task_id = database.create_task("https://www.youtube.com/watch?v=publishinfo", task_id="publishinfo")
    session = tmp_path / "workfolder" / "Uploader" / "Title__publishinfo"
    metadata = session / "metadata"
    metadata.mkdir(parents=True)
    (metadata / "ytdlp_info.json").write_text(
        json.dumps(
            {
                "id": "publishinfo",
                "title": "Original title",
                "description": "Original description",
                "uploader": "Creator",
                "uploader_id": "@creator",
                "uploader_url": "https://www.youtube.com/@creator",
                "upload_date": "20260728",
                "webpage_url": "https://www.youtube.com/watch?v=publishinfo",
                "duration": 125,
                "view_count": 12345,
                "like_count": 678,
                "comment_count": 90,
                "categories": ["Gaming"],
                "tags": ["demo", "test"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (session / "cover.jpg").write_bytes(b"cover-image")
    database.save_bilibili_settings(
        author_config_json='{"Creator":{"tags":"游戏,测试","tid":171}}'
    )
    database.update_task(task_id, session_path=str(session), bilibili_upload_title="中文标题")
    client = TestClient(main.app)

    response = client.get(f"/api/tasks/{task_id}")

    assert response.status_code == 200
    info = response.json()["bilibili_publish_info"]
    assert info["author"] == "Creator"
    assert info["author_id"] == "@creator"
    assert info["source_description"] == "Original description"
    assert info["source_upload_date"] == "20260728"
    assert info["duration_seconds"] == 125
    assert info["view_count"] == 12345
    assert info["publish_tags"] == ["游戏", "测试"]
    assert info["publish_tid"] == 171
    assert info["cover_available"] is True
    assert info["publish_description"] == (
        "请大家多多关注、点赞、投币、收藏，有什么想看的也可以评论区说\n"
        "2026年7月28日\n"
        "https://www.youtube.com/watch?v=publishinfo"
    )

    cover_response = client.get(f"/api/tasks/{task_id}/artifact/source-cover")
    assert cover_response.status_code == 200
    assert cover_response.content == b"cover-image"


def test_open_task_folder_endpoint_opens_session_directory(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    task_id = database.create_task("https://www.youtube.com/watch?v=openfolder1", task_id="openfolder1")
    session = tmp_path / "workfolder" / "Uploader" / "Title__openfolder1"
    session.mkdir(parents=True)
    database.update_task(task_id, session_path=str(session))
    opened: list[object] = []
    monkeypatch.setattr(main, "_open_directory", lambda path: opened.append(path))
    client = TestClient(main.app)

    response = client.post(f"/api/tasks/{task_id}/open-folder")

    assert response.status_code == 200
    assert response.json() == {"path": str(session)}
    assert opened == [session]


def test_final_video_artifact_uses_media_candidate_when_final_path_missing(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    task_id = database.create_task("https://www.youtube.com/watch?v=artifactvid", task_id="artifactvid")
    session = tmp_path / "workfolder" / "Uploader" / "Title__artifactvid"
    media = session / "media"
    media.mkdir(parents=True)
    source = media / "video_source.mp4"
    source.write_bytes(b"source-video")
    database.update_task(task_id, status="paused", session_path=str(session), final_video_path=None)
    client = TestClient(main.app)

    response = client.get(f"/api/tasks/{task_id}/artifact/final-video")

    assert response.status_code == 200
    assert response.content == b"source-video"


def test_delete_task_removes_session_log_and_record(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    task_id = database.create_task("https://www.youtube.com/watch?v=delvideoidx", task_id="delvideoidx")
    session = config.WORKFOLDER / "uploader" / "title__delvideoidx"
    (session / "media").mkdir(parents=True)
    (session / "media" / "video_source.mp4").write_bytes(b"mp4")
    database.update_task(task_id, session_path=str(session))
    log_file = database.log_path(task_id)
    log_file.write_text("hello", encoding="utf-8")

    client = TestClient(main.app)
    response = client.delete(f"/api/tasks/{task_id}")

    assert response.status_code == 204
    assert database.get_task(task_id) is None
    assert not session.exists()
    assert not log_file.exists()


def test_delete_task_returns_404_for_unknown(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    client = TestClient(main.app)

    response = client.delete("/api/tasks/does-not-exist")

    assert response.status_code == 404


def test_delete_task_rejects_running_task(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    task_id = database.create_task("https://www.youtube.com/watch?v=runningvidx", task_id="runningvidx")
    database.update_task(task_id, status="running")

    client = TestClient(main.app)
    response = client.delete(f"/api/tasks/{task_id}")

    assert response.status_code == 409
    assert database.get_task(task_id) is not None


def test_rerun_task_purges_session_and_requeues(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    enqueued: list[str] = []
    monkeypatch.setattr(main.worker, "enqueue", lambda task_id: enqueued.append(task_id))

    task_id = database.create_task("https://www.youtube.com/watch?v=rerunvideox", task_id="rerunvideox")
    session = config.WORKFOLDER / "uploader" / "title__rerunvideox"
    (session / "media").mkdir(parents=True)
    (session / "media" / "video_source.mp4").write_bytes(b"old")
    database.update_task(task_id, session_path=str(session), status="failed")
    log_file = database.log_path(task_id)
    log_file.write_text("old run", encoding="utf-8")

    client = TestClient(main.app)
    response = client.post(f"/api/tasks/{task_id}/rerun")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == task_id
    assert body["status"] == "queued"
    assert body["session_path"] is None
    assert enqueued == [task_id]
    assert not session.exists()
    assert not log_file.exists()


def test_rerun_task_returns_404_for_unknown(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    client = TestClient(main.app)

    response = client.post("/api/tasks/missing/rerun")

    assert response.status_code == 404


def test_rerun_task_rejects_running_task(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    enqueued: list[str] = []
    monkeypatch.setattr(main.worker, "enqueue", lambda task_id: enqueued.append(task_id))
    task_id = database.create_task("https://www.youtube.com/watch?v=runrerunvid", task_id="runrerunvid")
    database.update_task(task_id, status="running")

    client = TestClient(main.app)
    response = client.post(f"/api/tasks/{task_id}/rerun")

    assert response.status_code == 409
    assert enqueued == []
    assert database.get_task(task_id)["status"] == "running"


def test_delete_task_skips_session_outside_workfolder(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    task_id = database.create_task("https://www.youtube.com/watch?v=outsidevidx", task_id="outsidevidx")
    outside = tmp_path / "elsewhere" / "session"
    (outside / "media").mkdir(parents=True)
    (outside / "media" / "video_source.mp4").write_bytes(b"mp4")
    database.update_task(task_id, session_path=str(outside))

    client = TestClient(main.app)
    response = client.delete(f"/api/tasks/{task_id}")

    assert response.status_code == 204
    assert database.get_task(task_id) is None
    assert outside.exists(), "Sessions outside WORKFOLDER must not be deleted."


def test_cors_origins_include_runtime_configuration(monkeypatch):
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "http://172.27.2.90:3000, http://100.94.222.54:3000")

    origins = main.cors_origins()

    assert "http://localhost:3000" in origins
    assert "http://172.27.2.90:3000" in origins
    assert "http://100.94.222.54:3000" in origins


def test_cors_origin_regex_allows_common_development_hosts(monkeypatch):
    monkeypatch.delenv("CORS_ALLOW_ORIGIN_REGEX", raising=False)

    regex = re.compile(main.cors_origin_regex())

    assert regex.fullmatch("http://0.0.0.0:3000")
    assert regex.fullmatch("http://192.168.1.2:3000")
    assert regex.fullmatch("http://10.0.0.5:3000")
    assert regex.fullmatch("http://172.27.2.90:3000")
    assert regex.fullmatch("http://100.94.222.54:3000")
    assert not regex.fullmatch("http://example.com:3000")
    assert not regex.fullmatch("http://192.168.1.2:4000")


def test_openai_models_use_form_key_without_leaking_it(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    captured = {}

    def fake_list_models(*, base_url: str, api_key: str) -> list[str]:
        captured["base_url"] = base_url
        captured["api_key"] = api_key
        return ["gpt-test", "qwen-test"]

    monkeypatch.setattr(main, "list_openai_models", fake_list_models)
    client = TestClient(main.app)

    response = client.post(
        "/api/settings/openai/models",
        json={"base_url": "https://example.com/v1", "api_key": "sk-secret-models"},
    )

    assert response.status_code == 200
    assert response.json() == {"models": ["gpt-test", "qwen-test"]}
    assert captured == {"base_url": "https://example.com/v1", "api_key": "sk-secret-models"}
    assert "sk-secret-models" not in response.text


def test_openai_models_can_use_saved_key(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    database.save_openai_settings("https://saved.example/v1", "sk-saved", "saved-model")
    captured = {}

    def fake_list_models(*, base_url: str, api_key: str) -> list[str]:
        captured["base_url"] = base_url
        captured["api_key"] = api_key
        return ["saved-model"]

    monkeypatch.setattr(main, "list_openai_models", fake_list_models)
    client = TestClient(main.app)

    response = client.post("/api/settings/openai/models", json={"base_url": "", "api_key": ""})

    assert response.status_code == 200
    assert response.json() == {"models": ["saved-model"]}
    assert captured == {"base_url": "https://saved.example/v1", "api_key": "sk-saved"}


def test_openai_settings_include_translate_concurrency(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_TRANSLATE_CONCURRENCY", raising=False)
    configure_tmp_runtime(monkeypatch, tmp_path)
    client = TestClient(main.app)

    response = client.get("/api/settings/openai")

    assert response.status_code == 200
    assert response.json()["translate_concurrency"] == "50"


def test_openai_settings_persists_translate_concurrency(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    client = TestClient(main.app)

    response = client.post(
        "/api/settings/openai",
        json={
            "base_url": "https://example.com/v1",
            "api_key": "",
            "model": "model",
            "translate_concurrency": " 32 ",
        },
    )

    assert response.status_code == 200
    assert response.json()["translate_concurrency"] == "32"
    assert database.get_openai_settings()["translate_concurrency"] == "32"


@pytest.mark.parametrize("value", ["abc", "1.5", "0", "-1", "201"])
def test_openai_settings_rejects_invalid_translate_concurrency(monkeypatch, tmp_path, value):
    configure_tmp_runtime(monkeypatch, tmp_path)
    database.save_openai_settings("https://example.com/v1", "sk-test", "model", "64")
    client = TestClient(main.app)

    response = client.post(
        "/api/settings/openai",
        json={
            "base_url": "https://example.com/v1",
            "api_key": "",
            "clear_api_key": False,
            "model": "model",
            "translate_concurrency": value,
        },
    )

    assert response.status_code == 422
    assert database.get_openai_settings()["translate_concurrency"] == "64"


def test_openai_settings_empty_translate_concurrency_preserves_existing(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    database.save_openai_settings("https://example.com/v1", "sk-test", "model", "64")
    client = TestClient(main.app)

    response = client.post(
        "/api/settings/openai",
        json={
            "base_url": "https://example.com/v1",
            "api_key": "",
            "clear_api_key": False,
            "model": "next-model",
            "translate_concurrency": "",
        },
    )

    assert response.status_code == 200
    assert response.json()["translate_concurrency"] == "64"
    settings = database.get_openai_settings()
    assert settings["translate_concurrency"] == "64"
    assert settings["model"] == "next-model"


@pytest.mark.parametrize("value", ["1", "200"])
def test_openai_settings_accepts_translate_concurrency_boundaries(monkeypatch, tmp_path, value):
    configure_tmp_runtime(monkeypatch, tmp_path)
    client = TestClient(main.app)

    response = client.post(
        "/api/settings/openai",
        json={
            "base_url": "https://example.com/v1",
            "api_key": "",
            "clear_api_key": False,
            "model": "model",
            "translate_concurrency": value,
        },
    )

    assert response.status_code == 200
    assert response.json()["translate_concurrency"] == value
    assert database.get_openai_settings()["translate_concurrency"] == value


def test_resume_task_requeues_failed_task(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    enqueued: list[str] = []
    monkeypatch.setattr(main.worker, "enqueue", lambda task_id: enqueued.append(task_id))
    task_id = database.create_task("https://www.youtube.com/watch?v=resumevideox", task_id="resumevideox")
    database.update_task(task_id, status="failed", error_message="boom", completed_at=database.now_iso())
    database.update_stage(task_id, "asr", status="failed", progress=33, error_message="boom")
    database.update_stage(task_id, "download", status="succeeded")

    client = TestClient(main.app)
    response = client.post(f"/api/tasks/{task_id}/resume")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "queued"
    assert body["error_message"] is None
    stages = {s["name"]: s for s in body["stages"]}
    assert stages["download"]["status"] == "succeeded"
    assert stages["asr"]["status"] == "pending"
    assert stages["asr"]["progress"] is None
    assert stages["asr"]["error_message"] is None
    assert enqueued == [task_id]


def test_resume_task_rejects_non_failed(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    task_id = database.create_task("https://www.youtube.com/watch?v=okvideoxxxx", task_id="okvideoxxxx")
    database.update_task(task_id, status="succeeded")

    client = TestClient(main.app)
    response = client.post(f"/api/tasks/{task_id}/resume")

    assert response.status_code == 409


def test_continue_task_requeues_paused_manual_task(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    task_id = database.create_task(
        "https://www.youtube.com/watch?v=continuestep",
        task_id="continuestep",
        execution_mode="manual",
    )
    database.update_task(task_id, status="paused")
    database.update_stage(task_id, "download", status="succeeded", completed_at=database.now_iso())
    enqueued: list[str] = []
    monkeypatch.setattr(main.worker, "enqueue", lambda tid: enqueued.append(tid))

    client = TestClient(main.app)
    response = client.post(f"/api/tasks/{task_id}/continue")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "queued"
    assert body["execution_mode"] == "manual"
    assert enqueued == [task_id]


def test_continue_task_can_switch_to_auto(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    task_id = database.create_task(
        "https://www.youtube.com/watch?v=continuestepauto",
        task_id="continuestepauto",
        execution_mode="manual",
    )
    database.update_task(task_id, status="paused")
    database.update_stage(task_id, "download", status="succeeded", completed_at=database.now_iso())
    enqueued: list[str] = []
    monkeypatch.setattr(main.worker, "enqueue", lambda tid: enqueued.append(tid))

    client = TestClient(main.app)
    response = client.post(
        f"/api/tasks/{task_id}/continue",
        json={"execution_mode": "auto"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "queued"
    assert body["execution_mode"] == "auto"
    assert enqueued == [task_id]


def test_continue_task_rejects_auto_task(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    task_id = database.create_task("https://www.youtube.com/watch?v=autocontinue", task_id="autocontinue")
    database.update_task(task_id, status="paused")

    client = TestClient(main.app)
    response = client.post(f"/api/tasks/{task_id}/continue")

    assert response.status_code == 409


def test_cut_intervals_can_be_saved_before_session_exists(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    task_id = database.create_task("https://www.youtube.com/watch?v=cutranges001", task_id="cutranges001")
    client = TestClient(main.app)

    response = client.put(
        f"/api/tasks/{task_id}/cut-intervals",
        json={
            "cut_intervals": [
                {"start": 370, "end": 380},
                {"start": 430, "end": 450},
            ]
        },
    )

    assert response.status_code == 200
    assert response.json()["cut_intervals"] == [
        {"start": 370.0, "end": 380.0},
        {"start": 430.0, "end": 450.0},
    ]
    task = database.get_task(task_id)
    assert json.loads(task["cut_intervals_json"]) == response.json()["cut_intervals"]

    loaded = client.get(f"/api/tasks/{task_id}/cut-intervals")
    assert loaded.status_code == 200
    assert loaded.json() == response.json()


def test_cut_intervals_sync_to_session_file(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    session = tmp_path / "workfolder" / "cut-session"
    task_id = database.create_task("https://www.youtube.com/watch?v=cutranges002", task_id="cutranges002")
    database.update_task(task_id, session_path=str(session))
    client = TestClient(main.app)

    response = client.put(
        f"/api/tasks/{task_id}/cut-intervals",
        json={"cut_intervals": [{"start": 430, "end": 450}, {"start": 370, "end": 380}]},
    )

    assert response.status_code == 200
    intervals_file = session / "metadata" / "cut_intervals.json"
    assert json.loads(intervals_file.read_text(encoding="utf-8")) == [
        {"start": 370.0, "end": 380.0},
        {"start": 430.0, "end": 450.0},
    ]


def test_trim_video_accepts_minute_second_ranges_as_seconds(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    session = tmp_path / "workfolder" / "trim-session"
    media = session / "media"
    media.mkdir(parents=True)
    final = media / "video_final.mp4"
    final.write_bytes(b"video")
    task_id = database.create_task("https://www.youtube.com/watch?v=cutranges003", task_id="cutranges003")
    database.update_task(task_id, status="paused", session_path=str(session), final_video_path=str(final))
    trimmed = media / "video_final_trimmed.mp4"
    captured = {}

    def fake_trim_video(input_video, cut_intervals, trim_session):
        captured["input_video"] = input_video
        captured["cut_intervals"] = cut_intervals
        captured["session"] = trim_session
        trimmed.write_bytes(b"trimmed")
        return trimmed

    monkeypatch.setattr("backend.app.adapters.ffmpeg.trim_video", fake_trim_video)
    enqueued: list[str] = []
    monkeypatch.setattr(main.worker, "enqueue", lambda tid: enqueued.append(tid))
    client = TestClient(main.app)

    response = client.post(
        f"/api/tasks/{task_id}/trim",
        json={
            "cut_intervals": [
                {"start": 6 * 60 + 10, "end": 6 * 60 + 20},
                {"start": 7 * 60 + 10, "end": 7 * 60 + 30},
            ]
        },
    )

    assert response.status_code == 200
    assert enqueued == []
    intervals_file = session / "metadata" / "cut_intervals.json"
    expected_intervals = [
        {"start": 370.0, "end": 380.0},
        {"start": 430.0, "end": 450.0},
    ]
    assert json.loads(intervals_file.read_text(encoding="utf-8")) == expected_intervals
    assert captured == {"input_video": final, "cut_intervals": expected_intervals, "session": session}
    body = response.json()
    stages = {stage["name"]: stage for stage in body["stages"]}
    assert body["status"] == "paused"
    assert body["current_stage"] == "trim_video"
    assert body["final_video_path"] == str(trimmed)
    assert stages["trim_video"]["status"] == "succeeded"


def test_trim_video_endpoint_uses_requested_source_path(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    session = tmp_path / "workfolder" / "trim-source-session"
    media = session / "media"
    media.mkdir(parents=True)
    final = media / "video_final.mp4"
    final.write_bytes(b"final")
    source = media / "video_source.mp4"
    source.write_bytes(b"source")
    custom = tmp_path / "custom-source.mp4"
    custom.write_bytes(b"custom")
    trimmed = media / "video_final_trimmed.mp4"
    task_id = database.create_task("https://www.youtube.com/watch?v=cutranges006", task_id="cutranges006")
    database.update_task(task_id, status="paused", session_path=str(session), final_video_path=str(final))
    captured = {}

    def fake_trim_video(input_video, cut_intervals, trim_session):
        captured["input_video"] = input_video
        captured["cut_intervals"] = cut_intervals
        captured["session"] = trim_session
        trimmed.write_bytes(b"trimmed")
        return trimmed

    monkeypatch.setattr("backend.app.adapters.ffmpeg.trim_video", fake_trim_video)
    enqueued: list[str] = []
    monkeypatch.setattr(main.worker, "enqueue", lambda tid: enqueued.append(tid))
    client = TestClient(main.app)

    response = client.post(
        f"/api/tasks/{task_id}/trim",
        json={"source_video_path": str(custom), "cut_intervals": [{"start": 10, "end": 15}]},
    )

    assert response.status_code == 200
    assert enqueued == []
    assert captured == {
        "input_video": custom,
        "cut_intervals": [{"start": 10.0, "end": 15.0}],
        "session": session,
    }
    assert response.json()["final_video_path"] == str(trimmed)


def test_trim_video_endpoint_accepts_uploaded_source_file(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    session = tmp_path / "workfolder" / "trim-upload-session"
    media = session / "media"
    media.mkdir(parents=True)
    final = media / "video_final.mp4"
    final.write_bytes(b"final")
    trimmed = media / "video_final_trimmed.mp4"
    task_id = database.create_task("https://www.youtube.com/watch?v=cutranges007", task_id="cutranges007")
    database.update_task(task_id, status="paused", session_path=str(session), final_video_path=str(final))
    captured = {}

    def fake_trim_video(input_video, cut_intervals, trim_session):
        captured["input_video"] = input_video
        captured["cut_intervals"] = cut_intervals
        captured["session"] = trim_session
        trimmed.write_bytes(b"trimmed")
        return trimmed

    monkeypatch.setattr("backend.app.adapters.ffmpeg.trim_video", fake_trim_video)
    enqueued: list[str] = []
    monkeypatch.setattr(main.worker, "enqueue", lambda tid: enqueued.append(tid))
    client = TestClient(main.app)

    response = client.post(
        f"/api/tasks/{task_id}/trim",
        data={"cut_intervals": json.dumps([{"start": 12, "end": 18}])},
        files={"source_video_file": ("picked.mp4", b"picked video", "video/mp4")},
    )

    assert response.status_code == 200
    assert enqueued == []
    assert captured["input_video"].name.endswith(".mp4")
    assert captured["input_video"].parent == session / "tmp" / "trim_sources"
    assert captured["input_video"].read_bytes() == b"picked video"
    assert captured["cut_intervals"] == [{"start": 12.0, "end": 18.0}]
    assert captured["session"] == session
    assert response.json()["final_video_path"] == str(trimmed)


def test_trim_input_video_path_uses_configured_priority(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    cta = media / "video_final_with_cta.mp4"
    trimmed = media / "video_final_trimmed.mp4"
    final = media / "video_final.mp4"
    source = media / "video_source.mp4"
    for path in (trimmed, cta, final, source):
        path.write_bytes(path.name.encode("utf-8"))

    assert main.trim_input_video_path(str(tmp_path)) == cta
    cta.unlink()
    assert main.trim_input_video_path(str(tmp_path)) == final
    final.unlink()
    assert main.trim_input_video_path(str(tmp_path)) == source
    source.unlink()
    assert main.trim_input_video_path(str(tmp_path)) is None


def test_fast_trim_video_endpoint_uses_default_video_priority(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    session = tmp_path / "workfolder" / "fast-trim-session"
    media = session / "media"
    media.mkdir(parents=True)
    cta = media / "video_final_with_cta.mp4"
    cta.write_bytes(b"cta")
    existing_trimmed = media / "video_final_trimmed.mp4"
    existing_trimmed.write_bytes(b"existing trimmed")
    final = media / "video_final.mp4"
    final.write_bytes(b"final")
    source = media / "video_source.mp4"
    source.write_bytes(b"source")
    trimmed = media / "video_final_trimmed.mp4"
    task_id = database.create_task("https://www.youtube.com/watch?v=fasttrim01", task_id="fasttrim01")
    database.update_task(task_id, status="paused", session_path=str(session), final_video_path=str(final))
    captured = {}

    def fake_fast_trim_video(input_video, cut_intervals, trim_session):
        captured["input_video"] = input_video
        captured["cut_intervals"] = cut_intervals
        captured["session"] = trim_session
        trimmed.write_bytes(b"trimmed")
        return trimmed

    monkeypatch.setattr("backend.app.adapters.ffmpeg.fast_trim_video", fake_fast_trim_video)
    enqueued: list[str] = []
    monkeypatch.setattr(main.worker, "enqueue", lambda tid: enqueued.append(tid))
    client = TestClient(main.app)

    response = client.post(
        f"/api/tasks/{task_id}/trim/fast",
        json={"cut_intervals": [{"start": 10, "end": 15}]},
    )

    assert response.status_code == 200
    assert enqueued == []
    assert captured == {
        "input_video": cta,
        "cut_intervals": [{"start": 10.0, "end": 15.0}],
        "session": session,
    }
    body = response.json()
    stages = {stage["name"]: stage for stage in body["stages"]}
    assert body["current_stage"] == "trim_video"
    assert body["final_video_path"] == str(trimmed)
    assert stages["trim_video"]["status"] == "succeeded"


def test_fast_trim_replace_source_endpoint_renames_trimmed_video(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    session = tmp_path / "workfolder" / "fast-trim-replace-session"
    media = session / "media"
    media.mkdir(parents=True)
    final = media / "video_final.mp4"
    final.write_bytes(b"final")
    source = media / "video_source.mp4"
    source.write_bytes(b"source")
    trimmed = media / "video_final_trimmed.mp4"
    task_id = database.create_task("https://www.youtube.com/watch?v=fastreplace1", task_id="fastreplace1")
    database.update_task(task_id, status="paused", session_path=str(session), final_video_path=str(final))
    captured = {}

    def fake_fast_trim_video(input_video, cut_intervals, trim_session):
        captured["input_video"] = input_video
        captured["cut_intervals"] = cut_intervals
        captured["session"] = trim_session
        trimmed.write_bytes(b"trimmed")
        return trimmed

    monkeypatch.setattr("backend.app.adapters.ffmpeg.fast_trim_video", fake_fast_trim_video)
    enqueued: list[str] = []
    monkeypatch.setattr(main.worker, "enqueue", lambda tid: enqueued.append(tid))
    client = TestClient(main.app)

    response = client.post(
        f"/api/tasks/{task_id}/trim/fast-replace-source",
        json={"cut_intervals": [{"start": 10, "end": 15}]},
    )

    assert response.status_code == 200
    assert enqueued == []
    assert captured == {
        "input_video": final,
        "cut_intervals": [{"start": 10.0, "end": 15.0}],
        "session": session,
    }
    assert source.read_bytes() == b"trimmed"
    assert not trimmed.exists()
    body = response.json()
    stages = {stage["name"]: stage for stage in body["stages"]}
    assert body["current_stage"] == "trim_video"
    assert body["final_video_path"] == str(source)
    assert stages["trim_video"]["status"] == "succeeded"


def test_trim_video_without_ranges_rejects_missing_cut_intervals(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    session = tmp_path / "workfolder" / "trim-default-session"
    media = session / "media"
    media.mkdir(parents=True)
    final = media / "video_final.mp4"
    final.write_bytes(b"video")
    task_id = database.create_task("https://www.youtube.com/watch?v=cutranges005", task_id="cutranges005")
    database.update_task(task_id, status="paused", session_path=str(session), final_video_path=str(final))
    enqueued: list[str] = []
    monkeypatch.setattr(main.worker, "enqueue", lambda tid: enqueued.append(tid))
    client = TestClient(main.app)

    response = client.post(f"/api/tasks/{task_id}/trim")

    assert response.status_code == 400
    assert enqueued == []
    intervals_file = session / "metadata" / "cut_intervals.json"
    assert not intervals_file.exists()
    assert database.get_task(task_id)["cut_intervals_json"] is None
    assert response.json()["detail"] == "No cut intervals provided and cut_intervals.json not found."


def test_concat_video_endpoint_runs_concat_stage_directly(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    session = tmp_path / "workfolder" / "concat-session"
    media = session / "media"
    media.mkdir(parents=True)
    source = media / "video_source.mp4"
    source.write_bytes(b"source")
    final = media / "video_final.mp4"
    final.write_bytes(b"final")
    old_concat = media / "video_final_with_cta.mp4"
    old_concat.write_bytes(b"old")
    task_id = database.create_task("https://www.youtube.com/watch?v=concatapi01", task_id="concatapi01")
    database.update_task(task_id, status="paused", session_path=str(session), final_video_path=str(old_concat))
    database.update_stage(task_id, "concat_video", status="succeeded", completed_at=database.now_iso())
    database.update_stage(task_id, "trim_video", status="succeeded", completed_at=database.now_iso())
    captured: dict[str, Path] = {}

    def fake_prepend_cta_clip(input_video, concat_session):
        captured["input_video"] = input_video
        captured["session"] = concat_session
        old_concat.write_bytes(b"new")
        return old_concat

    monkeypatch.setattr("backend.app.adapters.ffmpeg.prepend_cta_clip", fake_prepend_cta_clip)
    enqueued: list[str] = []
    monkeypatch.setattr(main.worker, "enqueue", lambda tid: enqueued.append(tid))
    client = TestClient(main.app)

    response = client.post(f"/api/tasks/{task_id}/concat")

    assert response.status_code == 200
    assert enqueued == []
    assert captured == {"input_video": final, "session": session}
    assert old_concat.read_bytes() == b"new"
    body = response.json()
    stages = {stage["name"]: stage for stage in body["stages"]}
    assert body["status"] == "paused"
    assert body["current_stage"] == "concat_video"
    assert body["final_video_path"] == str(old_concat)
    assert stages["concat_video"]["status"] == "succeeded"
    assert stages["trim_video"]["status"] == "pending"


def test_concat_video_endpoint_skips_cta_for_zh_to_en(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    session = tmp_path / "workfolder" / "concat-zh-en-session"
    media = session / "media"
    media.mkdir(parents=True)
    final = media / "video_final.mp4"
    final.write_bytes(b"final")
    old_concat = media / "video_final_with_cta.mp4"
    old_concat.write_bytes(b"old")
    task_id = database.create_task("https://www.bilibili.com/video/BV1xx411c7mD", task_id="BV1xx411c7mD")
    database.update_task(task_id, status="paused", session_path=str(session), final_video_path=str(old_concat))
    database.update_stage(task_id, "concat_video", status="succeeded", completed_at=database.now_iso())
    database.update_stage(task_id, "trim_video", status="succeeded", completed_at=database.now_iso())

    def fail_prepend_cta_clip(input_video, concat_session):
        raise AssertionError("prepend_cta_clip should not run for zh->en")

    monkeypatch.setattr("backend.app.adapters.ffmpeg.prepend_cta_clip", fail_prepend_cta_clip)
    client = TestClient(main.app)

    response = client.post(f"/api/tasks/{task_id}/concat")

    assert response.status_code == 200
    assert not old_concat.exists()
    body = response.json()
    stages = {stage["name"]: stage for stage in body["stages"]}
    assert body["status"] == "paused"
    assert body["current_stage"] == "concat_video"
    assert body["final_video_path"] == str(final)
    assert stages["concat_video"]["status"] == "succeeded"
    assert stages["trim_video"]["status"] == "pending"


def test_cut_intervals_reject_invalid_ranges(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    task_id = database.create_task("https://www.youtube.com/watch?v=cutranges004", task_id="cutranges004")
    client = TestClient(main.app)

    response = client.put(
        f"/api/tasks/{task_id}/cut-intervals",
        json={"cut_intervals": [{"start": 450, "end": 430}]},
    )

    assert response.status_code == 422


def test_redo_stage_requeues_manual_task(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    session = tmp_path / "workfolder" / "redo-session"
    metadata = session / "metadata"
    metadata.mkdir(parents=True)
    translation = metadata / "translation.zh.json"
    asr_fixed = metadata / "asr_fixed.json"
    translation.write_text("{}", encoding="utf-8")
    asr_fixed.write_text("{}", encoding="utf-8")

    task_id = database.create_task(
        "https://www.youtube.com/watch?v=redostgapi1",
        task_id="redostgapi1",
        execution_mode="manual",
    )
    database.update_task(task_id, status="paused", session_path=str(session))
    for stage in ("download", "separate", "asr", "asr_fix", "translate"):
        database.update_stage(task_id, stage, status="succeeded", completed_at=database.now_iso())
    enqueued: list[str] = []
    monkeypatch.setattr(main.worker, "enqueue", lambda tid: enqueued.append(tid))

    client = TestClient(main.app)
    response = client.post(f"/api/tasks/{task_id}/stages/translate/redo")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "queued"
    assert not translation.exists()
    assert asr_fixed.exists()
    translate_stage = next(stage for stage in body["stages"] if stage["name"] == "translate")
    split_stage = next(stage for stage in body["stages"] if stage["name"] == "split_audio")
    assert translate_stage["status"] == "pending"
    assert split_stage["status"] == "pending"
    assert enqueued == [task_id]


def test_redo_stage_runtime_failure_preserves_artifacts_and_state(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    session = tmp_path / "workfolder" / "redo-runtime-session"
    metadata = session / "metadata"
    tts_dir = session / "segments" / "tts"
    metadata.mkdir(parents=True)
    tts_dir.mkdir(parents=True)
    translation = metadata / "translation.zh.json"
    tts_file = tts_dir / "0001.wav"
    translation.write_text("{}", encoding="utf-8")
    tts_file.write_bytes(b"wav")

    task_id = database.create_task(
        "https://www.youtube.com/watch?v=redoruntime",
        task_id="redoruntime",
        execution_mode="manual",
    )
    database.update_task(task_id, status="paused", session_path=str(session), current_stage="merge_video")
    for stage in ("download", "separate", "asr", "asr_fix", "translate", "split_audio", "tts"):
        database.update_stage(task_id, stage, status="succeeded", completed_at=database.now_iso())
    monkeypatch.setattr(
        main,
        "validate_runtime_device",
        lambda: (_ for _ in ()).throw(RuntimeError("runtime unavailable")),
    )
    enqueued: list[str] = []
    monkeypatch.setattr(main.worker, "enqueue", lambda tid: enqueued.append(tid))

    client = TestClient(main.app)
    response = client.post(f"/api/tasks/{task_id}/stages/translate/redo")

    assert response.status_code == 409
    assert response.json()["detail"] == "runtime unavailable"
    assert translation.exists()
    assert tts_file.exists()
    assert enqueued == []
    task = database.get_task(task_id)
    stages = {stage["name"]: stage for stage in task["stages"]}
    assert task["status"] == "paused"
    assert stages["translate"]["status"] == "succeeded"
    assert stages["tts"]["status"] == "succeeded"


def test_redo_stage_rejects_auto_task(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    task_id = database.create_task("https://www.youtube.com/watch?v=redostgauto", task_id="redostgauto")
    database.update_task(task_id, status="paused")
    database.update_stage(task_id, "download", status="succeeded", completed_at=database.now_iso())

    client = TestClient(main.app)
    response = client.post(f"/api/tasks/{task_id}/stages/download/redo")

    assert response.status_code == 409


def test_redo_stage_rejects_pending_stage(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    task_id = database.create_task(
        "https://www.youtube.com/watch?v=redostgpnd1",
        task_id="redostgpnd1",
        execution_mode="manual",
    )
    database.update_task(task_id, status="paused")
    database.update_stage(task_id, "download", status="succeeded", completed_at=database.now_iso())

    client = TestClient(main.app)
    response = client.post(f"/api/tasks/{task_id}/stages/translate/redo")

    assert response.status_code == 409


def test_ytdlp_proxy_port_settings(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    client = TestClient(main.app)

    saved = client.post("/api/settings/ytdlp", json={"proxy_port": "7890"})
    loaded = client.get("/api/settings/ytdlp")

    assert saved.status_code == 200
    assert loaded.status_code == 200
    assert loaded.json() == {"proxy_port": "7890"}


def test_ytdlp_proxy_port_rejects_invalid_value(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    client = TestClient(main.app)

    response = client.post("/api/settings/ytdlp", json={"proxy_port": "70000"})

    assert response.status_code == 422


def test_bilibili_settings_save_author_tags_json(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    client = TestClient(main.app)

    saved = client.post(
        "/api/settings/bilibili",
        json={
            "author_tags_json": '{"WolfeyVGC":"狼叔,宝可梦,VGC"}',
            "author_tids_json": '{"WolfeyVGC":201}',
        },
    )
    loaded = client.get("/api/settings/bilibili")

    assert saved.status_code == 200
    assert saved.json()["author_tags_json"] == '{"WolfeyVGC": "狼叔,宝可梦,VGC"}'
    assert saved.json()["author_tids_json"] == '{"WolfeyVGC": 201}'
    assert loaded.status_code == 200
    assert loaded.json() == saved.json()


def test_bilibili_settings_save_author_config_json(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    client = TestClient(main.app)

    saved = client.post(
        "/api/settings/bilibili",
        json={"author_config_json": '{"WolfeyVGC":{"tags":"狼叔,宝可梦,VGC","tid":17,"topic_name":"宝可梦集结","topic_id":1245603,"topic_mission_id":42,"season_id":123,"section_id":456}}'},
    )
    loaded = client.get("/api/settings/bilibili")

    assert saved.status_code == 200
    assert json.loads(saved.json()["author_config_json"]) == {
        "WolfeyVGC": {
            "tags": "狼叔,宝可梦,VGC",
            "tid": 17,
            "topic_name": "宝可梦集结",
            "topic_id": 1245603,
            "topic_mission_id": 42,
            "season_id": 123,
            "section_id": 456,
        }
    }
    assert loaded.status_code == 200
    assert loaded.json() == saved.json()


def test_bilibili_settings_save_pokemon_topic_config_json(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    client = TestClient(main.app)

    saved = client.post(
        "/api/settings/bilibili",
        json={"pokemon_topic_config_json": '{"宝可梦集结":1245603}'},
    )
    loaded = client.get("/api/settings/bilibili")

    assert saved.status_code == 200
    assert saved.json()["pokemon_topic_config_json"] == '{"宝可梦集结": 1245603}'
    assert loaded.status_code == 200
    assert loaded.json() == saved.json()


def test_bilibili_settings_merges_legacy_author_config(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    database.save_bilibili_settings('{"WolfeyVGC":"狼叔,宝可梦,VGC"}', '{"WolfeyVGC":17}')
    client = TestClient(main.app)

    loaded = client.get("/api/settings/bilibili")

    assert loaded.status_code == 200
    assert loaded.json()["author_config_json"] == '{"WolfeyVGC": {"tags": "狼叔,宝可梦,VGC", "tid": 17}}'


def test_bilibili_settings_rejects_invalid_author_tags_json(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    client = TestClient(main.app)

    response = client.post(
        "/api/settings/bilibili",
        json={"author_tags_json": '["not", "an", "object"]'},
    )

    assert response.status_code == 422


def test_bilibili_settings_rejects_invalid_author_tids_json(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    client = TestClient(main.app)

    response = client.post(
        "/api/settings/bilibili",
        json={"author_tids_json": '{"WolfeyVGC":"17"}'},
    )

    assert response.status_code == 422


def test_bilibili_settings_rejects_invalid_author_config_json(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    client = TestClient(main.app)

    response = client.post(
        "/api/settings/bilibili",
        json={"author_config_json": '{"WolfeyVGC":{"tags":["bad"],"tid":17}}'},
    )

    assert response.status_code == 422


def test_bilibili_settings_rejects_incomplete_author_collection(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    client = TestClient(main.app)

    response = client.post(
        "/api/settings/bilibili",
        json={"author_config_json": '{"WolfeyVGC":{"season_id":123}}'},
    )

    assert response.status_code == 422


def test_bilibili_settings_rejects_invalid_pokemon_topic_config_json(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    client = TestClient(main.app)

    response = client.post(
        "/api/settings/bilibili",
        json={"pokemon_topic_config_json": '{"宝可梦集结":"1245603"}'},
    )

    assert response.status_code == 422


def test_mail_settings_are_masked_and_saved(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    client = TestClient(main.app)

    saved = client.post(
        "/api/settings/mail",
        json={
            "enabled": True,
            "smtp_host": "smtp.example.com",
            "smtp_port": "465",
            "smtp_username": "user@example.com",
            "smtp_password": "secret",
            "from_address": "noreply@example.com",
            "to_addresses": "ops@example.com; dev@example.com",
            "smtp_security": "ssl",
            "notify_on_success": True,
            "notify_on_failure": False,
        },
    )
    loaded = client.get("/api/settings/mail")

    assert saved.status_code == 200
    assert loaded.status_code == 200
    body = loaded.json()
    assert body["enabled"] is True
    assert body["smtp_password"] == "********"
    assert body["has_smtp_password"] is True
    assert body["to_addresses"] == "ops@example.com,dev@example.com"
    assert body["smtp_security"] == "ssl"
    assert body["notify_on_failure"] is False
    assert "secret" not in loaded.text


def test_masked_mail_password_is_not_saved_back(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    database.save_mail_settings(
        enabled="true",
        smtp_host="smtp.example.com",
        smtp_port="587",
        smtp_username="user@example.com",
        smtp_password="secret",
        from_address="noreply@example.com",
        to_addresses="ops@example.com",
        smtp_security="tls",
        notify_on_success="true",
        notify_on_failure="true",
    )
    client = TestClient(main.app)

    response = client.post(
        "/api/settings/mail",
        json={
            "enabled": True,
            "smtp_host": "smtp2.example.com",
            "smtp_port": "587",
            "smtp_username": "user@example.com",
            "smtp_password": "********",
            "clear_smtp_password": False,
            "from_address": "noreply@example.com",
            "to_addresses": "ops@example.com",
            "smtp_security": "tls",
            "notify_on_success": True,
            "notify_on_failure": True,
        },
    )

    assert response.status_code == 200
    settings = database.get_mail_settings()
    assert settings["smtp_password"] == "secret"
    assert settings["smtp_host"] == "smtp2.example.com"


def test_mail_password_can_be_cleared(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    database.save_mail_settings(
        enabled="true",
        smtp_host="smtp.example.com",
        smtp_port="587",
        smtp_username="user@example.com",
        smtp_password="secret",
        from_address="noreply@example.com",
        to_addresses="ops@example.com",
        smtp_security="tls",
        notify_on_success="true",
        notify_on_failure="true",
    )
    client = TestClient(main.app)

    response = client.post(
        "/api/settings/mail",
        json={
            "enabled": True,
            "smtp_host": "smtp.example.com",
            "smtp_port": "587",
            "smtp_username": "user@example.com",
            "smtp_password": "",
            "clear_smtp_password": True,
            "from_address": "noreply@example.com",
            "to_addresses": "ops@example.com",
            "smtp_security": "tls",
            "notify_on_success": True,
            "notify_on_failure": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["has_smtp_password"] is False
    assert database.get_mail_settings()["smtp_password"] == ""


@pytest.mark.parametrize(
    ("payload", "detail"),
    [
        ({"smtp_port": "70000"}, "SMTP port must be between 1 and 65535."),
        ({"smtp_security": "starttls"}, "SMTP security must be one of: none, tls, ssl."),
        ({"to_addresses": "not-an-email"}, "Invalid email address: not-an-email"),
    ],
)
def test_mail_settings_reject_invalid_values(monkeypatch, tmp_path, payload, detail):
    configure_tmp_runtime(monkeypatch, tmp_path)
    client = TestClient(main.app)
    body = {
        "enabled": True,
        "smtp_host": "smtp.example.com",
        "smtp_port": "587",
        "smtp_username": "user@example.com",
        "smtp_password": "secret",
        "from_address": "noreply@example.com",
        "to_addresses": "ops@example.com",
        "smtp_security": "tls",
        "notify_on_success": True,
        "notify_on_failure": True,
    }
    body.update(payload)

    response = client.post("/api/settings/mail", json=body)

    assert response.status_code == 422
    assert response.json()["detail"] == detail


def test_upload_local_video_creates_task_and_saved_file(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    enqueued: list[str] = []
    monkeypatch.setattr(main.worker, "enqueue", lambda task_id: enqueued.append(task_id))
    client = TestClient(main.app)

    response = client.post(
        "/api/tasks/upload",
        data={"direction": "zh-en"},
        files={"file": ("clip.mp4", b"mp4data", "video/mp4")},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "clip"
    assert body["url"].startswith(f"local://upload/{body['id']}?direction=zh-en")
    assert enqueued == [body["id"]]
    saved = list((config.WORKFOLDER / "_uploads" / body["id"] / "video").iterdir())
    assert len(saved) == 1
    assert saved[0].read_bytes() == b"mp4data"


def test_upload_local_video_can_save_translated_srt(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    enqueued: list[str] = []
    monkeypatch.setattr(main.worker, "enqueue", lambda task_id: enqueued.append(task_id))
    client = TestClient(main.app)

    response = client.post(
        "/api/tasks/upload",
        data={"direction": "en-zh"},
        files={
            "file": ("clip.mp4", b"mp4data", "video/mp4"),
            "subtitle_file": (
                "clip.zh.srt",
                b"1\n00:00:00,000 --> 00:00:01,000\nhello\n",
                "application/x-subrip",
            ),
        },
    )

    assert response.status_code == 201
    task_id = response.json()["id"]
    assert enqueued == [task_id]
    video_files = list((config.WORKFOLDER / "_uploads" / task_id / "video").iterdir())
    subtitle_files = list((config.WORKFOLDER / "_uploads" / task_id / "subtitle").iterdir())
    assert [path.name for path in video_files] == ["clip.mp4"]
    assert [path.name for path in subtitle_files] == ["clip.zh.srt"]
    assert subtitle_files[0].read_bytes().startswith(b"1\n00:00:00,000")


def test_upload_local_video_rejects_non_srt_subtitle(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    client = TestClient(main.app)

    response = client.post(
        "/api/tasks/upload",
        data={"direction": "en-zh"},
        files={
            "file": ("clip.mp4", b"mp4data", "video/mp4"),
            "subtitle_file": ("clip.vtt", b"WEBVTT", "text/vtt"),
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Only .srt subtitle files are supported."


def test_upload_local_video_rejects_malformed_srt_and_cleans_upload(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    enqueued: list[str] = []
    monkeypatch.setattr(main.worker, "enqueue", lambda task_id: enqueued.append(task_id))
    client = TestClient(main.app)

    response = client.post(
        "/api/tasks/upload",
        data={"direction": "en-zh"},
        files={
            "file": ("clip.mp4", b"mp4data", "video/mp4"),
            "subtitle_file": ("broken.srt", b"not an srt file", "application/x-subrip"),
        },
    )

    assert response.status_code == 400
    assert "Invalid SRT subtitle file" in response.json()["detail"]
    assert enqueued == []
    assert database.list_tasks() == []
    assert not any((config.WORKFOLDER / "_uploads").glob("*"))


def test_create_task_rejects_local_upload_url(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    client = TestClient(main.app)
    response = client.post("/api/tasks", json={"url": "local://upload/fake?direction=en-zh"})
    assert response.status_code == 422


def test_create_task_rejects_unavailable_cuda_runtime(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(
        main,
        "validate_runtime_device",
        lambda: (_ for _ in ()).throw(RuntimeError("DEVICE=cuda is not available")),
    )
    client = TestClient(main.app)
    response = client.post("/api/tasks", json={"url": "https://www.youtube.com/watch?v=okvideoxxxx"})
    assert response.status_code == 409
    assert response.json()["detail"] == "DEVICE=cuda is not available"


def test_delete_local_video_removes_upload(monkeypatch, tmp_path):
    configure_tmp_runtime(monkeypatch, tmp_path)
    client = TestClient(main.app)
    upload = client.post(
        "/api/tasks/upload",
        data={"direction": "en-zh"},
        files={"file": ("clip.mp4", b"mp4data", "video/mp4")},
    )
    task_id = upload.json()["id"]
    upload_root = config.WORKFOLDER / "_uploads" / task_id
    assert upload_root.exists()

    response = client.delete(f"/api/tasks/{task_id}")

    assert response.status_code == 204
    assert not upload_root.exists()
    assert database.get_task(task_id) is None
