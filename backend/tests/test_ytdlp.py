from pathlib import Path

from backend.app.adapters import ytdlp
from backend.app.sources import SourceConfig


def _make_source(*, use_proxy: bool, cookie_dir: Path) -> SourceConfig:
    cookie_path = cookie_dir / "missing-cookie.txt"

    class _Source(SourceConfig):
        @property
        def cookie_path(self):
            return cookie_path

    return _Source(
        name="test",
        matches=lambda url: True,
        use_proxy=use_proxy,
        cookie_filename="missing-cookie.txt",
        asr_language="en",
        target_language="zh",
    )


def test_ytdlp_proxy_port_takes_priority(monkeypatch, tmp_path):
    monkeypatch.setenv("HTTP_PROXY", "http://env-proxy:8080")
    source = _make_source(use_proxy=True, cookie_dir=tmp_path)

    options = ytdlp._ydl_base(source, "7890")

    assert options["proxy"] == "http://127.0.0.1:7890"


def test_ytdlp_proxy_falls_back_to_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("HTTP_PROXY", "http://env-proxy:8080")
    source = _make_source(use_proxy=True, cookie_dir=tmp_path)

    options = ytdlp._ydl_base(source, "")

    assert options["proxy"] == "http://env-proxy:8080"


def test_ytdlp_disables_proxy_when_source_opts_out(monkeypatch, tmp_path):
    monkeypatch.setenv("HTTP_PROXY", "http://env-proxy:8080")
    source = _make_source(use_proxy=False, cookie_dir=tmp_path)

    options = ytdlp._ydl_base(source, "7890")

    assert options["proxy"] == ""


def test_ytdlp_enables_node_js_runtime(tmp_path):
    source = _make_source(use_proxy=True, cookie_dir=tmp_path)

    options = ytdlp._ydl_base(source, "")

    assert options["js_runtimes"] == {"node": {}}


def test_ytdlp_format_candidates_start_with_backend_format():
    assert ytdlp.FORMAT_CANDIDATES[0] == "bestvideo[height<=1080]+bestaudio/best"
    assert "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best" not in ytdlp.FORMAT_CANDIDATES


def test_download_cover_from_info_writes_cover_in_download_stage(monkeypatch, tmp_path):
    source = _make_source(use_proxy=False, cookie_dir=tmp_path)
    calls: list[str] = []

    class Response:
        content = b"cover"

        def raise_for_status(self):
            return None

    def fake_get(url, **kwargs):
        calls.append(url)
        return Response()

    monkeypatch.setattr(ytdlp.requests, "get", fake_get)

    dest = tmp_path / "cover.jpg"
    result = ytdlp._download_cover_from_info(
        {
            "id": "abc123def45",
            "webpage_url": "https://www.youtube.com/watch?v=abc123def45",
            "thumbnail": "https://example.com/thumb.jpg",
        },
        dest,
        source=source,
        proxy_port="",
    )

    assert result == dest
    assert dest.read_bytes() == b"cover"
    assert calls[0] == "https://img.youtube.com/vi/abc123def45/maxresdefault.jpg"


def test_download_cover_from_info_reuses_existing_cover(monkeypatch, tmp_path):
    source = _make_source(use_proxy=False, cookie_dir=tmp_path)
    dest = tmp_path / "cover.jpg"
    dest.write_bytes(b"cached")

    def fail_get(*args, **kwargs):
        raise AssertionError("existing cover should not be downloaded again")

    monkeypatch.setattr(ytdlp.requests, "get", fail_get)

    result = ytdlp._download_cover_from_info(
        {"id": "abc123", "webpage_url": "https://www.youtube.com/watch?v=abc123"},
        dest,
        source=source,
        proxy_port="",
    )

    assert result == dest
    assert dest.read_bytes() == b"cached"
