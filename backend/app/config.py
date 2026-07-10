from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
COOKIE_DIR = DATA_DIR / "cookies"
DB_PATH = DATA_DIR / "youdub.sqlite"
YOUTUBE_COOKIE_PATH = COOKIE_DIR / "youtube.txt"
WORKFOLDER = Path(os.getenv("WORKFOLDER", str(REPO_ROOT / "workfolder"))).expanduser()
LOG_DIR = DATA_DIR / "logs"
MODEL_CACHE_DIR = Path(os.getenv("MODEL_CACHE_DIR", str(DATA_DIR / "modelscope"))).expanduser()


def ensure_runtime_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    COOKIE_DIR.mkdir(parents=True, exist_ok=True)
    WORKFOLDER.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def device() -> str:
    configured = os.getenv("DEVICE") or os.getenv("CUDA_DEVICE")
    if configured:
        return configured
    return "cuda"


def openai_defaults() -> dict[str, str]:
    return {
        "base_url": os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_API_BASE") or "https://api.openai.com/v1",
        "api_key": os.getenv("OPENAI_API_KEY", ""),
        "model": os.getenv("OPENAI_MODEL") or os.getenv("OPENAI_MODEL_NAME") or "gpt-4o-mini",
        "translate_concurrency": os.getenv("OPENAI_TRANSLATE_CONCURRENCY", "50"),
    }


def ffmpeg_binary() -> str:
    return os.getenv("FFMPEG_PATH", "").strip() or "ffmpeg"


def ffprobe_binary() -> str:
    return os.getenv("FFPROBE_PATH", "").strip() or "ffprobe"


def ytdlp_defaults() -> dict[str, str]:
    return {
        "proxy_port": os.getenv("YTDLP_PROXY_PORT", ""),
    }


def mail_defaults() -> dict[str, str]:
    return {
        "enabled": os.getenv("MAIL_ENABLED", "false"),
        "smtp_host": os.getenv("MAIL_SMTP_HOST", ""),
        "smtp_port": os.getenv("MAIL_SMTP_PORT", "587"),
        "smtp_username": os.getenv("MAIL_SMTP_USERNAME", ""),
        "smtp_password": os.getenv("MAIL_SMTP_PASSWORD", ""),
        "from_address": os.getenv("MAIL_FROM_ADDRESS", ""),
        "to_addresses": os.getenv("MAIL_TO_ADDRESSES", ""),
        "smtp_security": os.getenv("MAIL_SMTP_SECURITY", "tls"),
        "notify_on_success": os.getenv("MAIL_NOTIFY_ON_SUCCESS", "true"),
        "notify_on_failure": os.getenv("MAIL_NOTIFY_ON_FAILURE", "true"),
    }
