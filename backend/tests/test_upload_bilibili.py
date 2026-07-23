from __future__ import annotations

from pathlib import Path

import pytest

from scripts.upload_bilibili import (
    CONCAT_VIDEO_NAME,
    DEFAULT_TID,
    SOURCE_VIDEO_NAME,
    VIDEO_NAME,
    UploadBilibiliError,
    UploadBilibiliOptions,
    build_upload_command,
    parse_dtime,
    resolve_paths,
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
    assert prepared.command[prepared.command.index("--title") + 1] == "【中配】最弱的草系宝可梦是谁？WolfeyVGC"


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
