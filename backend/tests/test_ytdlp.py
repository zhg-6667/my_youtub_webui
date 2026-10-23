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


def _make_youtube_source(*, use_proxy: bool, cookie_dir: Path) -> SourceConfig:
    cookie_path = cookie_dir / "youtube.txt"

    class _Source(SourceConfig):
        @property
        def cookie_path(self):
            return cookie_path

    return _Source(
        name="youtube",
        matches=lambda url: True,
        use_proxy=use_proxy,
        cookie_filename="youtube.txt",
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


def test_ytdlp_uses_configurable_user_agent(monkeypatch, tmp_path):
    monkeypatch.setenv("YTDLP_USER_AGENT", "custom-browser-ua")
    source = _make_source(use_proxy=True, cookie_dir=tmp_path)

    options = ytdlp._ydl_base(source, "")

    assert options["http_headers"]["User-Agent"] == "custom-browser-ua"


def test_ytdlp_youtube_uses_web_safari_excluding_pot_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("YTDLP_BGUTIL_SERVER_HOME", str(tmp_path / "missing-bgutil"))
    source = _make_youtube_source(use_proxy=True, cookie_dir=tmp_path)

    options = ytdlp._ydl_base(source, "")

    assert options["extractor_args"] == {
        "youtube": {
            "player_client": ["default,-web_safari"],
            "webpage_client": ["web"],
            "player_js_version": ["actual"],
            "formats": ["incomplete"],
            "fetch_pot": ["always"],
        }
    }


def test_ytdlp_adds_bgutil_script_provider(monkeypatch, tmp_path):
    server_home = tmp_path / "bgutil" / "server"
    (server_home / "build").mkdir(parents=True)
    (server_home / "build" / "generate_once.js").write_text("", encoding="utf-8")
    monkeypatch.setenv("YTDLP_BGUTIL_SERVER_HOME", str(server_home))
    source = _make_youtube_source(use_proxy=True, cookie_dir=tmp_path)

    options = ytdlp._ydl_base(source, "")

    assert options["extractor_args"]["youtubepot-bgutilscript"] == {
        "server_home": [str(server_home)]
    }


def test_download_video_uses_temporary_cookiefile(monkeypatch, tmp_path):
    cookie_file = tmp_path / "missing-cookie.txt"
    original_cookie = "# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tFALSE\t0\tSID\toriginal\n"
    cookie_file.write_text(original_cookie, encoding="utf-8")
    source = _make_source(use_proxy=False, cookie_dir=tmp_path)
    seen_cookiefiles: list[Path] = []

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts
            cookiefile = opts.get("cookiefile")
            if cookiefile:
                cookie_path = Path(cookiefile)
                seen_cookiefiles.append(cookie_path)
                cookie_path.write_text("mutated-by-ytdlp\n", encoding="utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def extract_info(self, url, download=False):
            return {
                "id": "abc123def45",
                "title": "Example",
                "uploader": "Uploader",
                "webpage_url": url,
            }

        def download(self, urls):
            output = Path(self.opts["outtmpl"])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"video")

        def sanitize_info(self, info):
            return info

    monkeypatch.setattr(ytdlp.yt_dlp, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(ytdlp, "_download_cover_from_info", lambda *args, **kwargs: None)

    session, _info = ytdlp.download_video(
        "https://www.youtube.com/watch?v=abc123def45", tmp_path / "work", source
    )

    assert (session / "media" / "video_source.mp4").read_bytes() == b"video"
    assert cookie_file.read_text(encoding="utf-8") == original_cookie
    assert seen_cookiefiles
    assert all(path != cookie_file for path in seen_cookiefiles)


def test_ytdlp_format_candidates_start_with_1080p():
    assert ytdlp.FORMAT_CANDIDATES[0] == (
        "bestvideo[height<=1080][protocol!*=m3u8]+bestaudio[protocol!*=m3u8]/"
        "best[height<=1080][protocol!*=m3u8]"
    )
    assert all("m3u8" in candidate for candidate in ytdlp.FORMAT_CANDIDATES)
    assert "bestvideo+bestaudio/best" not in ytdlp.FORMAT_CANDIDATES
    assert "best" not in ytdlp.FORMAT_CANDIDATES


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
