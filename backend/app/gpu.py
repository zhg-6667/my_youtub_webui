from __future__ import annotations

import gc


def _cuda_available() -> bool:
    try:
        import torch
    except ImportError:
        return False
    return torch.cuda.is_available()


def gpu_memory_status() -> str | None:
    """Return a "used/total" VRAM summary, or None when no CUDA device is present."""
    if not _cuda_available():
        return None

    import torch

    free, total = torch.cuda.mem_get_info()
    used = total - free
    mib = 1024 * 1024
    return f"{used / mib:.0f}MiB/{total / mib:.0f}MiB"


def free_gpu_memory(reason: str = "") -> None:
    """Release cached CUDA memory. No-op on CPU/MPS-only environments."""
    gc.collect()
    if not _cuda_available():
        return

    import torch

    torch.cuda.empty_cache()
    torch.cuda.ipc_collect()
