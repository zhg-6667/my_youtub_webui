from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.app import database, main, youtube_channel_lookup, youtube_video_metadata as metadata


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.sqlite")
    monkeypatch.setattr(metadata, "THUMBNAIL_DIR", tmp_path / "thumbnails")
    monkeypatch.setattr(main, "_ensure_runtime_ready", lambda: None)
    enqueued = []
    monkeypatch.setattr(main.worker, "enqueue", enqueued.append)
    metadata._feed_cache.clear()
    database.init_db()
    return TestClient(main.app), enqueued


def test_lookup_reuses_dates_and_marks_existing_tasks(runtime, monkeypatch):
    video_id = "abcdefghijk"
    database.cache_youtube_video_metadata(video_id, "2026-10-23T16:00:20+00:00", None)
    database.create_task(f"https://www.youtube.com/watch?v={video_id}", task_id=video_id)
    monkeypatch.setattr(youtube_channel_lookup, "fetch_author_videos", lambda *args: {
        "videos": [{"video_id": video_id}], "shorts": [],
    })
    result = youtube_channel_lookup.lookup_authors(["https://www.youtube.com/@author"], 5)
    video = result[0]["videos"][0]
    assert video["task_id"] == video_id
    assert video["task_status"] == "queued"
    assert video["metadata_checked"] is True
    assert video["published_at"] == "2026-10-23T16:00:20+00:00"


def test_metadata_uses_rss_and_caches_without_fetching_video_details(runtime, monkeypatch):
    client, _ = runtime
    channel_id = "UC41dsC_W53UpeZxd4wCS7bQ"
    calls = []

    def feed(channel, limit):
        calls.append(channel)
        return [SimpleNamespace(video_id="abcdefghijk", published_at="2026-10-23T16:00:20+00:00")]

    monkeypatch.setattr(metadata, "fetch_channel_feed", feed)
    monkeypatch.setattr(metadata, "_detail_date", lambda _: pytest.fail("RSS already provided the date"))
    payload = {"items": [{"video_id": "abcdefghijk", "channel_id": channel_id}]}
    first = client.post("/api/youtube-videos/metadata", json=payload)
    second = client.post("/api/youtube-videos/metadata", json=payload)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["videos"][0]["published_at"] == "2026-10-23T16:00:20+00:00"
    assert calls == [channel_id]


@pytest.mark.parametrize(("info", "published_at", "published_date"), [
    ({"timestamp": 1792771220}, datetime.fromtimestamp(1792771220, timezone.utc).isoformat(), None),
    ({"upload_date": "20261023"}, None, "2026-10-23"),
    ({}, None, None),
])
def test_detail_dates_preserve_precision_and_do_not_download(runtime, monkeypatch, info, published_at, published_date):
    import yt_dlp

    class FakeYDL:
        def __init__(self, opts):
            assert opts["skip_download"] is True
            assert opts["noplaylist"] is True

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, url, download):
            assert download is False
            return info

    monkeypatch.setattr(metadata, "_youtube_ydl_opts", lambda **kwargs: {"noplaylist": kwargs["noplaylist"]})
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    row = metadata._detail_date("abcdefghijk")
    assert row["published_at"] == published_at
    assert row["published_date"] == published_date


def test_failed_metadata_fetch_is_cached_briefly(runtime, monkeypatch):
    database.cache_youtube_video_metadata("abcdefghijk", None, None)
    monkeypatch.setattr(metadata, "_detail_date", lambda _: pytest.fail("Failure cache should avoid repeated requests"))
    result = metadata.fetch_video_metadata([{"video_id": "abcdefghijk", "channel_id": None}])
    assert result == [{"video_id": "abcdefghijk", "published_at": None, "published_date": None, "metadata_checked": True}]


def test_thumbnails_use_fixed_remote_host_and_disk_cache(runtime, monkeypatch):
    client, _ = runtime
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        assert kwargs["allow_redirects"] is False
        return SimpleNamespace(status_code=200, headers={"Content-Type": "image/jpeg"},
                               content=b"test-thumbnail", raise_for_status=lambda: None)

    monkeypatch.setattr(metadata, "_request_proxies", lambda: None)
    monkeypatch.setattr(metadata.requests, "get", get)
    for _ in range(2):
        response = client.get("/api/youtube-videos/abcdefghijk/thumbnail")
        assert response.status_code == 200
        assert response.content == b"test-thumbnail"
        assert response.headers["cache-control"] == "public, max-age=86400"
    assert calls == ["https://i.ytimg.com/vi/abcdefghijk/mqdefault.jpg"]
    assert client.get("/api/youtube-videos/invalid/thumbnail").status_code == 422


def test_batch_create_deduplicates_and_isolates_failures(runtime):
    client, enqueued = runtime
    first = "https://www.youtube.com/watch?v=abcdefghijk"
    second = "https://www.youtube.com/shorts/lmnopqrstuv"
    payload = {"urls": [first, "bad-url", second, first], "execution_mode": "manual", "auto_bilibili_upload": False}
    response = client.post("/api/tasks/batch", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert (body["created"], body["existing"], body["failed"]) == (2, 1, 1)
    assert enqueued == ["abcdefghijk", "lmnopqrstuv"]
    assert database.get_task("abcdefghijk")["execution_mode"] == "manual"
    assert database.get_task("abcdefghijk")["auto_bilibili_upload"] == 0
    assert body["results"][1]["error"]
    again = client.post("/api/tasks/batch", json=payload).json()
    assert (again["created"], again["existing"], again["failed"]) == (0, 3, 1)
    assert len(enqueued) == 2


def test_concurrent_batch_requests_enqueue_each_video_once(runtime):
    _, enqueued = runtime
    payload = main.BatchTaskCreate(urls=["https://www.youtube.com/watch?v=abcdefghijk"])
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: main.create_tasks_batch(payload), range(3)))
    assert sum(item["created"] for item in results) == 1
    assert sum(item["existing"] for item in results) == 2
    assert enqueued == ["abcdefghijk"]


@pytest.mark.parametrize("path,payload", [
    ("/api/tasks/batch", {"urls": []}),
    ("/api/tasks/batch", {"urls": ["url"] * 101}),
    ("/api/tasks/batch", {"urls": ["url"], "execution_mode": "invalid"}),
    ("/api/youtube-videos/metadata", {"items": []}),
    ("/api/youtube-videos/metadata", {"items": [{"video_id": "invalid"}]}),
    ("/api/youtube-videos/metadata", {"items": [{"video_id": "abcdefghijk", "channel_id": "invalid"}]}),
])
def test_batch_api_validation(runtime, path, payload):
    client, _ = runtime
    assert client.post(path, json=payload).status_code == 422
