from __future__ import annotations

import json

import pytest

from backend.app.adapters import openai_translate
from backend.app.adapters.openai_translate import (
    HotwordItem,
    PreprocessResponse,
    CorrectionItem,
)
from backend.app.sources import detect_source


YT_SOURCE = detect_source("https://www.youtube.com/watch?v=abcdefghijk")
BB_SOURCE = detect_source("https://www.bilibili.com/video/BV1xx411c7mD")


def _write_asr(path, n: int, full_text: str | None = None) -> None:
    utterances = [
        {"text": f"S{i}.", "start_time": i * 1000, "end_time": (i + 1) * 1000}
        for i in range(n)
    ]
    payload = {"result": {"utterances": utterances, "text": full_text or " ".join(u["text"] for u in utterances)}}
    path.write_text(json.dumps(payload), encoding="utf-8")


def _settings() -> dict[str, str]:
    return {"base_url": "https://example.com/v1", "api_key": "sk-test", "model": "model-x"}


def _stub_preprocess(monkeypatch, response: PreprocessResponse | None = None):
    seen: list[dict] = []

    def fake(full_text, meta, source, **kw):
        seen.append({"full_text": full_text, "meta": meta, "source": source, **kw})
        return response or PreprocessResponse()

    monkeypatch.setattr(openai_translate, "preprocess", fake)
    return seen


def _stub_translate_batch(monkeypatch, transform):
    seen: list[dict] = []

    def fake(texts, source, meta, pre, **kw):
        seen.append({"texts": list(texts), "source": source, "meta": meta, "pre": pre, **kw})
        return [transform(t) for t in texts]

    monkeypatch.setattr(openai_translate, "translate_batch", fake)
    return seen


def test_translate_asr_writes_preprocess_artifact(tmp_path, monkeypatch):
    metadata = tmp_path / "metadata"
    metadata.mkdir()
    asr_file = metadata / "asr_fixed.json"
    _write_asr(asr_file, 1)

    pre = PreprocessResponse(
        summary="Video recap",
        hotwords=[HotwordItem(src="Fable 5", dst="Fable 5")],
        corrections=[CorrectionItem(wrong="java script", correct="JavaScript")],
    )
    monkeypatch.setattr(openai_translate, "preprocess", lambda *a, **kw: pre)
    _stub_translate_batch(monkeypatch, lambda t: f"zh:{t}")

    openai_translate.translate_asr(asr_file, tmp_path, _settings(), YT_SOURCE)
    artifact = metadata / "translation_preprocess.json"
    assert artifact.exists()
    saved = json.loads(artifact.read_text(encoding="utf-8"))
    assert saved["summary"] == "Video recap"
    assert saved["hotwords"][0]["src"] == "Fable 5"
    assert saved["corrections"][0]["correct"] == "JavaScript"


def test_translate_asr_reuses_preprocess_artifact_without_calling_api(tmp_path, monkeypatch):
    metadata = tmp_path / "metadata"
    metadata.mkdir()
    asr_file = metadata / "asr_fixed.json"
    _write_asr(asr_file, 1)
    (metadata / "translation_preprocess.json").write_text(
        json.dumps(
            {
                "summary": "cached",
                "hotwords": [{"src": "GPU", "dst": "GPU"}],
                "corrections": [],
            }
        ),
        encoding="utf-8",
    )

    def fail_preprocess(*args, **kwargs):
        raise AssertionError("preprocess should not run when artifact exists")

    monkeypatch.setattr(openai_translate, "preprocess", fail_preprocess)
    seen = _stub_translate_batch(monkeypatch, lambda t: f"zh:{t}")

    openai_translate.translate_asr(asr_file, tmp_path, _settings(), YT_SOURCE)
    assert len(seen) == 1
    assert seen[0]["pre"].summary == "cached"


def test_translate_asr_writes_schema_with_speaker_and_lang(tmp_path, monkeypatch):
    metadata = tmp_path / "metadata"
    metadata.mkdir()
    asr_file = metadata / "asr.json"
    _write_asr(asr_file, 2)

    _stub_preprocess(monkeypatch)
    _stub_translate_batch(monkeypatch, lambda t: f"zh:{t}")

    out = openai_translate.translate_asr(asr_file, tmp_path, _settings(), YT_SOURCE)
    items = json.loads(out.read_text(encoding="utf-8"))["translation"]
    assert [i["dst"] for i in items] == ["zh:S0.", "zh:S1."]
    assert {i["src_lang"] for i in items} == {"en"}
    assert {i["dst_lang"] for i in items} == {"zh"}
    assert {i["speaker"] for i in items} == {"1"}
    assert items[0]["start_time"] == 0


def test_translate_asr_output_filename_uses_target_lang(tmp_path, monkeypatch):
    metadata = tmp_path / "metadata"
    metadata.mkdir()
    asr_file = metadata / "asr.json"
    _write_asr(asr_file, 1)

    _stub_preprocess(monkeypatch)
    _stub_translate_batch(monkeypatch, lambda _t: "x")

    out = openai_translate.translate_asr(asr_file, tmp_path, _settings(), BB_SOURCE)
    assert out.name == "translation.en.json"


def test_translate_asr_passes_meta_and_full_text_to_preprocess(tmp_path, monkeypatch):
    metadata = tmp_path / "metadata"
    metadata.mkdir()
    asr_file = metadata / "asr.json"
    _write_asr(asr_file, 1, full_text="hello world")
    (metadata / "ytdlp_info.json").write_text(
        json.dumps({"title": "T", "uploader": "U", "description": "D"}),
        encoding="utf-8",
    )

    seen = _stub_preprocess(monkeypatch)
    _stub_translate_batch(monkeypatch, lambda t: t)

    openai_translate.translate_asr(asr_file, tmp_path, _settings(), YT_SOURCE)
    assert seen[0]["full_text"] == "hello world"
    assert seen[0]["meta"] == {"title": "T", "uploader": "U", "description": "D"}


def test_translate_asr_invokes_translate_batch_with_all_texts_at_once(tmp_path, monkeypatch):
    metadata = tmp_path / "metadata"
    metadata.mkdir()
    asr_file = metadata / "asr.json"
    _write_asr(asr_file, 5)

    _stub_preprocess(monkeypatch, PreprocessResponse(hotwords=[HotwordItem(src="x", dst="y")]))
    seen = _stub_translate_batch(monkeypatch, lambda t: f"zh:{t}")

    openai_translate.translate_asr(asr_file, tmp_path, _settings(), YT_SOURCE)
    assert len(seen) == 1
    assert seen[0]["texts"] == ["S0.", "S1.", "S2.", "S3.", "S4."]
    assert seen[0]["pre"].hotwords[0].src == "x"


def test_translate_batch_replaces_em_dash_for_zh_target(monkeypatch):
    monkeypatch.setattr(openai_translate, "_call_json", lambda *a, **kw: {"items": ["你好——世界"]})
    monkeypatch.setattr(openai_translate, "_client", lambda *a, **kw: object())

    out = openai_translate.translate_batch(
        ["Hello world."], YT_SOURCE, {}, PreprocessResponse(),
        base_url="u", api_key="k", model="m",
    )
    assert out == ["你好，世界"]


def test_translate_batch_does_not_replace_em_dash_for_en_target(monkeypatch):
    monkeypatch.setattr(
        openai_translate, "_call_json", lambda *a, **kw: {"items": ["He said—wait—and left."]}
    )
    monkeypatch.setattr(openai_translate, "_client", lambda *a, **kw: object())

    out = openai_translate.translate_batch(
        ["他说——等等——就走了。"], BB_SOURCE, {}, PreprocessResponse(),
        base_url="u", api_key="k", model="m",
    )
    assert out == ["He said—wait—and left."]


def test_translate_batch_uses_shared_system_prompt(monkeypatch):
    captured: list[str] = []
    lock = __import__("threading").Lock()

    def fake_call_json(client, model, system, user):
        with lock:
            captured.append(system)
        lines = user.strip().split("\n")
        return {"items": [f"dst:{line.split('. ', 1)[1]}" for line in lines]}

    monkeypatch.setattr(openai_translate, "_call_json", fake_call_json)
    monkeypatch.setattr(openai_translate, "_client", lambda *a, **kw: object())

    texts = [f"s{i}" for i in range(5)]
    out = openai_translate.translate_batch(
        texts, BB_SOURCE, {}, PreprocessResponse(),
        base_url="u", api_key="k", model="m", concurrency=4, batch_size=2,
    )
    assert out == [f"dst:s{i}" for i in range(5)]
    assert len(set(captured)) == 1, "system prompt must be identical across calls for prompt cache"
    assert len(captured) == 3, "5 sentences with batch_size=2 must use 3 requests, not 5"


@pytest.mark.parametrize("value", ["abc", "1.5", "0", "-1", "201", ""])
def test_concurrency_from_bad_saved_values_falls_back_to_default(value):
    assert openai_translate._concurrency_from({"translate_concurrency": value}) == 50


def test_translate_sentence_retries_on_empty_dst(monkeypatch):
    calls = {"n": 0}

    def fake_call_json(client, model, system, user):
        calls["n"] += 1
        return {"items": [""]} if calls["n"] == 1 else {"items": ["ok"]}

    monkeypatch.setattr(openai_translate, "_call_json", fake_call_json)

    out = openai_translate.translate_sentence("hello", "en", object(), "m", "sys")
    assert out == "ok"
    assert calls["n"] == 2


def test_translate_sentence_raises_after_retries(monkeypatch):
    def fake_call_json(client, model, system, user):
        raise ValueError("boom")

    monkeypatch.setattr(openai_translate, "_call_json", fake_call_json)

    with pytest.raises(RuntimeError, match="translate chunk failed"):
        openai_translate.translate_sentence("x", "en", object(), "m", "sys")


def test_preprocess_returns_empty_when_repeatedly_invalid(monkeypatch):
    def fake_call_json(client, model, system, user):
        return {"summary": 123, "hotwords": "bad"}

    monkeypatch.setattr(openai_translate, "_call_json", fake_call_json)
    monkeypatch.setattr(openai_translate, "_client", lambda *a, **kw: object())

    pre = openai_translate.preprocess(
        "text", {"title": "t"}, YT_SOURCE,
        base_url="u", api_key="k", model="m",
    )
    assert pre.summary == ""
    assert pre.hotwords == []
    assert pre.corrections == []


def test_translate_system_prompt_contains_meta_summary_hotwords(monkeypatch):
    pre = PreprocessResponse(
        summary="Recap of the talk.",
        hotwords=[HotwordItem(src="LEGO", dst="乐高")],
    )
    meta = {"title": "Demo", "uploader": "Alice", "description": "Long description"}
    system = openai_translate._translate_system(YT_SOURCE, meta, pre)
    assert "Demo" in system
    assert "Alice" in system
    assert "Long description" in system
    assert "Recap of the talk." in system
    assert "LEGO -> 乐高" in system


def test_translate_batch_sends_one_request_per_chunk(monkeypatch):
    requests: list[int] = []
    lock = __import__("threading").Lock()

    def fake_call_json(client, model, system, user):
        lines = user.strip().split("\n")
        with lock:
            requests.append(len(lines))
        return {"items": [f"t{line.split('. ', 1)[1]}" for line in lines]}

    monkeypatch.setattr(openai_translate, "_call_json", fake_call_json)
    monkeypatch.setattr(openai_translate, "_client", lambda *a, **kw: object())

    texts = [f"s{i}" for i in range(7)]
    out = openai_translate.translate_batch(
        texts, BB_SOURCE, {}, PreprocessResponse(),
        base_url="u", api_key="k", model="m", batch_size=3,
    )
    assert out == [f"ts{i}" for i in range(7)]
    assert sorted(requests) == [1, 3, 3], "7 sentences with batch_size=3 -> chunks of 3,3,1"


def test_translate_batch_falls_back_to_per_sentence_when_batch_misaligns(monkeypatch):
    def fake_call_json(client, model, system, user):
        lines = user.strip().split("\n")
        if len(lines) > 1:
            # misbehaving model: wrong item count for a multi-sentence batch
            return {"items": ["only-one"]}
        return {"items": [f"solo:{lines[0].split('. ', 1)[1]}"]}

    monkeypatch.setattr(openai_translate, "_call_json", fake_call_json)
    monkeypatch.setattr(openai_translate, "_client", lambda *a, **kw: object())

    texts = ["a", "b", "c"]
    out = openai_translate.translate_batch(
        texts, BB_SOURCE, {}, PreprocessResponse(),
        base_url="u", api_key="k", model="m", batch_size=3,
    )
    assert out == ["solo:a", "solo:b", "solo:c"]


def test_translate_chunk_merges_single_sentence_split_into_clauses_zh(monkeypatch):
    monkeypatch.setattr(
        openai_translate, "_call_json", lambda *a, **kw: {"items": ["前半句，", "后半句。"]}
    )
    out = openai_translate.translate_sentence("a long source sentence", "zh", object(), "m", "sys")
    assert out == "前半句，后半句。"


def test_translate_chunk_merges_single_sentence_split_into_clauses_en(monkeypatch):
    monkeypatch.setattr(
        openai_translate, "_call_json", lambda *a, **kw: {"items": ["First part,", "second part."]}
    )
    out = openai_translate.translate_sentence("一个很长的句子", "en", object(), "m", "sys")
    assert out == "First part, second part."


def test_translate_batch_accepts_top_level_json_array(monkeypatch):
    # model returns a bare JSON array instead of {"items": [...]}
    def fake_call_json(client, model, system, user):
        lines = user.strip().split("\n")
        return [f"arr:{line.split('. ', 1)[1]}" for line in lines]

    monkeypatch.setattr(openai_translate, "_call_json", fake_call_json)
    monkeypatch.setattr(openai_translate, "_client", lambda *a, **kw: object())

    out = openai_translate.translate_batch(
        ["a", "b", "c"], BB_SOURCE, {}, PreprocessResponse(),
        base_url="u", api_key="k", model="m", batch_size=3,
    )
    assert out == ["arr:a", "arr:b", "arr:c"]


def test_extract_json_parses_bare_and_noisy_arrays():
    assert openai_translate._extract_json('["x", "y"]') == ["x", "y"]
    assert openai_translate._extract_json('结果：\n["x", "y"]\ndone') == ["x", "y"]
