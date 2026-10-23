from __future__ import annotations

import json
from pathlib import Path


DEFAULT_CUT_INTERVALS: tuple[dict[str, float], ...] = ()


def default_cut_intervals() -> list[dict[str, float]]:
    return [dict(interval) for interval in DEFAULT_CUT_INTERVALS]


def serialize_cut_intervals(intervals: list[dict[str, float]]) -> str:
    return json.dumps(intervals, ensure_ascii=False)


def default_cut_intervals_json() -> str:
    return serialize_cut_intervals(default_cut_intervals())


def cut_intervals_file(session: Path) -> Path:
    return session / "metadata" / "cut_intervals.json"


def write_default_cut_intervals(session: Path) -> Path:
    intervals_file = cut_intervals_file(session)
    intervals_file.parent.mkdir(parents=True, exist_ok=True)
    intervals_file.write_text(default_cut_intervals_json(), encoding="utf-8")
    return intervals_file
