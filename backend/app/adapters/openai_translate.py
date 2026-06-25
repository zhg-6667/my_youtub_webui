from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError

from ..sources import SourceConfig
from ._translate_prompts import PREPROCESS_PROMPT, TRANSLATE_RULES
from .openai_client import normalize_openai_base_url

log = logging.getLogger(__name__)

API_SETTING_KEYS = ("base_url", "api_key", "model")
PREPROCESS_RETRY = 2
TRANSLATE_RETRY = 2
DESCRIPTION_LIMIT = 500
DEFAULT_CONCURRENCY = 50
BATCH_SIZE = 20


class HotwordItem(BaseModel):
    src: str
    dst: str


class CorrectionItem(BaseModel):
    wrong: str
    correct: str


class PreprocessResponse(BaseModel):
    summary: str = ""
    hotwords: list[HotwordItem] = Field(default_factory=list)
    corrections: list[CorrectionItem] = Field(default_factory=list)


class TranslationItem(BaseModel):
    dst: str


def list_models(*, base_url: str, api_key: str) -> list[str]:
    if not api_key:
        raise ValueError("OpenAI API key is not configured.")
    client = OpenAI(api_key=api_key, base_url=normalize_openai_base_url(base_url))
    response = client.models.list()
    seen: set[str] = set()
    models: list[str] = []
    for item in response.data:
        model_id = getattr(item, "id", "")
        if model_id and model_id not in seen:
            seen.add(model_id)
            models.append(model_id)
    return models


def _client(base_url: str, api_key: str) -> OpenAI:
    if not api_key:
        raise ValueError("OpenAI API key is not configured.")
    return OpenAI(api_key=api_key, base_url=normalize_openai_base_url(base_url))


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


def _extract_json(raw: str) -> dict[str, Any] | list[Any]:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    last_exc: json.JSONDecodeError | None = None
    for pattern in (_JSON_OBJECT_RE, _JSON_ARRAY_RE):
        match = pattern.search(raw)
        if not match:
            continue
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            last_exc = exc
    if last_exc is not None:
        raise json.JSONDecodeError(
            f"{last_exc.msg}; len={len(raw)}; raw[:300]={raw[:300]!r}; raw[-200:]={raw[-200:]!r}",
            raw,
            last_exc.pos,
        ) from None
    raise json.JSONDecodeError(f"no JSON object found; raw[:300]={raw[:300]!r}", raw, 0)


def _call_json(client: OpenAI, model: str, system: str, user: str) -> dict[str, Any]:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.2,
    )
    raw = response.choices[0].message.content or "{}"
    return _extract_json(raw)


def _format_terms(items: list, fmt: str, empty: str) -> str:
    if not items:
        return empty
    return "\n".join(fmt.format(**item.model_dump()) for item in items)


def _meta_view(meta: dict[str, Any]) -> dict[str, str]:
    description = (meta.get("description") or "").strip()
    if len(description) > DESCRIPTION_LIMIT:
        description = description[:DESCRIPTION_LIMIT] + "..."
    return {
        "title": str(meta.get("title") or "").strip() or "(unknown)",
        "uploader": str(meta.get("uploader") or "").strip() or "(unknown)",
        "description": description or "(none)",
    }


def preprocess(
    full_text: str,
    meta: dict[str, Any],
    source: SourceConfig,
    *,
    base_url: str,
    api_key: str,
    model: str,
) -> PreprocessResponse:
    user = PREPROCESS_PROMPT.format(
        src_language_name=source.asr_language_name,
        dst_language_name=source.target_language_name,
        full_text=full_text,
        **_meta_view(meta),
    )
    client = _client(base_url, api_key)
    last_error: Exception | None = None
    for attempt in range(PREPROCESS_RETRY + 1):
        try:
            data = _call_json(client, model, "You output strict JSON only.", user)
            return PreprocessResponse.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = exc
            log.warning("preprocess attempt %d failed: %s", attempt + 1, exc)
    log.error("preprocess gave up, returning empty: %s", last_error)
    return PreprocessResponse()


def _translate_system(source: SourceConfig, meta: dict[str, Any], pre: PreprocessResponse) -> str:
    rules = TRANSLATE_RULES[source.target_language]
    return rules.format(
        summary=pre.summary or "(none)",
        hotwords=_format_terms(pre.hotwords, "{src} -> {dst}", "(none)"),
        corrections=_format_terms(pre.corrections, "{wrong} -> {correct}", "(none)"),
        **_meta_view(meta),
    )


def _post_process(text: str, target_language: str) -> str:
    cleaned = text.strip()
    if target_language == "zh":
        cleaned = cleaned.replace("——", "，")
    return cleaned


def _build_batch_user(texts: list[str]) -> str:
    return "\n".join(f"{i}. {text}" for i, text in enumerate(texts, 1))


def _parse_batch(data: dict[str, Any] | list[Any]) -> list[str]:
    if isinstance(data, list):
        items: Any = data
    elif isinstance(data, dict):
        items = data.get("items")
        if items is None:
            # tolerate a single unknown key wrapping the list, e.g. {"translations": [...]}
            wrapped = [value for value in data.values() if isinstance(value, list)]
            items = wrapped[0] if len(wrapped) == 1 else None
    else:
        items = None
    if not isinstance(items, list):
        raise ValueError(f"batch response has no items list; type={type(data).__name__}")
    out: list[str] = []
    for entry in items:
        if isinstance(entry, str):
            out.append(entry)
        elif isinstance(entry, dict):
            out.append(str(entry.get("dst") or entry.get("text") or ""))
        else:
            out.append(str(entry))
    return out


def _merge_clauses(parts: list[str], target_language: str) -> str:
    # A single source sentence may come back split into clauses; stitch them back.
    joiner = "" if target_language == "zh" else " "
    return joiner.join(part.strip() for part in parts if part.strip())


def _translate_chunk(
    texts: list[str],
    target_language: str,
    client: OpenAI,
    model: str,
    system: str,
) -> list[str]:
    last_error: Exception | None = None
    user = _build_batch_user(texts)
    for attempt in range(TRANSLATE_RETRY):
        try:
            items = _parse_batch(_call_json(client, model, system, user))
            if len(texts) == 1 and len(items) != 1:
                # single sentence split into clauses by the model: merge back to one
                items = [_merge_clauses(items, target_language)]
            if len(items) != len(texts):
                raise ValueError(f"batch returned {len(items)} items, expected {len(texts)}")
            cleaned = [_post_process(item, target_language) for item in items]
            if any(not text.strip() for text in cleaned):
                raise ValueError("empty translation in batch")
            return cleaned
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            last_error = exc
            log.warning(
                "translate chunk(size=%d) attempt %d failed: %s", len(texts), attempt + 1, exc
            )
    raise RuntimeError(f"translate chunk failed after {TRANSLATE_RETRY} attempts: {last_error}")


def translate_sentence(
    text: str,
    target_language: str,
    client: OpenAI,
    model: str,
    system: str,
) -> str:
    return _translate_chunk([text], target_language, client, model, system)[0]


def _chunked(texts: list[str], size: int) -> list[list[str]]:
    return [texts[i : i + size] for i in range(0, len(texts), size)]


def translate_batch(
    texts: list[str],
    source: SourceConfig,
    meta: dict[str, Any],
    pre: PreprocessResponse,
    *,
    base_url: str,
    api_key: str,
    model: str,
    concurrency: int = DEFAULT_CONCURRENCY,
    batch_size: int = BATCH_SIZE,
) -> list[str]:
    if not texts:
        return []
    system = _translate_system(source, meta, pre)
    client = _client(base_url, api_key)
    target_language = source.target_language
    chunks = _chunked(texts, max(1, batch_size))
    log.info(
        "translate_batch: %d sentences in %d chunk(s) of <=%d, concurrency=%d",
        len(texts), len(chunks), batch_size, concurrency,
    )

    def handle(chunk: list[str]) -> list[str]:
        try:
            return _translate_chunk(chunk, target_language, client, model, system)
        except RuntimeError:
            if len(chunk) == 1:
                raise
            log.warning("batch of %d failed, falling back to per-sentence", len(chunk))
            out: list[str] = []
            for text in chunk:
                out.extend(_translate_chunk([text], target_language, client, model, system))
            return out

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        results = list(pool.map(handle, chunks))
    flat: list[str] = []
    for result in results:
        flat.extend(result)
    return flat


def _read_meta(session: Path) -> dict[str, Any]:
    info_file = session / "metadata" / "ytdlp_info.json"
    if not info_file.exists():
        return {}
    return json.loads(info_file.read_text(encoding="utf-8"))


def _speaker(utt: dict[str, Any]) -> str:
    additions = utt.get("additions") or {}
    if isinstance(additions, dict):
        return str(additions.get("speaker") or "1")
    return "1"


def _full_text(data: dict[str, Any], texts: list[str]) -> str:
    raw = data.get("result", {}).get("text") or ""
    if raw.strip():
        return raw
    return " ".join(texts)


def preprocess_artifact_path(session: Path) -> Path:
    return session / "metadata" / "translation_preprocess.json"


def write_preprocess_artifact(session: Path, pre: PreprocessResponse) -> Path:
    path = preprocess_artifact_path(session)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pre.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_preprocess_artifact(session: Path) -> PreprocessResponse | None:
    path = preprocess_artifact_path(session)
    if not path.exists():
        return None
    return PreprocessResponse.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _concurrency_from(settings: dict[str, str]) -> int:
    raw = str(settings.get("translate_concurrency") or "").strip()
    if not raw or not all("0" <= char <= "9" for char in raw):
        return DEFAULT_CONCURRENCY
    concurrency = int(raw)
    if concurrency < 1 or concurrency > 200:
        return DEFAULT_CONCURRENCY
    return concurrency


def translate_asr(
    asr_file: Path,
    session: Path,
    settings: dict[str, str],
    source: SourceConfig,
) -> Path:
    output_file = session / "metadata" / f"translation.{source.target_language}.json"
    if output_file.exists():
        return output_file

    data = json.loads(asr_file.read_text(encoding="utf-8"))
    utterances = data["result"]["utterances"]
    texts = [u["text"].strip() for u in utterances]
    full_text = _full_text(data, texts)
    meta = _read_meta(session)

    api = {key: settings[key] for key in API_SETTING_KEYS if key in settings}
    pre = load_preprocess_artifact(session)
    if pre is None:
        pre = preprocess(full_text, meta, source, **api)
        write_preprocess_artifact(session, pre)
        log.info("Wrote translation preprocess artifact to %s", preprocess_artifact_path(session))
    else:
        log.info("Reusing translation preprocess artifact from %s", preprocess_artifact_path(session))
    dst_list = translate_batch(
        texts, source, meta, pre, **api, concurrency=_concurrency_from(settings)
    )

    translation = [
        {
            "src": text,
            "dst": dst,
            "src_lang": source.asr_language,
            "dst_lang": source.target_language,
            "start_time": utt["start_time"],
            "end_time": utt["end_time"],
            "speaker": _speaker(utt),
        }
        for text, dst, utt in zip(texts, dst_list, utterances)
    ]
    output_file.write_text(
        json.dumps({"translation": translation}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_file
