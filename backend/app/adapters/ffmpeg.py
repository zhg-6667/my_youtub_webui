from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from ..config import ffmpeg_binary, ffprobe_binary

SUBTITLE_PUNCTUATION = {"，", ",", "；", ";", "：", ":", "。", "?", "？", "!", "！", "、"}
SUBTITLE_PROTECTED_PAIRS = {"《": "》", "（": "）", "【": "】", "「": "」", "『": "』"}
SUBTITLE_CLOSING_QUOTES = {'"', "'", "」", "』", "》", "）", "】", "\u201d", "\u2019", "]"}
SUBTITLE_MIN_FRAGMENT_LEN = 5
SUBTITLE_MIN_DURATION_MS = 200
SUBTITLE_TAIL_BUFFER_MS = 100
SUBTITLE_DURATION_FLOOR_MS = 600


SUBTITLE_FONTS = {
    "zh": "Noto Sans CJK SC",
    "en": "Arial",
}

SUBTITLE_FONT_SIZES = {
    "zh": {"portrait": 12, "landscape": 24},
    "en": {"portrait": 9, "landscape": 18},
}


def _subtitle_style(font: str, size: int, margin_v: int) -> str:
    return (
        f"FontName={font},"
        f"FontSize={size},"
        "PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,"
        "BorderStyle=1,"
        "Outline=2,"
        "Alignment=2,"
        f"MarginV={margin_v}"
    )


def _srt_time(ms: int) -> str:
    hours = ms // 3_600_000
    ms -= hours * 3_600_000
    minutes = ms // 60_000
    ms -= minutes * 60_000
    seconds = ms // 1000
    millis = ms - seconds * 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _split_protected(text: str) -> list[str]:
    segments: list[str] = []
    buf: list[str] = []
    inside = None
    for ch in text:
        if inside is None and ch in SUBTITLE_PROTECTED_PAIRS:
            inside = SUBTITLE_PROTECTED_PAIRS[ch]
            buf.append(ch)
            continue
        if inside is not None and ch == inside:
            inside = None
            buf.append(ch)
            continue
        if inside is None and ch in SUBTITLE_PUNCTUATION:
            chunk = "".join(buf).strip()
            if chunk:
                segments.append(chunk)
            buf.clear()
            continue
        buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        segments.append(tail)
    return segments


def _attach_closing_quotes(segments: list[str]) -> list[str]:
    fixed: list[str] = []
    for seg in segments:
        if seg and seg[0] in SUBTITLE_CLOSING_QUOTES and fixed:
            fixed[-1] = f"{fixed[-1]}{seg}".strip()
            continue
        fixed.append(seg.strip())
    return fixed


def _merge_short_fragments(segments: list[str]) -> list[str]:
    merged: list[str] = []
    i = 0
    while i < len(segments):
        cur = segments[i]
        if len(cur.strip()) < SUBTITLE_MIN_FRAGMENT_LEN and i + 1 < len(segments):
            segments[i + 1] = f"{cur}{segments[i + 1]}".strip()
            i += 1
            continue
        merged.append(cur)
        i += 1
    return merged


def _strip_trailing_punct(segments: list[str]) -> list[str]:
    cleaned: list[str] = []
    for item in segments:
        text = item.strip()
        if not text:
            continue
        if text.endswith(("，", ",", "。")):
            text = text[:-1]
        cleaned.append(re.sub(r"\s+", " ", text).strip())
    return cleaned


def split_subtitle_text(text: str) -> list[str]:
    original = (text or "").strip()
    if not original:
        return []
    segments = _split_protected(original)
    if not segments:
        return [original]
    segments = _attach_closing_quotes(segments)
    segments = _merge_short_fragments(segments)
    cleaned = _strip_trailing_punct(segments)
    return cleaned or [original]


def _allocate_durations(fragments: list[str], total_duration: int) -> list[int]:
    if len(fragments) == 1:
        return [total_duration]
    weights = [max(1, len(f.replace(" ", ""))) for f in fragments]
    total_weight = sum(weights)
    durations: list[int] = []
    allocated = 0
    for i, weight in enumerate(weights[:-1]):
        share = round(total_duration * weight / total_weight)
        if total_duration >= SUBTITLE_DURATION_FLOOR_MS:
            ceiling = total_duration - allocated - SUBTITLE_TAIL_BUFFER_MS
            share = max(SUBTITLE_MIN_DURATION_MS, min(share, ceiling))
        else:
            share = max(int(SUBTITLE_MIN_DURATION_MS / 2), share)
        durations.append(share)
        allocated += share
    durations.append(max(SUBTITLE_TAIL_BUFFER_MS, total_duration - allocated))
    return durations


def _segment_times(item: dict) -> tuple[int, int]:
    start = int(item.get("actual_start_time", item["start_time"]))
    end = int(item.get("actual_end_time", item["end_time"]))
    return start, end


def _dst_lang(translation: list[dict]) -> str:
    for item in translation:
        lang = item.get("dst_lang")
        if lang:
            return lang
    return "zh"


def _dst_text(item: dict) -> str:
    return item.get("dst") or item.get("zh") or ""


def write_srt(translation_file: Path, session: Path) -> Path:
    data = json.loads(translation_file.read_text(encoding="utf-8"))
    translation = data["translation"]
    dst_lang = _dst_lang(translation)
    output_file = session / "metadata" / f"subtitles.{dst_lang}.srt"
    lines: list[str] = []
    idx = 1
    for item in translation:
        start, end = _segment_times(item)
        if end <= start:
            continue
        fragments = split_subtitle_text(_dst_text(item))
        if not fragments:
            continue
        cursor = start
        for fragment, duration in zip(fragments, _allocate_durations(fragments, end - start)):
            lines.extend([str(idx), f"{_srt_time(cursor)} --> {_srt_time(cursor + duration)}", fragment, ""])
            cursor += duration
            idx += 1
    output_file.write_text("\n".join(lines), encoding="utf-8")
    return output_file


def probe_video_size(video_file: Path) -> tuple[int, int] | None:
    result = subprocess.run(
        [
            ffprobe_binary(),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0",
            str(video_file),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None

    lines = result.stdout.strip().splitlines()
    if not lines:
        return None
    parts = lines[0].split(",", maxsplit=1)
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def get_video_orientation(video_file: Path) -> str:
    size = probe_video_size(video_file)
    if size is None:
        return "landscape"
    width, height = size
    return "portrait" if height > width else "landscape"


def subtitle_style_for_orientation(orientation: str, font: str, lang: str = "zh") -> str:
    sizes = SUBTITLE_FONT_SIZES.get(lang, SUBTITLE_FONT_SIZES["zh"])
    margin_v = 70 if orientation == "portrait" else 5
    return _subtitle_style(font, size=sizes[orientation], margin_v=margin_v)


def _subtitle_filter_path(subtitle_file: Path, session: Path) -> str:
    try:
        return subtitle_file.resolve().relative_to(session.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError("Subtitle file must be inside the session directory.") from exc


def subtitle_filter(video_file: Path, subtitle_file: Path, session: Path) -> str:
    lang = subtitle_file.stem.rsplit(".", 1)[-1]
    font = SUBTITLE_FONTS.get(lang, "Arial")
    style = subtitle_style_for_orientation(get_video_orientation(video_file), font, lang)
    sub_path = _subtitle_filter_path(subtitle_file, session)
    return f"subtitles=filename='{sub_path}':force_style='{style}'"


def _nvenc_available() -> bool:
    try:
        result = subprocess.run(
            [ffmpeg_binary(), "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return "h264_nvenc" in result.stdout


def _video_encoders() -> list[list[str]]:
    nvenc = ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "23"]
    libx264 = ["-c:v", "libx264", "-preset", "fast", "-crf", "23"]
    choice = os.getenv("VIDEO_ENCODER", "auto").strip().lower()
    if choice == "libx264":
        return [libx264]
    if choice in ("nvenc", "h264_nvenc"):
        return [nvenc]
    # auto: prefer NVENC when ffmpeg supports it, fall back to libx264 on failure.
    if _nvenc_available():
        return [nvenc, libx264]
    return [libx264]


def _probe_duration(video_file: Path) -> float | None:
    """Return video duration in seconds, or None on failure."""
    result = subprocess.run(
        [
            ffprobe_binary(),
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            str(video_file),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip())
    except (ValueError, TypeError):
        return None


CTA_CLIP_LINES = (
    "如果你喜欢这些视频",
    "请多多关注、一键三连，感谢！！",
)
CTA_CLIP_SECONDS = 2


def _probe_concat_reference(video_file: Path) -> dict[str, str | int]:
    result = subprocess.run(
        [
            ffprobe_binary(),
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name,profile,level,pix_fmt,width,height,avg_frame_rate,time_base,sample_rate,channels,channel_layout",
            "-of",
            "json",
            str(video_file),
        ],
        capture_output=True,
        text=True,
    )
    settings: dict[str, str | int] = {
        "width": 1920,
        "height": 1080,
        "fps": "60/1",
        "video_codec": "h264",
        "profile": "Main",
        "level": 42,
        "pix_fmt": "yuv420p",
        "time_base": "1/15360",
        "audio_codec": "aac",
        "sample_rate": 48000,
        "channels": 1,
        "channel_layout": "mono",
    }
    if result.returncode != 0:
        return settings
    try:
        streams = json.loads(result.stdout).get("streams") or []
    except json.JSONDecodeError:
        return settings
    video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
    audio_stream = next((stream for stream in streams if stream.get("codec_type") == "audio"), {})
    if video_stream.get("codec_name"):
        settings["video_codec"] = str(video_stream["codec_name"])
    if video_stream.get("profile"):
        settings["profile"] = str(video_stream["profile"])
    if video_stream.get("level"):
        settings["level"] = int(video_stream["level"])
    if video_stream.get("pix_fmt"):
        settings["pix_fmt"] = str(video_stream["pix_fmt"])
    if video_stream.get("width") and video_stream.get("height"):
        settings["width"] = int(video_stream["width"])
        settings["height"] = int(video_stream["height"])
    fps = str(video_stream.get("avg_frame_rate") or "60/1")
    if fps and fps != "0/0":
        settings["fps"] = fps
    if video_stream.get("time_base"):
        settings["time_base"] = str(video_stream["time_base"])
    if audio_stream.get("codec_name"):
        settings["audio_codec"] = str(audio_stream["codec_name"])
    if audio_stream.get("sample_rate"):
        settings["sample_rate"] = int(audio_stream["sample_rate"])
    if audio_stream.get("channels"):
        settings["channels"] = int(audio_stream["channels"])
    if audio_stream.get("channel_layout"):
        settings["channel_layout"] = str(audio_stream["channel_layout"])
    return settings


def _filter_path(path: Path) -> str:
    return path.resolve().as_posix().replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _drawtext_font_option() -> str:
    configured = os.getenv("CTA_FONT_FILE", "").strip()
    candidates = [configured] if configured else []
    candidates.extend(
        [
            "C:/Windows/Fonts/msyhbd.ttc",
            "C:/Windows/Fonts/msyh.ttc",
        ]
    )
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return f"fontfile='{_filter_path(Path(candidate))}'"
    return "font='Noto Sans CJK SC'"


def _channel_layout(channels: int) -> str:
    if channels == 1:
        return "mono"
    if channels == 2:
        return "stereo"
    return "mono"


def _concat_file_path(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "\\'")


def _x264_profile(profile: str) -> str | None:
    normalized = profile.strip().lower()
    if "baseline" in normalized:
        return "baseline"
    if "main" in normalized:
        return "main"
    if "high" in normalized:
        return "high"
    return None


def _ffmpeg_level(level: int | str) -> str | None:
    try:
        value = int(level)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return f"{value // 10}.{value % 10}"


def _cta_video_encoder_args(settings: dict[str, str | int]) -> list[str]:
    codec = str(settings.get("video_codec") or "h264").lower()
    pix_fmt = str(settings.get("pix_fmt") or "yuv420p")
    if pix_fmt.startswith("yuvj"):
        pix_fmt = pix_fmt.replace("yuvj", "yuv", 1)
    if pix_fmt not in {"yuv420p", "yuv422p", "yuv444p"}:
        pix_fmt = "yuv420p"

    if codec in {"h264", "avc1"}:
        args = ["-c:v", "libx264", "-preset", "fast", "-crf", "23", "-pix_fmt", pix_fmt]
        profile = _x264_profile(str(settings.get("profile") or ""))
        if profile:
            args.extend(["-profile:v", profile])
        level = _ffmpeg_level(settings.get("level") or 0)
        if level:
            args.extend(["-level:v", level])
        return args

    if codec in {"hevc", "h265"}:
        return ["-c:v", "libx265", "-preset", "fast", "-crf", "26", "-pix_fmt", pix_fmt]

    if codec == "av1":
        return ["-c:v", "libsvtav1", "-preset", "8", "-crf", "35", "-pix_fmt", pix_fmt]

    return ["-c:v", "libx264", "-preset", "fast", "-crf", "23", "-pix_fmt", "yuv420p"]


def _cta_audio_encoder_args(settings: dict[str, str | int]) -> list[str]:
    codec = str(settings.get("audio_codec") or "aac").lower()
    sample_rate = str(settings.get("sample_rate") or 48000)
    channels = str(settings.get("channels") or 1)
    if codec == "opus":
        return ["-c:a", "libopus", "-ar", sample_rate, "-ac", channels, "-b:a", "128k"]
    if codec == "mp3":
        return ["-c:a", "libmp3lame", "-ar", sample_rate, "-ac", channels]
    return ["-c:a", "aac", "-ar", sample_rate, "-ac", channels]


def _video_track_timescale(time_base: str | int) -> str | None:
    raw = str(time_base)
    if "/" not in raw:
        return None
    numerator, denominator = raw.split("/", 1)
    try:
        if int(numerator) != 1:
            return None
        value = int(denominator)
    except ValueError:
        return None
    if value <= 0:
        return None
    return str(value)


def generate_cta_clip(reference_video: Path, session: Path) -> Path:
    media_dir = session / "media"
    tmp_dir = session / "tmp"
    media_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    clip = media_dir / "cta_follow_2s.mp4"
    if clip.exists():
        clip.unlink()

    settings = _probe_concat_reference(reference_video)
    width = int(settings["width"])
    height = int(settings["height"])
    fps = str(settings["fps"])
    sample_rate = int(settings["sample_rate"])
    channels = int(settings["channels"])
    channel_layout = str(settings.get("channel_layout") or _channel_layout(channels))
    line1 = tmp_dir / "cta_follow_line1.txt"
    line2 = tmp_dir / "cta_follow_line2.txt"
    line1.write_text(CTA_CLIP_LINES[0], encoding="utf-8")
    line2.write_text(CTA_CLIP_LINES[1], encoding="utf-8")

    font = _drawtext_font_option()
    first_size = max(36, round(height * 0.072))
    second_size = max(32, round(height * 0.063))
    first_y = round(height * 0.394)
    second_y = round(height * 0.509)
    vf = (
        f"drawtext={font}:textfile='{_filter_path(line1)}':fontsize={first_size}:"
        f"fontcolor=white:x=(w-text_w)/2:y={first_y},"
        f"drawtext={font}:textfile='{_filter_path(line2)}':fontsize={second_size}:"
        f"fontcolor=white:x=(w-text_w)/2:y={second_y},"
        "format=yuv420p"
    )
    subprocess.run(
        [
            ffmpeg_binary(),
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x121418:s={width}x{height}:r={fps}:d={CTA_CLIP_SECONDS}",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=channel_layout={channel_layout}:sample_rate={sample_rate}:d={CTA_CLIP_SECONDS}",
            "-vf",
            vf,
            *_cta_video_encoder_args(settings),
            *_cta_audio_encoder_args(settings),
            "-t",
            str(CTA_CLIP_SECONDS),
            *(
                ["-video_track_timescale", timescale]
                if (timescale := _video_track_timescale(settings.get("time_base") or "")) is not None
                else []
            ),
            "-movflags",
            "+faststart",
            str(clip.resolve()),
        ],
        check=True,
    )
    return clip


def prepend_cta_clip(input_video: Path, session: Path) -> Path:
    media_dir = session / "media"
    tmp_dir = session / "tmp"
    media_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    output_video = media_dir / "video_final_with_cta.mp4"
    if input_video.resolve() == output_video.resolve():
        raise ValueError("Cannot prepend CTA clip to its own output; use video_final.mp4 or video_source.mp4 as input.")
    if output_video.exists():
        return output_video

    cta_clip = generate_cta_clip(input_video, session)
    tmp_output = tmp_dir / "video_final_with_cta.tmp.mp4"
    if tmp_output.exists():
        tmp_output.unlink()

    input_duration = _probe_duration(input_video)
    timeout = max(600, int(((input_duration or 1800) + CTA_CLIP_SECONDS) * 3))
    session_dir = session.resolve()
    cta_input = cta_clip.resolve()
    video_input = input_video.resolve()
    tmp_resolved = tmp_output.resolve()
    concat_file = tmp_dir / "concat_video.txt"
    concat_file.write_text(
        f"file '{_concat_file_path(cta_input)}'\nfile '{_concat_file_path(video_input)}'\n",
        encoding="utf-8",
    )
    subprocess.run(
        [
            ffmpeg_binary(),
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file.resolve()),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(tmp_resolved),
        ],
        check=True,
        timeout=timeout,
        cwd=session_dir,
    )

    output_duration = _probe_duration(tmp_output)
    if output_duration is None:
        tmp_output.unlink(missing_ok=True)
        raise RuntimeError(f"Cannot probe concatenated video duration: {tmp_output}")
    if input_duration is not None and output_duration <= input_duration + 0.5:
        tmp_output.unlink(missing_ok=True)
        raise RuntimeError(
            f"Concatenated video is too short: input={input_duration:.3f}s, output={output_duration:.3f}s"
        )
    tmp_output.replace(output_video)
    return output_video

def merge_video(video_file: Path, dubbing_file: Path, bgm_file: Path, timings_file: Path, session: Path) -> Path:
    tmp_dir = session / "tmp"
    media_dir = session / "media"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    media_dir.mkdir(parents=True, exist_ok=True)
    final_video = media_dir / "video_final.mp4"
    if final_video.exists():
        return final_video

    session_dir = session.resolve()
    video_input = video_file.resolve()
    dubbing_input = dubbing_file.resolve()
    bgm_input = bgm_file.resolve()
    subtitles = write_srt(timings_file, session)
    mixed_audio = tmp_dir / "audio_mixed.m4a"
    mixed_audio_output = mixed_audio.resolve()
    final_video_output = final_video.resolve()
    subprocess.run(
        [
            ffmpeg_binary(),
            "-y",
            "-i",
            str(dubbing_input),
            "-i",
            str(bgm_input),
            "-filter_complex",
            "[0:a]volume=1.0[a0];[1:a]volume=0.30[a1];[a0][a1]amix=inputs=2:duration=longest:normalize=0[aout]",
            "-map",
            "[aout]",
            "-c:a",
            "aac",
            str(mixed_audio_output),
        ],
        check=True,
    )
    vf = subtitle_filter(video_input, subtitles, session_dir)
    encoders = _video_encoders()
    duration = _probe_duration(video_input)
    timeout = max(600, int((duration or 1800) * 3))
    last_error: subprocess.SubprocessError | None = None
    for index, video_codec in enumerate(encoders):
        try:
            subprocess.run(
                [
                    ffmpeg_binary(),
                    "-y",
                    "-i",
                    str(video_input),
                    "-i",
                    str(mixed_audio_output),
                    "-vf",
                    vf,
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a:0",
                    *video_codec,
                    "-c:a",
                    "aac",
                    "-movflags",
                    "+faststart",
                    "-shortest",
                    str(final_video_output),
                ],
                check=True,
                timeout=timeout,
                cwd=session_dir,
            )
            return final_video
        except subprocess.SubprocessError as error:
            last_error = error
            detail = (
                f"timed out after {timeout}s"
                if isinstance(error, subprocess.TimeoutExpired)
                else getattr(error, "stderr", "") or str(error)
            )
            if index + 1 < len(encoders):
                print(
                    f"[ffmpeg] video encoder {video_codec[1]} failed ({detail}), "
                    f"falling back to {encoders[index + 1][1]}",
                    file=sys.stderr,
                )
    assert last_error is not None
    raise last_error


def _keep_intervals(cuts: list[dict], total_duration: float) -> list[tuple[float, float]]:
    """Invert cut intervals into keep intervals, clamped to [0, total_duration]."""
    if not cuts:
        return [(0, total_duration)]

    normalized: list[tuple[float, float]] = []
    for c in cuts:
        start = float(max(0, c.get("start", 0)))
        end = float(min(total_duration, c.get("end", total_duration)))
        if end > start:
            normalized.append((start, end))
    normalized.sort(key=lambda x: x[0])

    merged: list[tuple[float, float]] = []
    for s, e in normalized:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))

    keep: list[tuple[float, float]] = []
    cursor = 0.0
    for s, e in merged:
        if s > cursor:
            keep.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < total_duration:
        keep.append((cursor, total_duration))
    return keep


def trim_video(input_video: Path, cut_intervals: list[dict], session: Path) -> Path:
    """Cut intervals from the final video, output as a separate trimmed file.

    cut_intervals: [{"start": 120.0, "end": 180.0}, ...] in seconds.
    Returns path to video_final_trimmed.mp4 (or the original if nothing to cut).
    """
    media_dir = session / "media"
    output_video = media_dir / "video_final_trimmed.mp4"
    if output_video.exists():
        return output_video

    if not cut_intervals:
        return input_video

    duration = _probe_duration(input_video)
    if duration is None:
        raise RuntimeError(f"Cannot probe duration of {input_video}")

    keep = _keep_intervals(cut_intervals, duration)
    if not keep:
        return input_video
    if len(keep) == 1 and keep[0] == (0, duration):
        return input_video

    session_dir = session.resolve()
    input_resolved = input_video.resolve()
    output_resolved = output_video.resolve()

    trim_filters: list[str] = []
    concat_inputs: list[str] = []
    for i, (start, end) in enumerate(keep):
        trim_filters.append(f"[0:v]trim=start={start}:end={end},setpts=PTS-STARTPTS[v{i}];")
        trim_filters.append(f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[a{i}];")
        concat_inputs.append(f"[v{i}][a{i}]")

    n = len(keep)
    filter_graph = "".join(trim_filters) + f"{''.join(concat_inputs)}concat=n={n}:v=1:a=1[outv][outa]"

    encoders = _video_encoders()
    timeout = max(600, int(duration * 3))
    last_error: subprocess.SubprocessError | None = None
    for index, video_codec in enumerate(encoders):
        try:
            subprocess.run(
                [
                    ffmpeg_binary(),
                    "-y",
                    "-i",
                    str(input_resolved),
                    "-filter_complex",
                    filter_graph,
                    "-map",
                    "[outv]",
                    "-map",
                    "[outa]",
                    *video_codec,
                    "-c:a",
                    "aac",
                    "-movflags",
                    "+faststart",
                    str(output_resolved),
                ],
                check=True,
                timeout=timeout,
                cwd=session_dir,
            )
            return output_video
        except subprocess.SubprocessError as error:
            last_error = error
            detail = (
                f"timed out after {timeout}s"
                if isinstance(error, subprocess.TimeoutExpired)
                else getattr(error, "stderr", "") or str(error)
            )
            if index + 1 < len(encoders):
                print(
                    f"[ffmpeg] trim_video encoder {video_codec[1]} failed ({detail}), "
                    f"falling back to {encoders[index + 1][1]}",
                    file=sys.stderr,
                )
    assert last_error is not None
    raise last_error
