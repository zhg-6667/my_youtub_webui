from __future__ import annotations

from pathlib import Path

import pytest

from scripts.upload_bilibili import (
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


def test_build_upload_command_requires_metadata(tmp_path: Path) -> None:
    task_dir = _make_task_dir(tmp_path)
    video = task_dir / "media" / VIDEO_NAME
    video.write_bytes(b"video")

    with pytest.raises(UploadBilibiliError, match="找不到元数据文件"):
        build_upload_command(UploadBilibiliOptions(path=str(task_dir), title="标题", biliup="biliup"))
