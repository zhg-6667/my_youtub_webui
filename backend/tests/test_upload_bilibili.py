from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.upload_bilibili import (
    CONCAT_VIDEO_NAME,
    CREATION_STATEMENT_EXTRA_FIELDS,
    COPYRIGHT_SELF,
    DEFAULT_TID,
    BilibiliTopic,
    PreparedBilibiliUpload,
    SOURCE_VIDEO_NAME,
    VIDEO_NAME,
    UploadBilibiliError,
    UploadBilibiliOptions,
    add_bilibili_season_episode,
    build_extra_fields,
    build_desc,
    find_recent_creator_archive_by_bvid,
    find_recent_creator_archive_by_title,
    build_upload_command,
    is_retryable_biliup_upload_error,
    parse_biliup_list_bvid,
    parse_uploaded_archive,
    parse_dtime,
    resolve_archive_aid,
    resolve_archive_cid,
    resolve_paths,
    run_upload_command,
)

FALLBACK_VIDEO_NAME = "video_final.mp4"


def _make_task_dir(tmp_path: Path) -> Path:
    task_dir = tmp_path / "task__abc123"
    (task_dir / "media").mkdir(parents=True)
    return task_dir


def _write_metadata(task_dir: Path) -> None:
    metadata = task_dir / "metadata"
    metadata.mkdir()
    (metadata / "ytdlp_info.json").write_text(
        """
        {
          "id": "abc123",
          "title": "Original title",
          "uploader": "WolfeyVGC",
          "upload_date": "20260117",
          "webpage_url": "https://www.youtube.com/watch?v=abc123"
        }
        """,
        encoding="utf-8",
    )


def _write_metadata_with_author(task_dir: Path, author: str) -> None:
    metadata = task_dir / "metadata"
    metadata.mkdir(exist_ok=True)
    (metadata / "ytdlp_info.json").write_text(
        f"""
        {{
          "id": "abc123",
          "title": "Original title",
          "uploader": "{author}",
          "upload_date": "20260117",
          "webpage_url": "https://www.youtube.com/watch?v=abc123"
        }}
        """,
        encoding="utf-8",
    )


def test_build_desc_uses_cta_date_and_source_url() -> None:
    assert build_desc(
        {
            "upload_date": "20260731",
            "webpage_url": "https://www.youtube.com/watch?v=jb1IYQ-Uaeo",
        }
    ) == (
        "请大家多多关注、点赞、投币、收藏，有什么想看的也可以评论区说\n"
        "2026年7月31日\n"
        "https://www.youtube.com/watch?v=jb1IYQ-Uaeo"
    )


def test_resolve_paths_prefers_trimmed_video_for_task_directory(tmp_path: Path) -> None:
    task_dir = _make_task_dir(tmp_path)
    preferred = task_dir / "media" / VIDEO_NAME
    fallback = task_dir / "media" / FALLBACK_VIDEO_NAME
    fallback.write_bytes(b"fallback")
    preferred.write_bytes(b"preferred")

    resolved_task_dir, resolved_video = resolve_paths(str(task_dir))

    assert resolved_task_dir == task_dir.resolve()
    assert resolved_video == preferred.resolve()


def test_resolve_paths_uses_video_final_when_trimmed_missing(tmp_path: Path) -> None:
    task_dir = _make_task_dir(tmp_path)
    fallback = task_dir / "media" / FALLBACK_VIDEO_NAME
    fallback.write_bytes(b"fallback")

    resolved_task_dir, resolved_video = resolve_paths(str(task_dir))

    assert resolved_task_dir == task_dir.resolve()
    assert resolved_video == fallback.resolve()


def test_resolve_paths_uses_concat_video_before_final_when_trimmed_missing(tmp_path: Path) -> None:
    task_dir = _make_task_dir(tmp_path)
    concatenated = task_dir / "media" / CONCAT_VIDEO_NAME
    fallback = task_dir / "media" / FALLBACK_VIDEO_NAME
    fallback.write_bytes(b"fallback")
    concatenated.write_bytes(b"concat")

    resolved_task_dir, resolved_video = resolve_paths(str(task_dir))

    assert resolved_task_dir == task_dir.resolve()
    assert resolved_video == concatenated.resolve()


def test_resolve_paths_uses_source_video_when_outputs_missing(tmp_path: Path) -> None:
    task_dir = _make_task_dir(tmp_path)
    source = task_dir / "media" / SOURCE_VIDEO_NAME
    source.write_bytes(b"source")

    resolved_task_dir, resolved_video = resolve_paths(str(task_dir))

    assert resolved_task_dir == task_dir.resolve()
    assert resolved_video == source.resolve()



def test_resolve_paths_uses_fallback_when_trimmed_path_is_directory(tmp_path: Path) -> None:
    task_dir = _make_task_dir(tmp_path)
    preferred = task_dir / "media" / VIDEO_NAME
    fallback = task_dir / "media" / FALLBACK_VIDEO_NAME
    preferred.mkdir()
    fallback.write_bytes(b"fallback")

    resolved_task_dir, resolved_video = resolve_paths(str(task_dir))

    assert resolved_task_dir == task_dir.resolve()
    assert resolved_video == fallback.resolve()


def test_resolve_paths_keeps_preferred_path_when_no_directory_video_exists(tmp_path: Path) -> None:
    task_dir = _make_task_dir(tmp_path)
    preferred = task_dir / "media" / VIDEO_NAME

    resolved_task_dir, resolved_video = resolve_paths(str(task_dir))

    assert resolved_task_dir == task_dir.resolve()
    assert resolved_video == preferred.resolve()
    assert not resolved_video.exists()


def test_parse_dtime_accepts_timestamp_and_datetime() -> None:
    assert parse_dtime("9999999999") == "9999999999"
    assert parse_dtime("2999-07-01T18:00").isdigit()
    assert parse_dtime("2999-07-01 18:00").isdigit()


def test_parse_dtime_rejects_past_time() -> None:
    with pytest.raises(UploadBilibiliError, match="定时发布时间必须在未来"):
        parse_dtime("2000-01-01T00:00")


def test_parse_uploaded_archive_accepts_aid_and_bvid() -> None:
    assert parse_uploaded_archive('{"aid":123456,"bvid":"BV1abcDEF234"}') == (123456, "BV1abcDEF234")
    assert parse_uploaded_archive("提交成功 av654321") == (654321, None)


def test_parse_biliup_list_bvid_matches_title() -> None:
    output = "\x1b[1;93mBV1Xob862EhV\t【中配】标题WolfeyVGC\t审核中\x1b[0m\n"
    assert parse_biliup_list_bvid(output, "【中配】标题WolfeyVGC") == "BV1Xob862EhV"


def test_parse_biliup_list_bvid_accepts_empty_output() -> None:
    assert parse_biliup_list_bvid(None, "【中配】标题WolfeyVGC") is None


def test_retryable_biliup_upload_error_detects_expired_peer_certificate() -> None:
    stderr = (
        "\x1b[1merror sending request for url "
        "(https://upos-cs-upcdnbldsa.bilivideo.com/ugcever/demo.mp4)\x1b[22m\n"
        "\x1b[1mclient error (Connect)\x1b[22m\n"
        "\x1b[1minvalid peer certificate: Expired\x1b[22m"
    )

    assert is_retryable_biliup_upload_error(stderr=stderr)


def test_find_recent_creator_archive_by_title_reads_audit_list(monkeypatch) -> None:
    def fake_load_cookie_bundle(cookie=None):
        return {"SESSDATA": "ok", "bili_jct": "csrf"}, "csrf"

    def fake_request_json(method, url, **kwargs):
        assert kwargs["params"]["status"] in {"is_pubing", "pubed"}
        return {
            "code": 0,
            "data": {
                "arc_audits": [
                    {
                        "Archive": {
                            "aid": 117110688777235,
                            "bvid": "BV129bY6jENE",
                            "title": "狼叔：星晶化是个好属性！-WolfeyVGC",
                        }
                    }
                ]
            },
        }

    monkeypatch.setattr("scripts.upload_bilibili.load_cookie_bundle", fake_load_cookie_bundle)
    monkeypatch.setattr("scripts.upload_bilibili.request_json", fake_request_json)

    assert find_recent_creator_archive_by_title(
        title="狼叔：星晶化是个好属性！-WolfeyVGC",
    ) == (117110688777235, "BV129bY6jENE")


def test_find_recent_creator_archive_by_bvid_reads_archives_pages(monkeypatch) -> None:
    calls: list[tuple[str, int]] = []

    def fake_load_cookie_bundle(cookie=None):
        return {"SESSDATA": "ok", "bili_jct": "csrf"}, "csrf"

    def fake_request_json(method, url, **kwargs):
        status = kwargs["params"]["status"]
        page = kwargs["params"]["pn"]
        calls.append((status, page))
        if status == "not_pubed" and page == 2:
            return {
                "code": 0,
                "data": {
                    "archives": [
                        {
                            "aid": 117110688777236,
                            "bvid": "BV1qUbr6QEjG",
                            "title": "如果初代宝可梦交换进化石会怎样？",
                        }
                    ]
                },
            }
        return {"code": 0, "data": {"arc_audits": [{}] if page == 1 else []}}

    monkeypatch.setattr("scripts.upload_bilibili.load_cookie_bundle", fake_load_cookie_bundle)
    monkeypatch.setattr("scripts.upload_bilibili.request_json", fake_request_json)

    assert find_recent_creator_archive_by_bvid(
        bvid="BV1qUbr6QEjG",
    ) == (117110688777236, "BV1qUbr6QEjG")
    assert ("not_pubed", 2) in calls


def test_resolve_archive_aid_retries_creator_lookup(monkeypatch) -> None:
    calls = 0
    sleeps: list[float] = []

    def fake_request_json(method, url, **kwargs):
        raise UploadBilibiliError("not visible yet")

    def fake_find_recent_creator_archive_by_bvid(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return None, kwargs["bvid"]
        return 117114245487540, kwargs["bvid"]

    monkeypatch.setattr("scripts.upload_bilibili.request_json", fake_request_json)
    monkeypatch.setattr(
        "scripts.upload_bilibili.find_recent_creator_archive_by_bvid",
        fake_find_recent_creator_archive_by_bvid,
    )
    monkeypatch.setattr("scripts.upload_bilibili.time.sleep", sleeps.append)

    assert resolve_archive_aid(
        aid=None,
        bvid="BV1aZbr61E2c",
        max_attempts=2,
        delay_seconds=0.25,
    ) == 117114245487540
    assert sleeps == [0.25]


def test_resolve_archive_cid_reads_public_view(monkeypatch) -> None:
    def fake_request_json(method, url, **kwargs):
        assert method == "GET"
        assert url == "https://api.bilibili.com/x/web-interface/view"
        assert kwargs["params"] == {"aid": 117114312594668}
        return {"code": 0, "data": {"aid": 117114312594668, "pages": [{"cid": 41125020234}]}}

    monkeypatch.setattr("scripts.upload_bilibili.request_json", fake_request_json)

    assert resolve_archive_cid(aid=117114312594668) == 41125020234


def test_add_bilibili_season_episode_retries_transient_not_found(monkeypatch) -> None:
    calls = 0
    sleeps: list[float] = []

    def fake_load_cookie_bundle(cookie=None):
        return {"SESSDATA": "ok", "bili_jct": "csrf"}, "csrf"

    def fake_request_json(method, url, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise UploadBilibiliError("B 站接口返回错误", api_code=-404)
        return {"code": 0, "data": {"ok": True}}

    monkeypatch.setattr("scripts.upload_bilibili.load_cookie_bundle", fake_load_cookie_bundle)
    monkeypatch.setattr("scripts.upload_bilibili.request_json", fake_request_json)
    monkeypatch.setattr("scripts.upload_bilibili.time.sleep", sleeps.append)

    assert add_bilibili_season_episode(
        aid=117114312594668,
        cid=41125020234,
        section_id=9879530,
        title="Pokémon Adventures Manga Animation 3-Anima Red",
        max_attempts=2,
        delay_seconds=0.25,
    ) == {"code": 0, "data": {"ok": True}}
    assert sleeps == [0.25]


def test_add_bilibili_season_episode_uses_add_endpoint_payload(monkeypatch) -> None:
    requests: list[dict] = []

    def fake_load_cookie_bundle(cookie=None):
        return {"SESSDATA": "ok", "bili_jct": "csrf"}, "csrf"

    def fake_request_json(method, url, **kwargs):
        requests.append({"method": method, "url": url, **kwargs})
        return {"code": 0}

    monkeypatch.setattr("scripts.upload_bilibili.load_cookie_bundle", fake_load_cookie_bundle)
    monkeypatch.setattr("scripts.upload_bilibili.request_json", fake_request_json)

    add_bilibili_season_episode(
        aid=117114312594668,
        cid=41125020234,
        section_id=9879530,
        title="Pokémon Adventures Manga Animation 3-Anima Red",
    )

    assert requests == [
        {
            "method": "POST",
            "url": "https://member.bilibili.com/x2/creative/web/season/section/episodes/add",
            "cookies": {"SESSDATA": "ok", "bili_jct": "csrf"},
            "params": {"csrf": "csrf"},
            "json": {
                "sectionId": 9879530,
                "episodes": [
                    {
                        "aid": 117114312594668,
                        "cid": 41125020234,
                        "title": "Pokémon Adventures Manga Animation 3-Anima Red",
                        "charging_pay": 0,
                    }
                ],
            },
            "headers": {"Content-Type": "application/json; charset=UTF-8"},
        }
    ]


def test_add_bilibili_season_episode_does_not_retry_non_transient_errors(monkeypatch) -> None:
    calls = 0

    def fake_load_cookie_bundle(cookie=None):
        return {"SESSDATA": "ok", "bili_jct": "csrf"}, "csrf"

    def fake_request_json(method, url, **kwargs):
        nonlocal calls
        calls += 1
        raise UploadBilibiliError("B 站接口返回错误", api_code=-400)

    monkeypatch.setattr("scripts.upload_bilibili.load_cookie_bundle", fake_load_cookie_bundle)
    monkeypatch.setattr("scripts.upload_bilibili.request_json", fake_request_json)

    with pytest.raises(UploadBilibiliError, match="B 站接口返回错误"):
        add_bilibili_season_episode(
            aid=117114312594668,
            cid=41125020234,
            section_id=9879530,
            title="Pokémon Adventures Manga Animation 3-Anima Red",
            max_attempts=3,
            delay_seconds=0,
        )
    assert calls == 1


def test_run_upload_command_falls_back_to_biliup_list_for_bvid(monkeypatch, tmp_path: Path) -> None:
    cookie = tmp_path / "cookies.json"
    cookie.write_text("{}", encoding="utf-8")
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    prepared = PreparedBilibiliUpload(
        command=["biliup", "-u", str(cookie), "upload", str(video)],
        task_dir=tmp_path,
        video=video,
        cookie=cookie,
        title="【中配】标题WolfeyVGC",
        desc="desc",
        tag="tag",
        tid=171,
        topic_name="",
        topic_id=None,
        topic_mission_id=None,
        cover_path="",
        dtime=None,
        raw_dtime=None,
    )
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["errors"] == "replace"
        if "list" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="BV1Xob862EhV\t【中配】标题WolfeyVGC\t审核中\n",
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout="uploaded\n", stderr="")

    monkeypatch.setattr("scripts.upload_bilibili.subprocess.run", fake_run)
    monkeypatch.setattr("scripts.upload_bilibili.find_recent_creator_archive_by_title", lambda **kwargs: (None, None))
    monkeypatch.setattr(
        "scripts.upload_bilibili.find_recent_creator_archive_by_bvid",
        lambda **kwargs: (None, kwargs["bvid"]),
    )

    result = run_upload_command(prepared)

    assert result.bvid == "BV1Xob862EhV"
    assert calls[1][:4] == ["biliup", "-u", str(cookie), "list"]


def test_run_upload_command_resolves_aid_when_upload_outputs_only_bvid(monkeypatch, tmp_path: Path) -> None:
    cookie = tmp_path / "cookies.json"
    cookie.write_text("{}", encoding="utf-8")
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    prepared = PreparedBilibiliUpload(
        command=["biliup", "-u", str(cookie), "upload", str(video)],
        task_dir=tmp_path,
        video=video,
        cookie=cookie,
        title="如果初代宝可梦交换进化石会怎样？",
        desc="desc",
        tag="tag",
        tid=171,
        topic_name="",
        topic_id=None,
        topic_mission_id=None,
        cover_path="",
        dtime=None,
        raw_dtime=None,
    )

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout="投稿成功 BV1qUbr6QEjG\n", stderr="")

    monkeypatch.setattr("scripts.upload_bilibili.subprocess.run", fake_run)
    monkeypatch.setattr(
        "scripts.upload_bilibili.find_recent_creator_archive_by_bvid",
        lambda **kwargs: (117110688777236, kwargs["bvid"]),
    )

    result = run_upload_command(prepared)

    assert result.aid == 117110688777236
    assert result.bvid == "BV1qUbr6QEjG"


def test_run_upload_command_retries_expired_peer_certificate(monkeypatch, tmp_path: Path) -> None:
    cookie = tmp_path / "cookies.json"
    cookie.write_text("{}", encoding="utf-8")
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    prepared = PreparedBilibiliUpload(
        command=["biliup", "-u", str(cookie), "upload", str(video)],
        task_dir=tmp_path,
        video=video,
        cookie=cookie,
        title="重试标题",
        desc="desc",
        tag="tag",
        tid=171,
        topic_name="",
        topic_id=None,
        topic_mission_id=None,
        cover_path="",
        dtime=None,
        raw_dtime=None,
    )
    calls: list[list[str]] = []
    sleeps: list[float] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            return subprocess.CompletedProcess(
                command,
                1,
                stdout="",
                stderr=(
                    "error sending request for url "
                    "(https://upos-cs-upcdnbldsa.bilivideo.com/ugcever/demo.mp4)\n"
                    "client error (Connect)\n"
                    "invalid peer certificate: Expired\n"
                ),
            )
        return subprocess.CompletedProcess(command, 0, stdout="投稿成功 BV1retryOk\n", stderr="")

    monkeypatch.setattr("scripts.upload_bilibili.subprocess.run", fake_run)
    monkeypatch.setattr("scripts.upload_bilibili.time.sleep", sleeps.append)
    monkeypatch.setattr(
        "scripts.upload_bilibili.find_recent_creator_archive_by_bvid",
        lambda **kwargs: (117110688777237, kwargs["bvid"]),
    )

    result = run_upload_command(prepared)

    assert len(calls) == 2
    assert sleeps == [8.0]
    assert result.aid == 117110688777237
    assert result.bvid == "BV1retryOk"
    assert "[biliup 第 1 次尝试]" in result.stderr


def test_build_upload_command_includes_dtime_when_scheduled(tmp_path: Path) -> None:
    task_dir = _make_task_dir(tmp_path)
    video = task_dir / "media" / VIDEO_NAME
    video.write_bytes(b"video")
    _write_metadata(task_dir)
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"cover")

    prepared = build_upload_command(
        UploadBilibiliOptions(
            path=str(task_dir),
            title="最弱的草系宝可梦是谁？",
            cover=str(cover),
            biliup="biliup",
            dtime="2999-07-01T18:00",
        )
    )

    assert "--dtime" in prepared.command
    assert prepared.command[prepared.command.index("--title") + 1] == "最弱的草系宝可梦是谁？-WolfeyVGC"


def test_build_upload_command_omits_dtime_for_immediate_publish(tmp_path: Path) -> None:
    task_dir = _make_task_dir(tmp_path)
    video = task_dir / "media" / VIDEO_NAME
    video.write_bytes(b"video")
    _write_metadata(task_dir)
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"cover")

    prepared = build_upload_command(
        UploadBilibiliOptions(
            path=str(task_dir),
            title="立即发布",
            cover=str(cover),
            biliup="biliup",
        )
    )

    assert "--dtime" not in prepared.command


def test_build_upload_command_sets_creation_statement_to_no_mark(tmp_path: Path) -> None:
    task_dir = _make_task_dir(tmp_path)
    video = task_dir / "media" / VIDEO_NAME
    video.write_bytes(b"video")
    _write_metadata(task_dir)

    prepared = build_upload_command(
        UploadBilibiliOptions(
            path=str(task_dir),
            title="创作声明",
            biliup="biliup",
        )
    )

    assert prepared.command[prepared.command.index("--copyright") + 1] == str(COPYRIGHT_SELF)
    assert prepared.command[prepared.command.index("--submit") + 1] == "web"
    assert prepared.command[prepared.command.index("--extra-fields") + 1] == CREATION_STATEMENT_EXTRA_FIELDS


def test_build_extra_fields_includes_topic_id_and_mission_id() -> None:
    assert build_extra_fields(topic_id=123, mission_id=456) == (
        '{"creationStatementId":-1,"recreate":-1,"topic_id":123,"mission_id":456}'
    )


def test_build_upload_command_includes_explicit_topic_id(tmp_path: Path) -> None:
    task_dir = _make_task_dir(tmp_path)
    video = task_dir / "media" / VIDEO_NAME
    video.write_bytes(b"video")
    _write_metadata(task_dir)

    prepared = build_upload_command(
        UploadBilibiliOptions(
            path=str(task_dir),
            title="话题投稿",
            biliup="biliup",
            topic_id=123,
            topic_mission_id=456,
        )
    )

    assert prepared.topic_id == 123
    assert prepared.topic_mission_id == 456
    assert prepared.command[prepared.command.index("--extra-fields") + 1] == (
        '{"creationStatementId":-1,"recreate":-1,"topic_id":123,"mission_id":456}'
    )


def test_build_upload_command_resolves_topic_name(monkeypatch, tmp_path: Path) -> None:
    task_dir = _make_task_dir(tmp_path)
    video = task_dir / "media" / VIDEO_NAME
    video.write_bytes(b"video")
    _write_metadata(task_dir)
    cookie = tmp_path / "cookies.json"
    cookie.write_text("{}", encoding="utf-8")
    cookie_path = cookie.resolve()

    def fake_resolve(topic_name, *, cookie=None):
        assert topic_name == "宝可梦集结"
        assert cookie == cookie_path
        return BilibiliTopic(id=1245603, name="宝可梦集结", mission_id=None)

    monkeypatch.setattr("scripts.upload_bilibili.resolve_bilibili_topic_by_name", fake_resolve)

    prepared = build_upload_command(
        UploadBilibiliOptions(
            path=str(task_dir),
            title="话题投稿",
            biliup="biliup",
            cookie=str(cookie),
            topic_name="宝可梦集结",
        )
    )

    assert prepared.topic_name == "宝可梦集结"
    assert prepared.topic_id == 1245603
    assert prepared.command[prepared.command.index("--extra-fields") + 1] == (
        '{"creationStatementId":-1,"recreate":-1,"topic_id":1245603}'
    )


def test_build_upload_command_uses_cached_download_stage_cover(tmp_path: Path) -> None:
    task_dir = _make_task_dir(tmp_path)
    video = task_dir / "media" / VIDEO_NAME
    video.write_bytes(b"video")
    _write_metadata(task_dir)
    cached_cover = task_dir / "cover.jpg"
    cached_cover.write_bytes(b"cover")

    prepared = build_upload_command(
        UploadBilibiliOptions(
            path=str(task_dir),
            title="缓存封面",
            biliup="biliup",
        )
    )

    assert prepared.cover_path == str(cached_cover)
    assert prepared.command[prepared.command.index("--cover") + 1] == str(cached_cover)


def test_build_upload_command_does_not_download_cover_when_cached_cover_missing(tmp_path: Path) -> None:
    task_dir = _make_task_dir(tmp_path)
    video = task_dir / "media" / VIDEO_NAME
    video.write_bytes(b"video")
    _write_metadata(task_dir)

    prepared = build_upload_command(
        UploadBilibiliOptions(
            path=str(task_dir),
            title="无缓存封面",
            biliup="biliup",
        )
    )

    assert prepared.cover_path == ""
    assert "--cover" not in prepared.command


def test_build_upload_command_uses_configured_author_tags(tmp_path: Path) -> None:
    task_dir = _make_task_dir(tmp_path)
    (task_dir / "media" / VIDEO_NAME).write_bytes(b"video")
    _write_metadata_with_author(task_dir, "WolfeyVGC")
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"cover")

    prepared = build_upload_command(
        UploadBilibiliOptions(
            path=str(task_dir),
            title="标题",
            cover=str(cover),
            biliup="biliup",
        )
    )

    assert prepared.tag == "狼叔,宝可梦,神奇宝贝,VGC,WolfeyVGC,对战游戏,Pokemon,口袋妖怪"
    assert prepared.command[prepared.command.index("--tag") + 1] == prepared.tag


def test_build_upload_command_uses_configured_author_tid(tmp_path: Path) -> None:
    task_dir = _make_task_dir(tmp_path)
    (task_dir / "media" / VIDEO_NAME).write_bytes(b"video")
    _write_metadata_with_author(task_dir, "WolfeyVGC")
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"cover")

    prepared = build_upload_command(
        UploadBilibiliOptions(
            path=str(task_dir),
            title="标题",
            cover=str(cover),
            biliup="biliup",
            author_tids_json='{"WolfeyVGC": 201}',
        )
    )

    assert prepared.tid == 201
    assert prepared.command[prepared.command.index("--tid") + 1] == "201"


def test_build_upload_command_uses_combined_author_config(tmp_path: Path) -> None:
    task_dir = _make_task_dir(tmp_path)
    (task_dir / "media" / VIDEO_NAME).write_bytes(b"video")
    _write_metadata_with_author(task_dir, "WolfeyVGC")
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"cover")

    prepared = build_upload_command(
        UploadBilibiliOptions(
            path=str(task_dir),
            title="标题",
            cover=str(cover),
            biliup="biliup",
            author_config_json='{"WolfeyVGC":{"tags":"合并,标签","tid":201}}',
        )
    )

    assert prepared.tag == "合并,标签"
    assert prepared.tid == 201


def test_build_upload_command_combined_config_overrides_legacy_author_config(tmp_path: Path) -> None:
    task_dir = _make_task_dir(tmp_path)
    (task_dir / "media" / VIDEO_NAME).write_bytes(b"video")
    _write_metadata_with_author(task_dir, "WolfeyVGC")
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"cover")

    prepared = build_upload_command(
        UploadBilibiliOptions(
            path=str(task_dir),
            title="标题",
            cover=str(cover),
            biliup="biliup",
            author_config_json='{"WolfeyVGC":{"tags":"新标签","tid":201}}',
            author_tags_json='{"WolfeyVGC":"旧标签"}',
            author_tids_json='{"WolfeyVGC":17}',
        )
    )

    assert prepared.tag == "新标签"
    assert prepared.tid == 201


def test_build_upload_command_uses_default_tid_for_unconfigured_author(tmp_path: Path) -> None:
    task_dir = _make_task_dir(tmp_path)
    (task_dir / "media" / VIDEO_NAME).write_bytes(b"video")
    _write_metadata_with_author(task_dir, "UnknownCreator")
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"cover")

    prepared = build_upload_command(
        UploadBilibiliOptions(
            path=str(task_dir),
            title="标题",
            cover=str(cover),
            biliup="biliup",
            author_tids_json='{"WolfeyVGC": 201}',
        )
    )

    assert prepared.tid == DEFAULT_TID


def test_build_upload_command_explicit_tid_overrides_author_tid(tmp_path: Path) -> None:
    task_dir = _make_task_dir(tmp_path)
    (task_dir / "media" / VIDEO_NAME).write_bytes(b"video")
    _write_metadata_with_author(task_dir, "WolfeyVGC")
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"cover")

    prepared = build_upload_command(
        UploadBilibiliOptions(
            path=str(task_dir),
            title="标题",
            cover=str(cover),
            biliup="biliup",
            tid=17,
            author_tids_json='{"WolfeyVGC": 201}',
        )
    )

    assert prepared.tid == 17


def test_build_upload_command_uses_empty_tags_for_unconfigured_author(tmp_path: Path) -> None:
    task_dir = _make_task_dir(tmp_path)
    (task_dir / "media" / VIDEO_NAME).write_bytes(b"video")
    _write_metadata_with_author(task_dir, "UnknownCreator")
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"cover")

    prepared = build_upload_command(
        UploadBilibiliOptions(
            path=str(task_dir),
            title="标题",
            cover=str(cover),
            biliup="biliup",
        )
    )

    assert prepared.tag == ""
    assert prepared.command[prepared.command.index("--tag") + 1] == ""


def test_build_upload_command_allows_author_tags_env_override(monkeypatch, tmp_path: Path) -> None:
    task_dir = _make_task_dir(tmp_path)
    (task_dir / "media" / VIDEO_NAME).write_bytes(b"video")
    _write_metadata_with_author(task_dir, "UnknownCreator")
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"cover")
    monkeypatch.setenv("BILIBILI_AUTHOR_TAGS_JSON", '{"UnknownCreator":"自定义,标签"}')

    prepared = build_upload_command(
        UploadBilibiliOptions(
            path=str(task_dir),
            title="标题",
            cover=str(cover),
            biliup="biliup",
        )
    )

    assert prepared.tag == "自定义,标签"


def test_build_upload_command_allows_author_tids_env_override(monkeypatch, tmp_path: Path) -> None:
    task_dir = _make_task_dir(tmp_path)
    (task_dir / "media" / VIDEO_NAME).write_bytes(b"video")
    _write_metadata_with_author(task_dir, "UnknownCreator")
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"cover")
    monkeypatch.setenv("BILIBILI_AUTHOR_TIDS_JSON", '{"UnknownCreator": 201}')

    prepared = build_upload_command(
        UploadBilibiliOptions(
            path=str(task_dir),
            title="标题",
            cover=str(cover),
            biliup="biliup",
        )
    )

    assert prepared.tid == 201


def test_build_upload_command_allows_combined_author_config_env_override(monkeypatch, tmp_path: Path) -> None:
    task_dir = _make_task_dir(tmp_path)
    (task_dir / "media" / VIDEO_NAME).write_bytes(b"video")
    _write_metadata_with_author(task_dir, "UnknownCreator")
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"cover")
    monkeypatch.setenv("BILIBILI_AUTHOR_CONFIG_JSON", '{"UnknownCreator":{"tags":"环境,配置","tid":201}}')

    prepared = build_upload_command(
        UploadBilibiliOptions(
            path=str(task_dir),
            title="标题",
            cover=str(cover),
            biliup="biliup",
        )
    )

    assert prepared.tag == "环境,配置"
    assert prepared.tid == 201


def test_build_upload_command_requires_metadata(tmp_path: Path) -> None:
    task_dir = _make_task_dir(tmp_path)
    video = task_dir / "media" / VIDEO_NAME
    video.write_bytes(b"video")

    with pytest.raises(UploadBilibiliError, match="找不到元数据文件"):
        build_upload_command(UploadBilibiliOptions(path=str(task_dir), title="标题", biliup="biliup"))
