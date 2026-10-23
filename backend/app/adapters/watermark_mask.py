from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from ..config import ffmpeg_binary
from .ffmpeg import _probe_duration, _video_encoders, probe_video_size

WATERMARK_MASK_MODES = {"patch", "blur"}
WATERMARK_METADATA_NAME = "watermark_mask.json"


@dataclass(frozen=True)
class WatermarkRegion:
    x: int
    y: int
    width: int
    height: int
    sample_x: int
    sample_y: int


def _fallback_region(width: int, height: int) -> WatermarkRegion:
    region_w = max(40, round(width * 0.21))
    region_h = max(20, round(height * 0.07))
    x = min(width - region_w, max(0, round(width * 0.77)))
    y = min(height - region_h, max(0, round(height * 0.035)))
    return _with_sample_region(WatermarkRegion(x, y, region_w, region_h, x, y), width, height)


def _with_sample_region(region: WatermarkRegion, video_width: int, video_height: int) -> WatermarkRegion:
    margin = max(4, region.height // 2)
    sample_x = region.x
    sample_y = region.y + region.height + margin
    if sample_y + region.height > video_height:
        sample_y = region.y
        sample_x = max(0, region.x - region.width - margin)
    if sample_x + region.width > video_width:
        sample_x = max(0, video_width - region.width)
    if sample_y + region.height > video_height:
        sample_y = max(0, video_height - region.height)
    return WatermarkRegion(region.x, region.y, region.width, region.height, sample_x, sample_y)


def _sample_seconds(duration: float | None) -> list[float]:
    if not duration or duration <= 1:
        return [0]
    points = [0.08, 0.22, 0.38, 0.58, 0.78]
    return [max(0, min(duration - 0.1, duration * point)) for point in points]


def _read_rgb_frame(video: Path, seconds: float, width: int, height: int) -> np.ndarray | None:
    result = subprocess.run(
        [
            ffmpeg_binary(),
            "-v", "error",
            "-ss", f"{seconds:.3f}",
            "-i", str(video),
            "-frames:v", "1",
            "-f", "rawvideo",
            "-pix_fmt", "rgb24",
            "-",
        ],
        capture_output=True,
        check=False,
    )
    expected = width * height * 3
    if result.returncode != 0 or len(result.stdout) < expected:
        return None
    return np.frombuffer(result.stdout[:expected], dtype=np.uint8).reshape((height, width, 3))


def detect_bilibili_watermark_region(video: Path, session: Path) -> WatermarkRegion:
    size = probe_video_size(video)
    if size is None:
        raise RuntimeError(f"Cannot probe video size: {video}")
    width, height = size
    fallback = _fallback_region(width, height)
    duration = _probe_duration(video)

    x0 = round(width * 0.55)
    x1 = round(width * 0.98)
    y0 = round(height * 0.02)
    y1 = round(height * 0.45)
    if x1 <= x0 or y1 <= y0:
        return fallback

    score_sum = np.zeros((y1 - y0, x1 - x0), dtype=np.float32)
    frames = 0
    for seconds in _sample_seconds(duration):
        frame = _read_rgb_frame(video, seconds, width, height)
        if frame is None:
            continue
        roi = frame[y0:y1, x0:x1].astype(np.float32)
        maxc = roi.max(axis=2)
        minc = roi.min(axis=2)
        brightness = roi.mean(axis=2)
        saturation = maxc - minc
        score = (brightness > 170) & (saturation < 42)
        score_sum += score.astype(np.float32)
        frames += 1

    if frames == 0:
        return fallback

    mask = score_sum >= max(2, min(frames, 3))
    ys, xs = np.where(mask)
    min_pixels = max(20, int(width * height * 0.00003))
    if len(xs) < min_pixels:
        return fallback

    x_min = int(xs.min()) + x0
    x_max = int(xs.max()) + x0
    y_min = int(ys.min()) + y0
    y_max = int(ys.max()) + y0

    pad_x = max(12, round(width * 0.012))
    pad_y = max(8, round(height * 0.012))
    region_w = max(round(width * 0.12), min(round(width * 0.30), x_max - x_min + 1 + pad_x * 2))
    region_h = max(round(height * 0.045), min(round(height * 0.12), y_max - y_min + 1 + pad_y * 2))
    x = max(0, min(width - region_w, x_min - pad_x))
    y = max(0, min(height - region_h, y_min - pad_y))
    return _with_sample_region(WatermarkRegion(x, y, region_w, region_h, x, y), width, height)


def _source_video_for_mask(input_video: Path, session: Path) -> Path:
    metadata = session / "metadata" / WATERMARK_METADATA_NAME
    if not metadata.exists():
        return input_video
    try:
        data = json.loads(metadata.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return input_video
    source = data.get("source_final_video_path")
    if not source:
        return input_video
    source_path = Path(source)
    return source_path if source_path.exists() else input_video


def _blur_filter(region: WatermarkRegion) -> str:
    luma = min(16, max(2, region.height // 2 - 1))
    chroma = min(8, max(2, region.height // 4 - 1))
    return (
        f"[0:v]split[base][patch];"
        f"[patch]crop={region.width}:{region.height}:{region.x}:{region.y},"
        f"boxblur={luma}:2:{chroma}:2[blur];"
        f"[base][blur]overlay={region.x}:{region.y},"
        f"drawbox=x={region.x}:y={region.y}:w={region.width}:h={region.height}:color=black@0.45:t=fill[outv]"
    )


def _patch_filter(region: WatermarkRegion) -> str:
    return (
        f"[0:v]split[base][patch];"
        f"[patch]crop={region.width}:{region.height}:{region.sample_x}:{region.sample_y}[clean];"
        f"[base][clean]overlay={region.x}:{region.y}[outv]"
    )


def _filter_for_mode(mode: str, region: WatermarkRegion) -> str:
    if mode == "patch":
        return _patch_filter(region)
    if mode == "blur":
        return _blur_filter(region)
    raise ValueError("mode must be one of: patch, blur")


def _output_for_mode(session: Path, mode: str) -> Path:
    return session / "media" / f"video_final_watermark_masked_{mode}.mp4"


def mask_bilibili_watermark(video: Path, session: Path, mode: str) -> tuple[Path, WatermarkRegion]:
    if mode not in WATERMARK_MASK_MODES:
        raise ValueError("mode must be one of: patch, blur")
    source_video = _source_video_for_mask(video, session).resolve()
    if not source_video.exists():
        raise FileNotFoundError(f"Video not found: {source_video}")

    session_dir = session.resolve()
    output = _output_for_mode(session, mode)
    output.parent.mkdir(parents=True, exist_ok=True)
    output_resolved = output.resolve()
    if output_resolved.exists():
        output_resolved.unlink()

    region = detect_bilibili_watermark_region(source_video, session)
    filter_graph = _filter_for_mode(mode, region)
    duration = _probe_duration(source_video)
    timeout = max(600, int((duration or 1800) * 3))
    last_error: subprocess.SubprocessError | None = None
    for index, video_codec in enumerate(_video_encoders()):
        try:
            subprocess.run(
                [
                    ffmpeg_binary(),
                    "-y",
                    "-i", str(source_video),
                    "-filter_complex", filter_graph,
                    "-map", "[outv]",
                    "-map", "0:a?",
                    *video_codec,
                    "-c:a", "copy",
                    "-movflags", "+faststart",
                    str(output_resolved),
                ],
                check=True,
                timeout=timeout,
                cwd=session_dir,
            )
            _write_metadata(session, source_video, output_resolved, mode, region)
            return output, region
        except subprocess.SubprocessError as error:
            last_error = error
            if output_resolved.exists():
                output_resolved.unlink()
            if index + 1 >= len(_video_encoders()):
                break
    assert last_error is not None
    raise last_error


def _write_metadata(session: Path, source: Path, output: Path, mode: str, region: WatermarkRegion) -> None:
    metadata_dir = session / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "source_final_video_path": str(source),
        "output_video_path": str(output),
        "mode": mode,
        "region": asdict(region),
    }
    (metadata_dir / WATERMARK_METADATA_NAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
