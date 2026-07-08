from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Callable

import soundfile as sf
from pydub import AudioSegment

from ..config import MODEL_CACHE_DIR

logger = logging.getLogger(__name__)

_MODEL = None

_PROMPT_CACHE_GENERATION_DEFAULTS = {
    "min_len": 2,
    "max_len": 4096,
    "retry_badcase": True,
    "retry_badcase_max_times": 3,
    "retry_badcase_ratio_threshold": 6.0,
}


def _model_path() -> Path:
    configured_dir = os.getenv("VOXCPM_MODEL_DIR")
    if configured_dir:
        return Path(configured_dir).expanduser()

    model_id = os.getenv("VOXCPM_MODEL", "OpenBMB/VoxCPM2")
    local_dir = MODEL_CACHE_DIR / model_id.replace("/", "__")
    from modelscope import snapshot_download

    downloaded = snapshot_download(model_id, local_dir=str(local_dir))
    return Path(downloaded)


def _load_model():
    global _MODEL
    if _MODEL is None:
        from voxcpm import VoxCPM

        _MODEL = VoxCPM.from_pretrained(
            str(_model_path()),
            load_denoiser=os.getenv("VOXCPM_LOAD_DENOISER", "false").lower() == "true",
        )
    return _MODEL


def unload_model() -> None:
    """Drop the resident VoxCPM model so its VRAM can be reclaimed."""
    global _MODEL
    _MODEL = None


def _first_reference(files: list[Path], min_ms: int) -> Path | None:
    for path in files:
        if len(AudioSegment.from_file(path)) >= min_ms:
            return path
    if files:
        return files[0]
    return None


def _speaker(item: dict) -> str:
    speaker = item.get("speaker")
    if speaker is None:
        return "1"
    speaker = str(speaker).strip()
    return speaker or "1"


def _fallback_references(vocals_dir: Path, items: list[dict], min_ms: int) -> tuple[dict[str, Path], Path]:
    files = sorted(vocals_dir.glob("*.wav"))
    if not files:
        raise FileNotFoundError("No vocal segments were generated for VoxCPM references.")

    global_fallback = _first_reference(files, min_ms) or files[0]
    speaker_files: dict[str, list[Path]] = {}
    for index, item in enumerate(items, start=1):
        reference = vocals_dir / f"{index:04d}.wav"
        if reference.exists():
            speaker_files.setdefault(_speaker(item), []).append(reference)

    fallbacks: dict[str, Path] = {}
    for speaker, refs in speaker_files.items():
        fallback = _first_reference(refs, min_ms)
        if fallback is not None:
            fallbacks[speaker] = fallback

    return fallbacks, global_fallback


def _tts_text(item: dict) -> str:
    text = item.get("dst") or item.get("zh", "")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("target text must be a non-empty string")
    text = text.replace("\n", " ")
    return re.sub(r"\s+", " ", text)


def generate_tts(
    translation_file: Path,
    vocals_dir: Path,
    session: Path,
    progress_callback: Callable[[int, str], None] | None = None,
) -> Path:
    output_dir = session / "segments" / "tts"
    output_dir.mkdir(parents=True, exist_ok=True)
    data = json.loads(translation_file.read_text(encoding="utf-8"))
    items = data["translation"]
    total = len(items)
    if total == 0:
        if progress_callback:
            progress_callback(100, "No TTS clips to generate")
        return output_dir

    model = _load_model()
    min_reference_ms = int(os.getenv("VOXCPM_MIN_REFERENCE_MS", "1200"))
    fallback_references, global_fallback = _fallback_references(vocals_dir, items, min_reference_ms)
    cfg_value = float(os.getenv("VOXCPM_CFG_VALUE", "2.0"))
    inference_timesteps = int(os.getenv("VOXCPM_INFERENCE_TIMESTEPS", "10"))

    # When VOXCPM_USE_SPEAKER_CACHE=true, pre-build one prompt cache per speaker and route
    # ALL sentences through generate_with_prompt_cache, skipping per-sentence reference encoding.
    # Trade-off: slightly less per-sentence voice variation; suitable for single-speaker videos.
    use_speaker_cache = os.getenv("VOXCPM_USE_SPEAKER_CACHE", "false").lower() == "true"

    fallback_caches: dict[str, object] = {}
    global_cache: object = None

    if use_speaker_cache:
        speakers = {_speaker(it) for it in items}
        logger.info("VOXCPM_USE_SPEAKER_CACHE: pre-building prompt cache for %d speaker(s)...", len(speakers))
        for spk in speakers:
            ref = fallback_references.get(spk, global_fallback)
            fallback_caches[spk] = model.tts_model.build_prompt_cache(reference_wav_path=str(ref))
        global_cache = model.tts_model.build_prompt_cache(reference_wav_path=str(global_fallback))
        logger.info("VOXCPM_USE_SPEAKER_CACHE: prompt caches ready")

    cache_path_count = 0
    direct_path_count = 0
    total_tts_time = 0.0

    for index, item in enumerate(items, start=1):
        output_file = output_dir / f"{index:04d}.wav"
        if not output_file.exists():
            reference = vocals_dir / f"{index:04d}.wav"
            text = _tts_text(item)
            t_start = time.perf_counter()
            needs_cache = (
                use_speaker_cache
                or not reference.exists()
                or len(AudioSegment.from_file(reference)) < min_reference_ms
            )
            if needs_cache:
                cache_path_count += 1
                speaker = _speaker(item)
                if speaker not in fallback_caches:
                    ref = fallback_references.get(speaker, global_fallback)
                    fallback_caches[speaker] = model.tts_model.build_prompt_cache(
                        reference_wav_path=str(ref)
                    )
                if global_cache is None:
                    global_cache = model.tts_model.build_prompt_cache(
                        reference_wav_path=str(global_fallback)
                    )
                cache = fallback_caches.get(speaker) or global_cache
                result = model.tts_model.generate_with_prompt_cache(
                    target_text=text,
                    prompt_cache=cache,
                    cfg_value=cfg_value,
                    inference_timesteps=inference_timesteps,
                    **_PROMPT_CACHE_GENERATION_DEFAULTS,
                )
                wav_tensor, _, _ = result
                wav = wav_tensor.squeeze(0).cpu().numpy()
            else:
                direct_path_count += 1
                wav = model.generate(
                    text=text,
                    reference_wav_path=str(reference),
                    cfg_value=cfg_value,
                    inference_timesteps=inference_timesteps,
                )
            elapsed = time.perf_counter() - t_start
            total_tts_time += elapsed
            sf.write(output_file, wav, model.tts_model.sample_rate)
        if progress_callback:
            progress = round(index / total * 100)
            progress_callback(progress, f"Prepared {index}/{total} TTS clips")

    avg_ms = (total_tts_time / max(total, 1)) * 1000
    logger.info(
        "TTS summary: %d items | direct=%d cache=%d | speaker_cache=%s | total=%.1fs avg=%dms/item",
        total, direct_path_count, cache_path_count,
        "on" if use_speaker_cache else "off",
        total_tts_time, round(avg_ms),
    )
    if cache_path_count > 0 and not use_speaker_cache:
        logger.info(
            "TTS reference fallback rate: %d/%d (%.1f%%) — retry_badcase_max_times=%d",
            cache_path_count, total,
            cache_path_count / max(total, 1) * 100,
            _PROMPT_CACHE_GENERATION_DEFAULTS["retry_badcase_max_times"],
        )

    return output_dir
