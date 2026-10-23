from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import soundfile as sf

from backend.app.adapters import audio


def _write_tone(path: Path, duration_sec: float, sample_rate: int = 16_000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    samples = np.linspace(0, duration_sec, int(duration_sec * sample_rate), endpoint=False)
    sf.write(str(path), 0.2 * np.sin(2 * np.pi * 440 * samples), sample_rate)


def test_trim_tts_file_removes_edge_and_long_inner_silence(tmp_path):
    sample_rate = 16_000
    silence = np.zeros(int(0.35 * sample_rate), dtype=np.float32)
    long_pause = np.zeros(int(0.60 * sample_rate), dtype=np.float32)
    tail = np.zeros(int(0.40 * sample_rate), dtype=np.float32)
    tone = np.full(int(0.50 * sample_rate), 0.2, dtype=np.float32)
    source = tmp_path / "tts" / "0001.wav"
    source.parent.mkdir()
    sf.write(str(source), np.concatenate([silence, tone, long_pause, tone, tail]), sample_rate)

    trimmed = audio._trim_tts_file(source, tmp_path / "trimmed")

    original = sf.info(str(source)).duration
    cleaned = sf.info(str(trimmed)).duration
    assert cleaned < original - 0.8
    assert cleaned > 1.0


def test_merge_tts_audio_keeps_original_speed_floor_without_overlap(monkeypatch, tmp_path):
    session = tmp_path / "session"
    metadata = session / "metadata"
    tts_dir = session / "segments" / "tts"
    metadata.mkdir(parents=True)
    translation_file = metadata / "translation.zh.json"
    translation_file.write_text(
        json.dumps(
            {
                "translation": [
                    {"start_time": 0, "end_time": 1000, "dst": "一"},
                    {"start_time": 1000, "end_time": 2000, "dst": "二"},
                    {"start_time": 2000, "end_time": 3000, "dst": "三"},
                ]
            }
        ),
        encoding="utf-8",
    )
    for index in range(1, 4):
        _write_tone(tts_dir / f"{index:04d}.wav", 2.0)

    def fake_stretch(audio_file: Path, ratio: float, target_sec: float, cache_dir: Path):
        sample_rate = sf.info(str(audio_file)).samplerate
        stretch_factors.append(round(ratio, 4))
        return np.zeros(int(target_sec * sample_rate), dtype=np.float32), sample_rate

    stretch_factors: list[float] = []
    monkeypatch.setattr(audio, "_stretch_segment", fake_stretch)

    dubbing, timings = audio.merge_tts_audio(translation_file, tts_dir, session)

    data = json.loads(timings.read_text(encoding="utf-8"))["translation"]
    assert dubbing.exists()
    assert min(stretch_factors) == round(audio.BASE_FACTOR_MIN * audio.LOCAL_FACTOR_MIN, 4)
    for previous, current in zip(data, data[1:]):
        assert current["actual_start_time"] >= previous["actual_end_time"]
    assert data[-1]["actual_start_time"] > data[-1]["start_time"]
