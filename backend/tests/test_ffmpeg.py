from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from backend.app.adapters import ffmpeg
from backend.app.adapters import watermark_mask


def test_video_orientation_uses_height_greater_than_width(monkeypatch):
    def fake_run(cmd, capture_output=False, text=False, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="720,1280\n", stderr="")

    monkeypatch.setattr(ffmpeg.subprocess, "run", fake_run)

    assert ffmpeg.get_video_orientation(Path("video.mp4")) == "portrait"


def test_video_orientation_defaults_to_landscape_when_probe_fails(monkeypatch):
    def fake_run(cmd, capture_output=False, text=False, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="ffprobe failed")

    monkeypatch.setattr(ffmpeg.subprocess, "run", fake_run)

    assert ffmpeg.get_video_orientation(Path("video.mp4")) == "landscape"


def test_subtitle_styles_match_backend_orientation_rules():
    portrait = ffmpeg.subtitle_style_for_orientation("portrait", "Noto Sans CJK SC", "zh")
    landscape = ffmpeg.subtitle_style_for_orientation("landscape", "Noto Sans CJK SC", "zh")

    assert "FontSize=12" in portrait
    assert "MarginV=70" in portrait
    assert "FontSize=24" in landscape
    assert "MarginV=5" in landscape


def test_subtitle_styles_use_smaller_size_for_english():
    portrait_en = ffmpeg.subtitle_style_for_orientation("portrait", "Arial", "en")
    landscape_en = ffmpeg.subtitle_style_for_orientation("landscape", "Arial", "en")

    assert "FontSize=9" in portrait_en
    assert "FontSize=18" in landscape_en


def test_subtitle_filter_picks_chinese_font_for_zh_srt(monkeypatch, tmp_path):
    monkeypatch.setattr(ffmpeg, "get_video_orientation", lambda _: "landscape")
    sub_zh = tmp_path / "subtitles.zh.srt"
    sub_zh.write_text("", encoding="utf-8")
    assert "FontName=Noto Sans CJK SC" in ffmpeg.subtitle_filter(tmp_path / "v.mp4", sub_zh, tmp_path)
    sub_en = tmp_path / "subtitles.en.srt"
    sub_en.write_text("", encoding="utf-8")
    assert "FontName=Arial" in ffmpeg.subtitle_filter(tmp_path / "v.mp4", sub_en, tmp_path)


def test_merge_video_burns_portrait_subtitles(monkeypatch, tmp_path):
    session = tmp_path / "session"
    metadata_dir = session / "metadata"
    metadata_dir.mkdir(parents=True)
    timings = metadata_dir / "timings.json"
    timings.write_text(
        json.dumps(
            {
                "translation": [
                    {
                        "start_time": 0,
                        "end_time": 1200,
                        "actual_start_time": 0,
                        "actual_end_time": 1200,
                        "zh": "你好",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    commands: list[list[str]] = []
    cwd_values: list[Path | None] = []

    def fake_run(cmd, capture_output=False, text=False, check=False, **kwargs):
        commands.append(cmd)
        cwd_values.append(kwargs.get("cwd"))
        if cmd[0] == ffmpeg.ffprobe_binary():
            return subprocess.CompletedProcess(cmd, 0, stdout="720,1280\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(ffmpeg.subprocess, "run", fake_run)
    monkeypatch.setattr(ffmpeg, "_video_encoders", lambda: [["-c:v", "libx264", "-preset", "fast", "-crf", "23"]])

    final_video = ffmpeg.merge_video(
        tmp_path / "video.mp4",
        tmp_path / "dubbing.wav",
        tmp_path / "bgm.wav",
        timings,
        session,
    )

    assert final_video == session / "media" / "video_final.mp4"
    final_command = next(cmd for cmd in reversed(commands) if "-vf" in cmd)
    filter_arg = final_command[final_command.index("-vf") + 1]
    assert filter_arg.startswith("subtitles=filename='metadata/subtitles.zh.srt'")
    assert "FontSize=12" in filter_arg
    assert "MarginV=70" in filter_arg
    assert "-c:s" not in final_command
    assert cwd_values[-1] == session.resolve()


def test_merge_video_uses_absolute_media_paths_when_cwd_is_session(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    session = Path("workfolder") / "uploader" / "title__videoid"
    metadata_dir = session / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    timings = metadata_dir / "timings.json"
    timings.write_text(
        json.dumps(
            {
                "translation": [
                    {
                        "start_time": 0,
                        "end_time": 1200,
                        "actual_start_time": 0,
                        "actual_end_time": 1200,
                        "zh": "你好",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    commands: list[list[str]] = []
    cwd_values: list[Path | None] = []

    def fake_run(cmd, capture_output=False, text=False, check=False, **kwargs):
        commands.append(cmd)
        cwd_values.append(kwargs.get("cwd"))
        if cmd[0] == "ffprobe":
            return subprocess.CompletedProcess(cmd, 0, stdout="720,1280\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(ffmpeg.subprocess, "run", fake_run)
    monkeypatch.setattr(ffmpeg, "_video_encoders", lambda: [["-c:v", "libx264", "-preset", "fast", "-crf", "23"]])

    ffmpeg.merge_video(
        session / "media" / "video_source.mp4",
        session / "tmp" / "audio_dubbing.wav",
        session / "media" / "audio_bgm.wav",
        timings,
        session,
    )

    mix_command = commands[0]
    final_command = commands[-1]
    assert Path(mix_command[mix_command.index("-i") + 1]).is_absolute()
    assert Path(mix_command[mix_command.index("-i", mix_command.index("-i") + 1) + 1]).is_absolute()
    assert Path(mix_command[-1]).is_absolute()
    assert Path(final_command[final_command.index("-i") + 1]).is_absolute()
    assert Path(final_command[final_command.index("-i", final_command.index("-i") + 1) + 1]).is_absolute()
    assert Path(final_command[-1]).is_absolute()
    assert cwd_values[-1] == session.resolve()


def test_fast_trim_video_uses_stream_copy_segments(monkeypatch, tmp_path):
    session = tmp_path / "session"
    media = session / "media"
    media.mkdir(parents=True)
    input_video = media / "video_final.mp4"
    input_video.write_bytes(b"source")
    commands: list[list[str]] = []

    monkeypatch.setattr(ffmpeg, "_probe_duration", lambda _: 100.0)

    def fake_run(cmd, check=False, timeout=None, cwd=None, **kwargs):
        commands.append(cmd)
        output = Path(cmd[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"out")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(ffmpeg.subprocess, "run", fake_run)

    output = ffmpeg.fast_trim_video(input_video, [{"start": 10, "end": 20}], session)

    assert output == media / "video_final_trimmed.mp4"
    assert output.read_bytes() == b"out"
    segment_commands = [cmd for cmd in commands if "-ss" in cmd]
    concat_commands = [cmd for cmd in commands if "-f" in cmd and "concat" in cmd]
    assert len(segment_commands) == 2
    assert len(concat_commands) == 1
    assert all(cmd[cmd.index("-c") + 1] == "copy" for cmd in segment_commands)
    assert concat_commands[0][concat_commands[0].index("-c") + 1] == "copy"


def test_trim_video_reencodes_keep_segments_before_concat(monkeypatch, tmp_path):
    session = tmp_path / "session"
    media = session / "media"
    media.mkdir(parents=True)
    input_video = media / "video_final_with_cta.mp4"
    input_video.write_bytes(b"source")
    commands: list[list[str]] = []

    monkeypatch.setattr(ffmpeg, "_probe_duration", lambda _: 100.0)
    monkeypatch.setattr(ffmpeg, "_video_encoders", lambda: [["-c:v", "libx264", "-preset", "fast", "-crf", "23"]])

    def fake_run(cmd, check=False, timeout=None, cwd=None, **kwargs):
        commands.append(cmd)
        output = Path(cmd[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"out")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(ffmpeg.subprocess, "run", fake_run)

    output = ffmpeg.trim_video(
        input_video,
        [{"start": 1.0, "end": 10.0}, {"start": 20.0, "end": 99.7}],
        session,
    )

    assert output == media / "video_final_trimmed.mp4"
    assert output.read_bytes() == b"out"
    segment_commands = [cmd for cmd in commands if "-f" not in cmd]
    concat_commands = [cmd for cmd in commands if "-f" in cmd and "concat" in cmd]
    assert len(segment_commands) == 3
    assert len(concat_commands) == 1
    assert all("-filter_complex" not in cmd for cmd in commands)
    assert all(cmd[cmd.index("-c:v") + 1] == "libx264" for cmd in segment_commands)
    assert concat_commands[0][concat_commands[0].index("-c") + 1] == "copy"


def test_split_subtitle_text_breaks_on_punctuation_and_keeps_protected():
    out = ffmpeg.split_subtitle_text("我们今天讨论一下宇宙的边界，那是一个神秘话题；不过别担心，我会详细解释。")
    assert len(out) >= 3
    assert all(len(s) >= 2 for s in out)
    protected = ffmpeg.split_subtitle_text("他说《三体，黑暗森林》是经典，必读。")
    assert any("《三体，黑暗森林》" in s for s in protected)


def test_write_srt_splits_long_sentence_into_multiple_entries(tmp_path):
    session = tmp_path / "session"
    metadata_dir = session / "metadata"
    metadata_dir.mkdir(parents=True)
    timings = metadata_dir / "timings.json"
    timings.write_text(
        json.dumps(
            {
                "translation": [
                    {
                        "start_time": 0,
                        "end_time": 6000,
                        "actual_start_time": 0,
                        "actual_end_time": 6000,
                        "zh": "我们今天讨论宇宙的边界，那是一个神秘话题；不过别担心，我会详细解释",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    srt = ffmpeg.write_srt(timings, session)
    content = srt.read_text(encoding="utf-8")
    blocks = [b for b in content.strip().split("\n\n") if b.strip()]
    assert len(blocks) >= 3
    assert all("-->" in b for b in blocks)


def test_cta_font_uses_macos_cjk_font_when_windows_fonts_are_unavailable(monkeypatch):
    monkeypatch.delenv("CTA_FONT_FILE", raising=False)
    mac_font = "/System/Library/Fonts/STHeiti Medium.ttc"
    monkeypatch.setattr(Path, "exists", lambda path: path.as_posix() == mac_font)

    assert ffmpeg._drawtext_font_option() == f"fontfile='{ffmpeg._filter_path(Path(mac_font))}'"


def test_cta_font_preserves_explicit_font_override(monkeypatch, tmp_path):
    custom_font = tmp_path / "custom cjk.ttf"
    custom_font.write_bytes(b"font")
    monkeypatch.setenv("CTA_FONT_FILE", str(custom_font))

    assert ffmpeg._drawtext_font_option() == f"fontfile='{ffmpeg._filter_path(custom_font)}'"


def test_generate_cta_clip_matches_reference_stream(monkeypatch, tmp_path):
    session = tmp_path / "session"
    media = session / "media"
    media.mkdir(parents=True)
    final_video = media / "video_final.mp4"
    final_video.write_bytes(b"final")
    commands: list[list[str]] = []

    def fake_run(cmd, check=False, capture_output=False, text=False, **kwargs):
        commands.append(cmd)
        if "-show_entries" in cmd:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps(
                    {
                        "streams": [
                            {
                                "codec_type": "video",
                                "codec_name": "h264",
                                "profile": "High",
                                "level": 51,
                                "pix_fmt": "yuv420p",
                                "width": 1280,
                                "height": 720,
                                "avg_frame_rate": "30000/1001",
                            },
                            {
                                "codec_type": "audio",
                                "codec_name": "aac",
                                "sample_rate": "44100",
                                "channels": 2,
                                "channel_layout": "stereo",
                            },
                        ]
                    }
                ),
                stderr="",
            )
        Path(cmd[-1]).write_bytes(b"cta")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(ffmpeg.subprocess, "run", fake_run)

    output = ffmpeg.generate_cta_clip(final_video, session)

    assert output == session / "media" / "cta_follow_1s.mp4"
    ffmpeg_command = commands[-1]
    assert "color=c=0x121418:s=1280x720:r=30000/1001:d=1" in ffmpeg_command
    assert "anullsrc=channel_layout=stereo:sample_rate=44100:d=1" in ffmpeg_command
    assert ffmpeg_command[ffmpeg_command.index("-c:v") + 1] == "libx264"
    assert ffmpeg_command[ffmpeg_command.index("-profile:v") + 1] == "high"
    assert ffmpeg_command[ffmpeg_command.index("-level:v") + 1] == "5.1"
    assert ffmpeg_command[ffmpeg_command.index("-c:a") + 1] == "aac"


def test_generate_cta_clip_matches_av1_opus_reference(monkeypatch, tmp_path):
    session = tmp_path / "session"
    media = session / "media"
    media.mkdir(parents=True)
    source_video = media / "video_source.mp4"
    source_video.write_bytes(b"source")
    commands: list[list[str]] = []

    def fake_run(cmd, check=False, capture_output=False, text=False, **kwargs):
        commands.append(cmd)
        if "-show_entries" in cmd:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps(
                    {
                        "streams": [
                            {
                                "codec_type": "video",
                                "codec_name": "av1",
                                "profile": "Main",
                                "level": 9,
                                "pix_fmt": "yuv420p",
                                "width": 1920,
                                "height": 1080,
                                "avg_frame_rate": "60/1",
                                "time_base": "1/15360",
                            },
                            {
                                "codec_type": "audio",
                                "codec_name": "opus",
                                "sample_rate": "48000",
                                "channels": 2,
                                "channel_layout": "stereo",
                            },
                        ]
                    }
                ),
                stderr="",
            )
        Path(cmd[-1]).write_bytes(b"cta")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(ffmpeg.subprocess, "run", fake_run)

    output = ffmpeg.generate_cta_clip(source_video, session)

    assert output == media / "cta_follow_1s.mp4"
    ffmpeg_command = commands[-1]
    assert "color=c=0x121418:s=1920x1080:r=60/1:d=1" in ffmpeg_command
    assert "anullsrc=channel_layout=stereo:sample_rate=48000:d=1" in ffmpeg_command
    assert ffmpeg_command[ffmpeg_command.index("-c:v") + 1] == "libsvtav1"
    assert ffmpeg_command[ffmpeg_command.index("-c:a") + 1] == "libopus"
    assert ffmpeg_command[ffmpeg_command.index("-video_track_timescale") + 1] == "15360"


def test_generate_cta_clip_wraps_portrait_text(monkeypatch, tmp_path):
    session = tmp_path / "session"
    media = session / "media"
    media.mkdir(parents=True)
    source_video = media / "video_source.mp4"
    source_video.write_bytes(b"source")
    commands: list[list[str]] = []

    def fake_run(cmd, check=False, capture_output=False, text=False, **kwargs):
        commands.append(cmd)
        if "-show_entries" in cmd:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps(
                    {
                        "streams": [
                            {
                                "codec_type": "video",
                                "codec_name": "h264",
                                "profile": "Main",
                                "level": 42,
                                "pix_fmt": "yuv420p",
                                "width": 608,
                                "height": 1080,
                                "avg_frame_rate": "24/1",
                            },
                            {
                                "codec_type": "audio",
                                "codec_name": "aac",
                                "sample_rate": "48000",
                                "channels": 1,
                                "channel_layout": "mono",
                            },
                        ]
                    }
                ),
                stderr="",
            )
        Path(cmd[-1]).write_bytes(b"cta")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(ffmpeg.subprocess, "run", fake_run)

    ffmpeg.generate_cta_clip(source_video, session)

    line_files = sorted((session / "tmp").glob("cta_follow_line*.txt"))
    assert [path.read_text(encoding="utf-8") for path in line_files] == [
        "如果你喜欢这些视频",
        "请多多关注、一键三连",
        "感谢！！",
    ]
    ffmpeg_command = commands[-1]
    filter_arg = ffmpeg_command[ffmpeg_command.index("-vf") + 1]
    font_sizes = [int(size) for size in re.findall(r"fontsize=(\d+)", filter_arg)]
    assert filter_arg.count("drawtext=") == 3
    assert len(font_sizes) == 3
    assert font_sizes[0] < max(36, round(1080 * 0.072))
    assert font_sizes[1] < max(36, round(1080 * 0.063))


def test_prepend_cta_clip_copy_concats_without_reencoding_input(monkeypatch, tmp_path):
    session = tmp_path / "session"
    media = session / "media"
    tmp_dir = session / "tmp"
    media.mkdir(parents=True)
    tmp_dir.mkdir(parents=True)
    final_video = media / "video_final.mp4"
    cta_clip = tmp_dir / "cta_follow_1s.mp4"
    final_video.write_bytes(b"final")
    cta_clip.write_bytes(b"cta")
    commands: list[list[str]] = []

    monkeypatch.setattr(ffmpeg, "generate_cta_clip", lambda reference, concat_session: cta_clip)
    monkeypatch.setattr(ffmpeg, "_probe_duration", lambda path: 10.0 if path == final_video else 12.0)

    def fake_run(cmd, check=False, cwd=None, **kwargs):
        commands.append(cmd)
        Path(cmd[-1]).write_bytes(b"concat")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(ffmpeg.subprocess, "run", fake_run)

    output = ffmpeg.prepend_cta_clip(final_video, session)

    assert output == media / "video_final_with_cta.mp4"
    assert output.read_bytes() == b"concat"
    assert commands[-1][commands[-1].index("-f") + 1] == "concat"
    assert commands[-1][commands[-1].index("-c") + 1] == "copy"
    assert "-filter_complex" not in commands[-1]
    assert "-c:v" not in commands[-1]
    concat_file = tmp_dir / "concat_video.txt"
    assert concat_file.read_text(encoding="utf-8").splitlines() == [
        f"file '{ffmpeg._concat_file_path(cta_clip.resolve())}'",
        f"file '{ffmpeg._concat_file_path(final_video.resolve())}'",
    ]


def test_prepend_cta_clip_rejects_output_as_input(tmp_path):
    session = tmp_path / "session"
    media = session / "media"
    media.mkdir(parents=True)
    output = media / "video_final_with_cta.mp4"
    output.write_bytes(b"old")

    with pytest.raises(ValueError, match="Cannot prepend CTA clip to its own output"):
        ffmpeg.prepend_cta_clip(output, session)

    assert output.read_bytes() == b"old"


def test_prepend_cta_clip_rejects_too_short_output(monkeypatch, tmp_path):
    session = tmp_path / "session"
    media = session / "media"
    tmp_dir = session / "tmp"
    media.mkdir(parents=True)
    tmp_dir.mkdir(parents=True)
    final_video = media / "video_final.mp4"
    cta_clip = tmp_dir / "cta_follow_1s.mp4"
    final_video.write_bytes(b"final")
    cta_clip.write_bytes(b"cta")

    monkeypatch.setattr(ffmpeg, "generate_cta_clip", lambda reference, concat_session: cta_clip)
    monkeypatch.setattr(ffmpeg, "_probe_duration", lambda path: 100.0 if path == final_video else 2.0)

    def fake_run(cmd, check=False, cwd=None, **kwargs):
        Path(cmd[-1]).write_bytes(b"bad")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(ffmpeg.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="Concatenated video is too short"):
        ffmpeg.prepend_cta_clip(final_video, session)

    assert not (media / "video_final_with_cta.mp4").exists()
    assert not (tmp_dir / "video_final_with_cta.tmp.mp4").exists()




def test_watermark_mask_patch_filter_uses_sample_region(monkeypatch, tmp_path):
    session = tmp_path / "session"
    session.mkdir()
    input_video = tmp_path / "video.mp4"
    input_video.write_bytes(b"video")
    commands: list[list[str]] = []

    monkeypatch.setattr(watermark_mask, "detect_bilibili_watermark_region", lambda video, session: watermark_mask.WatermarkRegion(656, 17, 179, 34, 656, 60))
    monkeypatch.setattr(watermark_mask, "_probe_duration", lambda video: 5.0)
    monkeypatch.setattr(watermark_mask, "_video_encoders", lambda: [["-c:v", "libx264", "-preset", "fast", "-crf", "23"]])

    def fake_run(cmd, check=False, timeout=None, cwd=None, **kwargs):
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(watermark_mask.subprocess, "run", fake_run)

    output, region = watermark_mask.mask_bilibili_watermark(input_video, session, "patch")

    assert output == session / "media" / "video_final_watermark_masked_patch.mp4"
    assert region.sample_y == 60
    filter_graph = commands[-1][commands[-1].index("-filter_complex") + 1]
    assert "crop=179:34:656:60" in filter_graph
    assert "overlay=656:17" in filter_graph


def test_watermark_mask_blur_filter_uses_boxblur_and_drawbox(monkeypatch, tmp_path):
    session = tmp_path / "session"
    session.mkdir()
    input_video = tmp_path / "video.mp4"
    input_video.write_bytes(b"video")
    commands: list[list[str]] = []

    monkeypatch.setattr(watermark_mask, "detect_bilibili_watermark_region", lambda video, session: watermark_mask.WatermarkRegion(656, 17, 179, 34, 656, 60))
    monkeypatch.setattr(watermark_mask, "_probe_duration", lambda video: 5.0)
    monkeypatch.setattr(watermark_mask, "_video_encoders", lambda: [["-c:v", "libx264", "-preset", "fast", "-crf", "23"]])

    def fake_run(cmd, check=False, timeout=None, cwd=None, **kwargs):
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(watermark_mask.subprocess, "run", fake_run)

    output, _ = watermark_mask.mask_bilibili_watermark(input_video, session, "blur")

    assert output == session / "media" / "video_final_watermark_masked_blur.mp4"
    filter_graph = commands[-1][commands[-1].index("-filter_complex") + 1]
    assert "boxblur=" in filter_graph
    assert "drawbox=x=656:y=17:w=179:h=34:color=black@0.45:t=fill" in filter_graph


def test_watermark_detection_falls_back_when_no_frame_can_be_read(monkeypatch, tmp_path):
    monkeypatch.setattr(watermark_mask, "probe_video_size", lambda video: (852, 480))
    monkeypatch.setattr(watermark_mask, "_probe_duration", lambda video: 5.0)
    monkeypatch.setattr(watermark_mask, "_read_rgb_frame", lambda video, seconds, width, height: None)

    region = watermark_mask.detect_bilibili_watermark_region(tmp_path / "video.mp4", tmp_path)

    assert region.x == 656
    assert region.y == 17
    assert region.width == 179
    assert region.height == 34
