from __future__ import annotations

import pytest

from backend.app import youtube_channel_lookup as lookup


@pytest.mark.parametrize("suffix", ["", "/", "/shorts?view=0", "/videos", "/streams", "/featured"])
def test_normalize_author_homepage(suffix):
    assert lookup.normalize_channel_url(f"https://www.youtube.com/@pokedexfillers{suffix}") == (
        "https://www.youtube.com/@pokedexfillers"
    )


@pytest.mark.parametrize("url", [
    "https://notyoutube.com/@author", "https://youtube.com.evil.test/@author",
    "https://www.youtube.com/watch?v=abcdefghijk", "https://www.youtube.com/shorts/abcdefghijk",
    "https://www.youtube.com/playlist?list=PLtest", "https://www.youtube.com/@author/playlists",
    "https://www.youtube.com/channel/UCbad", "https://www.youtube.com/@", "",
])
def test_reject_non_author_urls(url):
    with pytest.raises(ValueError):
        lookup.normalize_channel_url(url)


def install_fake_ydl(monkeypatch, extract):
    import yt_dlp

    class FakeYDL:
        def __init__(self, opts):
            assert opts["playlistend"] == 2
            assert opts["extract_flat"] is True
            assert opts["skip_download"] is True

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download):
            assert download is False
            return extract(url)

    monkeypatch.setattr(lookup, "_youtube_ydl_opts", lambda **kwargs: {})
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)


def test_fetches_both_tabs_with_separate_limits_and_safe_links(monkeypatch):
    calls = []

    def extract(url):
        calls.append(url)
        prefix = "short" if url.endswith("/shorts") else "video"
        return {"channel": "Author", "channel_id": "UCtest", "entries": iter([
            {"id": f"{prefix}000001".ljust(11, "0"), "title": "Newest", "duration": 61.4, "url": "https://bad.test"},
            {"id": f"{prefix}000002".ljust(11, "0"), "title": "Older"},
            {"id": "extravideo1", "title": "Outside limit"},
        ])}

    install_fake_ydl(monkeypatch, extract)
    result = lookup.fetch_author_videos("https://www.youtube.com/@author/shorts", 2)
    assert calls == ["https://www.youtube.com/@author/videos", "https://www.youtube.com/@author/shorts"]
    assert result["title"] == "Author"
    assert len(result["videos"]) == len(result["shorts"]) == 2
    assert result["videos"][0]["url"].startswith("https://www.youtube.com/watch?v=")
    assert result["shorts"][0]["url"].startswith("https://www.youtube.com/shorts/")
    assert result["videos"][0]["duration_seconds"] == 61
    assert result["shorts"][1]["duration_seconds"] is None


@pytest.mark.parametrize("missing", [True, False])
def test_missing_tab_is_empty_but_network_failure_is_reported(monkeypatch, missing):
    def extract(url):
        if url.endswith("/videos"):
            raise RuntimeError("This channel does not have a videos tab" if missing else "Connection timed out")
        return {"channel": "Shorts author", "entries": [{"id": "abcdefghijk", "title": "Short"}]}

    install_fake_ydl(monkeypatch, extract)
    result = lookup.fetch_author_videos("https://www.youtube.com/@author", 2)
    assert result["videos"] == []
    assert len(result["shorts"]) == 1
    assert ("videos" in result["errors"]) is not missing


def test_batch_merges_tab_duplicates_and_isolates_author_failures(monkeypatch):
    def fetch(url, latest_count):
        assert latest_count == 7
        if "broken" in url:
            raise RuntimeError("Unavailable")
        return {"input_url": url, "title": "Success"}

    monkeypatch.setattr(lookup, "fetch_author_videos", fetch)
    results = lookup.lookup_authors([
        "https://www.youtube.com/@ok", "https://www.youtube.com/@ok/shorts",
        "https://www.youtube.com/@broken", "https://www.youtube.com/@other",
    ], 7)
    assert len(results) == 3
    assert results[0]["title"] == results[2]["title"] == "Success"
    assert results[1]["errors"] == {"channel": "Unavailable"}
