"""Task-local Bilibili cover assets and their selection order."""

from __future__ import annotations

import shutil
from pathlib import Path


COVER_DIR_NAME = "bilibili_cover"
MANUAL_COVER_STEM = "manual"
EDITED_COVER_NAME = "edited.jpg"
EDITOR_PROJECT_NAME = "editor.json"
ALLOWED_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")


def cover_dir(session_path: str | Path) -> Path:
    return Path(session_path) / COVER_DIR_NAME


def manual_cover_path(session_path: str | Path) -> Path | None:
    directory = cover_dir(session_path)
    for suffix in ALLOWED_SUFFIXES:
        candidate = directory / f"{MANUAL_COVER_STEM}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def edited_cover_path(session_path: str | Path) -> Path | None:
    candidate = cover_dir(session_path) / EDITED_COVER_NAME
    return candidate if candidate.is_file() else None


def source_cover_path(session_path: str | Path) -> Path | None:
    candidate = Path(session_path) / "cover.jpg"
    return candidate if candidate.is_file() else None


def resolve_cover_path(session_path: str | Path) -> tuple[Path | None, str | None]:
    """Return the cover used by previews and uploads, in stable priority order."""
    for source, path in (
        ("edited", edited_cover_path(session_path)),
        ("manual", manual_cover_path(session_path)),
        ("source", source_cover_path(session_path)),
    ):
        if path:
            return path, source
    return None, None


def editor_project_path(session_path: str | Path) -> Path:
    return cover_dir(session_path) / EDITOR_PROJECT_NAME



def remove_manual_cover(session_path: str | Path) -> None:
    directory = cover_dir(session_path)
    for suffix in ALLOWED_SUFFIXES:
        (directory / f"{MANUAL_COVER_STEM}{suffix}").unlink(missing_ok=True)


def remove_edited_cover(session_path: str | Path) -> None:
    directory = cover_dir(session_path)
    (directory / EDITED_COVER_NAME).unlink(missing_ok=True)
    editor_project_path(session_path).unlink(missing_ok=True)


def snapshot_cover_for_job(session_path: str | Path, job_id: str) -> str | None:
    """Freeze the resolved task cover so later edits cannot change a queued job."""
    cover, _ = resolve_cover_path(session_path)
    if not cover:
        return None
    destination = cover_dir(session_path) / "jobs" / f"{job_id}{cover.suffix.lower()}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cover, destination)
    return str(destination)
