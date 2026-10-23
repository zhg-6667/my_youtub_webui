from __future__ import annotations

import json
import sys
import types
from pathlib import Path

from backend.app import bilibili_uploads, database
from backend.app import pipeline
from backend.app.pipeline import PipelineRunner


def configure_db(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.sqlite")
    database.init_db()


def _noop_stage(self, task):
    return None


def _cached_session(tmp_path: Path) -> Path:
    session = tmp_path / "session"
    for directory in ("media", "metadata", "segments/vocals", "segments/tts", "tmp"):
        (session / directory).mkdir(parents=True, exist_ok=True)
    for file in (
        "media/video_source.mp4",
        "media/audio_vocals.wav",
        "media/audio_bgm.wav",
        "metadata/asr.json",
        "metadata/asr_fixed.json",
        "metadata/translation.zh.json",
        "tmp/audio_dubbing.wav",
        "metadata/timings.json",
        "media/video_final.mp4",
    ):
        (session / file).write_bytes(b"cached")
    return session


def test_pipeline_marks_all_stages_succeeded(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    task_id = database.create_task("https://www.youtube.com/watch?v=abcdefghijk")
    final_path = tmp_path / "video_final.mp4"
    final_path.write_bytes(b"mp4")

    for name in ("_download", "_separate", "_asr", "_asr_fix", "_translate", "_split_audio", "_tts", "_merge_audio", "_concat_video", "_trim_video"):
        monkeypatch.setattr(PipelineRunner, name, _noop_stage)

    def merge_video(self, task):
        self.artifacts.final_video = final_path

    monkeypatch.setattr(PipelineRunner, "_merge_video", merge_video)

    PipelineRunner(task_id).run()
    task = database.get_task(task_id)

    assert task["status"] == "succeeded"
    assert task["final_video_path"] == str(final_path)
    assert [stage["status"] for stage in task["stages"]] == ["succeeded"] * 11
    assert [stage["progress"] for stage in task["stages"]] == [100] * 11


def test_pipeline_auto_queues_bilibili_upload_on_success(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    task_id = database.create_task(
        "https://www.youtube.com/watch?v=autobilique",
        task_id="autobilique",
        auto_bilibili_upload=True,
    )
    final_path = tmp_path / "video_final.mp4"
    final_path.write_bytes(b"mp4")
    enqueued: list[str] = []
    monkeypatch.setattr(bilibili_uploads, "enqueue", lambda job_id: enqueued.append(job_id))

    for name in ("_download", "_separate", "_asr", "_asr_fix", "_translate", "_split_audio", "_tts", "_merge_audio", "_concat_video", "_trim_video"):
        monkeypatch.setattr(PipelineRunner, name, _noop_stage)

    def merge_video(self, task):
        database.update_task(task_id, title="自动发布标题")
        self.artifacts.final_video = final_path

    monkeypatch.setattr(PipelineRunner, "_merge_video", merge_video)

    PipelineRunner(task_id).run()

    assert len(enqueued) == 1
    job = database.get_bilibili_upload_job(enqueued[0])
    assert job["task_id"] == task_id
    assert job["title"] == "自动发布标题"
    assert job["publish_mode"] == "now"
    assert job["status"] == "queued"


def test_pipeline_auto_uses_saved_bilibili_upload_settings(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    task_id = database.create_task(
        "https://www.youtube.com/watch?v=autobiliset",
        task_id="autobiliset",
        auto_bilibili_upload=True,
        bilibili_upload_title="预设投稿标题",
        bilibili_publish_mode="scheduled",
        bilibili_dtime="2999-07-01T18:00",
        bilibili_upload_after="2999-07-01T01:00:00+00:00",
    )
    database.update_task(
        task_id,
        bilibili_upload_description="自动投稿简介",
        bilibili_upload_tags="自动,标签",
        bilibili_upload_tid=171,
        bilibili_topic_name="宝可梦集结",
    )
    final_path = tmp_path / "video_final.mp4"
    final_path.write_bytes(b"mp4")
    enqueued: list[str] = []
    monkeypatch.setattr(bilibili_uploads, "enqueue", lambda job_id: enqueued.append(job_id))

    for name in ("_download", "_separate", "_asr", "_asr_fix", "_translate", "_split_audio", "_tts", "_merge_audio", "_concat_video", "_trim_video"):
        monkeypatch.setattr(PipelineRunner, name, _noop_stage)

    def merge_video(self, task):
        self.artifacts.final_video = final_path

    monkeypatch.setattr(PipelineRunner, "_merge_video", merge_video)

    PipelineRunner(task_id).run()

    assert enqueued == []
    job = database.get_active_bilibili_upload_job(task_id)
    assert job is not None
    assert job["title"] == "预设投稿标题"
    assert job["description"] == "自动投稿简介"
    assert job["tags"] == "自动,标签"
    assert job["tid"] == 171
    assert job["topic_name"] == "宝可梦集结"
    assert job["publish_mode"] == "scheduled"
    assert job["dtime"] == "2999-07-01T18:00"
    assert job["upload_after"] == "2999-07-01T01:00:00+00:00"
    assert job["status"] == "scheduled"


def test_download_applies_author_topic_and_collection_defaults_to_auto_upload(monkeypatch, tmp_path):
    from backend.app.adapters import ytdlp

    configure_db(monkeypatch, tmp_path)
    database.save_bilibili_settings(
        "",
        "",
        author_config_json=json.dumps({
            "WolfeyVGC": {
                "topic_name": "宝可梦集结",
                "topic_id": 1245603,
                "topic_mission_id": 42,
                "season_id": 123,
                "section_id": 456,
            }
        }),
    )
    task_id = database.create_task(
        "https://www.youtube.com/watch?v=authorcfg01",
        task_id="authorcfg01",
        auto_bilibili_upload=True,
    )
    session = tmp_path / "session"
    (session / "media").mkdir(parents=True)
    monkeypatch.setattr(
        ytdlp,
        "download_video",
        lambda *args: (session, {"title": "测试视频", "uploader": "WolfeyVGC"}),
    )
    enqueued: list[str] = []
    monkeypatch.setattr(bilibili_uploads, "enqueue", lambda job_id: enqueued.append(job_id))

    runner = PipelineRunner(task_id)
    runner._download(database.get_task(task_id))
    task = database.get_task(task_id)
    assert task["bilibili_topic_name"] == "宝可梦集结"
    assert task["bilibili_topic_id"] == 1245603
    assert task["bilibili_topic_mission_id"] == 42
    assert task["bilibili_season_id"] == 123
    assert task["bilibili_section_id"] == 456

    runner._maybe_enqueue_auto_bilibili_upload()
    assert len(enqueued) == 1
    job = database.get_bilibili_upload_job(enqueued[0])
    assert job["topic_name"] == "宝可梦集结"
    assert job["topic_id"] == 1245603
    assert job["topic_mission_id"] == 42
    assert job["season_id"] == 123
    assert job["section_id"] == 456

    override_id = database.create_task(
        "https://www.youtube.com/watch?v=authorcfg02",
        task_id="authorcfg02",
    )
    database.update_task(
        override_id,
        bilibili_topic_name="手动话题",
        bilibili_topic_id=99,
        bilibili_season_id=7,
        bilibili_section_id=8,
    )
    PipelineRunner(override_id)._download(database.get_task(override_id))
    overridden = database.get_task(override_id)
    assert overridden["bilibili_topic_name"] == "手动话题"
    assert overridden["bilibili_topic_id"] == 99
    assert overridden["bilibili_season_id"] == 7
    assert overridden["bilibili_section_id"] == 8


def test_pipeline_skips_already_succeeded_stages(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    task_id = database.create_task("https://www.youtube.com/watch?v=resumevidxxx", task_id="resumevidxxx")
    session = _cached_session(tmp_path)
    final_path = session / "media" / "video_final.mp4"
    database.update_task(task_id, session_path=str(session))

    for name in ("download", "separate", "asr"):
        database.update_stage(task_id, name, status="succeeded", completed_at=database.now_iso())

    visited: list[str] = []
    for stage_name in ("_download", "_separate", "_asr"):
        def make_handler(name=stage_name):
            def handler(self, task):
                visited.append(name)
            return handler
        monkeypatch.setattr(PipelineRunner, stage_name, make_handler())

    def asr_fix(self, task):
        visited.append("_asr_fix")
        assert self.artifacts.session == session
        assert self.artifacts.video_file == session / "media" / "video_source.mp4"
        assert self.artifacts.vocals_file == session / "media" / "audio_vocals.wav"
        assert self.artifacts.bgm_file == session / "media" / "audio_bgm.wav"
        assert self.artifacts.asr_file == session / "metadata" / "asr.json"
        self.artifacts.asr_fixed_file = session / "metadata" / "asr_fixed.json"

    def translate(self, task):
        visited.append("_translate")
        self.artifacts.translation_file = session / "metadata" / "translation.zh.json"

    def split_audio(self, task):
        visited.append("_split_audio")
        self.artifacts.vocals_dir = session / "segments" / "vocals"

    def tts(self, task):
        visited.append("_tts")
        self.artifacts.tts_dir = session / "segments" / "tts"

    def merge_audio(self, task):
        visited.append("_merge_audio")
        self.artifacts.dubbing_file = session / "tmp" / "audio_dubbing.wav"
        self.artifacts.timings_file = session / "metadata" / "timings.json"

    def merge_video(self, task):
        visited.append("_merge_video")
        self.artifacts.final_video = final_path

    def concat_video(self, task):
        visited.append("_concat_video")
        self.artifacts.concatenated_video = final_path

    monkeypatch.setattr(PipelineRunner, "_asr_fix", asr_fix)
    monkeypatch.setattr(PipelineRunner, "_translate", translate)
    monkeypatch.setattr(PipelineRunner, "_split_audio", split_audio)
    monkeypatch.setattr(PipelineRunner, "_tts", tts)
    monkeypatch.setattr(PipelineRunner, "_merge_audio", merge_audio)
    monkeypatch.setattr(PipelineRunner, "_merge_video", merge_video)
    monkeypatch.setattr(PipelineRunner, "_concat_video", concat_video)

    PipelineRunner(task_id).run()

    assert visited == [
        "_asr_fix", "_translate", "_split_audio", "_tts", "_merge_audio", "_merge_video", "_concat_video",
    ]
    task = database.get_task(task_id)
    assert task["status"] == "succeeded"
    assert task["final_video_path"] == str(final_path)


def test_pipeline_fails_when_succeeded_stage_cache_is_missing(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    task_id = database.create_task("https://www.youtube.com/watch?v=missingvidx", task_id="missingvidx")
    session = tmp_path / "session"
    session.mkdir()
    database.update_task(task_id, session_path=str(session))
    database.update_stage(task_id, "download", status="succeeded", completed_at=database.now_iso())

    visited: list[str] = []

    def download(self, task):
        visited.append("_download")

    monkeypatch.setattr(PipelineRunner, "_download", download)

    PipelineRunner(task_id).run()
    task = database.get_task(task_id)
    stages = {stage["name"]: stage for stage in task["stages"]}

    assert visited == []
    assert task["status"] == "failed"
    assert task["error_message"].startswith("Missing cached pipeline artifact: video_file")
    assert stages["download"]["status"] == "failed"
    assert stages["separate"]["status"] == "pending"


def test_pipeline_failure_stops_following_stages(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    task_id = database.create_task("https://www.youtube.com/watch?v=abcdefghijk")

    monkeypatch.setattr(PipelineRunner, "_download", _noop_stage)
    monkeypatch.setattr(PipelineRunner, "_separate", _noop_stage)

    def fail_asr(self, task):
        raise RuntimeError("asr exploded")

    monkeypatch.setattr(PipelineRunner, "_asr", fail_asr)

    PipelineRunner(task_id).run()
    task = database.get_task(task_id)
    stages = {stage["name"]: stage for stage in task["stages"]}

    assert task["status"] == "failed"
    assert stages["asr"]["status"] == "failed"
    assert stages["asr"]["progress"] == 0
    assert stages["translate"]["status"] == "pending"
    assert task["error_message"] == "asr exploded"


def test_pipeline_sends_success_notification(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    task_id = database.create_task("https://www.youtube.com/watch?v=successmail", task_id="successmail")
    final_path = tmp_path / "video_final.mp4"
    final_path.write_bytes(b"mp4")

    for name in ("_download", "_separate", "_asr", "_asr_fix", "_translate", "_split_audio", "_tts", "_merge_audio", "_concat_video", "_trim_video"):
        monkeypatch.setattr(PipelineRunner, name, _noop_stage)

    def merge_video(self, task):
        self.artifacts.final_video = final_path

    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(PipelineRunner, "_merge_video", merge_video)
    monkeypatch.setattr(
        "backend.app.notifications.send_task_notification",
        lambda task, event: sent.append((task["id"], event)) or "sent",
    )

    PipelineRunner(task_id).run()

    assert sent == [(task_id, "succeeded")]


def test_pipeline_notification_error_does_not_change_task_status(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    task_id = database.create_task("https://www.youtube.com/watch?v=mailerrtask", task_id="mailerrtask")
    final_path = tmp_path / "video_final.mp4"
    final_path.write_bytes(b"mp4")

    for name in ("_download", "_separate", "_asr", "_asr_fix", "_translate", "_split_audio", "_tts", "_merge_audio", "_concat_video", "_trim_video"):
        monkeypatch.setattr(PipelineRunner, name, _noop_stage)

    def merge_video(self, task):
        self.artifacts.final_video = final_path

    def fail_notification(task, event):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(PipelineRunner, "_merge_video", merge_video)
    monkeypatch.setattr("backend.app.notifications.send_task_notification", fail_notification)

    PipelineRunner(task_id).run()
    task = database.get_task(task_id)
    log_content = database.log_path(task_id).read_text(encoding="utf-8")

    assert task["status"] == "succeeded"
    assert "Mail notification failed: smtp down" in log_content


def test_pipeline_sends_failure_notification(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    task_id = database.create_task("https://www.youtube.com/watch?v=failedmailx", task_id="failedmailx")
    monkeypatch.setattr(PipelineRunner, "_download", _noop_stage)

    def fail_separate(self, task):
        raise RuntimeError("separate exploded")

    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(PipelineRunner, "_separate", fail_separate)
    monkeypatch.setattr(
        "backend.app.notifications.send_task_notification",
        lambda task, event: sent.append((task["id"], event, task["error_message"])) or "sent",
    )

    PipelineRunner(task_id).run()

    assert sent == [(task_id, "failed", "separate exploded")]


def test_stage_progress_is_throttled(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    task_id = database.create_task("https://www.youtube.com/watch?v=progressidx")
    runner = PipelineRunner(task_id)
    ticks = iter([0.0, 0.5, 2.1])
    monkeypatch.setattr(pipeline, "monotonic", lambda: next(ticks))

    runner.stage_progress("tts", 10, "Prepared 1/10 TTS clips")
    runner.stage_progress("tts", 20, "Prepared 2/10 TTS clips")
    runner.stage_progress("tts", 30, "Prepared 3/10 TTS clips")

    stage = {entry["name"]: entry for entry in database.get_task(task_id)["stages"]}["tts"]
    assert stage["progress"] == 30
    assert stage["last_message"] == "Prepared 3/10 TTS clips"


def test_trim_video_without_intervals_keeps_original(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    task_id = database.create_task("https://www.youtube.com/watch?v=defaulttrim1", task_id="defaulttrim1")
    session = tmp_path / "session"
    media = session / "media"
    media.mkdir(parents=True)
    final_video = media / "video_final.mp4"
    final_video.write_bytes(b"mp4")
    trimmed_video = media / "video_final_trimmed.mp4"

    def fake_trim_video(input_video, cut_intervals, trim_session):
        raise AssertionError("trim_video should not run without cut intervals")

    monkeypatch.setattr("backend.app.adapters.ffmpeg.trim_video", fake_trim_video)

    runner = PipelineRunner(task_id)
    runner.artifacts.session = session
    runner.artifacts.final_video = final_video
    runner._trim_video(database.get_task(task_id))

    intervals_file = session / "metadata" / "cut_intervals.json"
    assert not intervals_file.exists()
    assert database.get_task_cut_intervals(task_id) is None
    assert not trimmed_video.exists()
    assert runner.artifacts.trimmed_video == final_video


def test_concat_video_uses_source_when_final_video_is_missing(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    task_id = database.create_task("https://www.youtube.com/watch?v=concatfb001", task_id="concatfallback")
    session = tmp_path / "session"
    media = session / "media"
    media.mkdir(parents=True)
    source_video = media / "video_source.mp4"
    source_video.write_bytes(b"source")
    concatenated = media / "video_final_with_cta.mp4"
    captured: dict[str, Path] = {}

    def fake_prepend_cta_clip(input_video, concat_session):
        captured["input_video"] = input_video
        captured["session"] = concat_session
        concatenated.write_bytes(b"concat")
        return concatenated

    monkeypatch.setattr("backend.app.adapters.ffmpeg.prepend_cta_clip", fake_prepend_cta_clip)

    runner = PipelineRunner(task_id)
    runner.artifacts.session = session
    runner._concat_video(database.get_task(task_id))

    assert captured == {"input_video": source_video, "session": session}
    assert runner.artifacts.video_file == source_video
    assert runner.artifacts.concatenated_video == concatenated


def test_concat_video_skips_cta_for_zh_to_en(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    task_id = database.create_task("https://www.bilibili.com/video/BV1concatSkip", task_id="concatzh2en")
    session = tmp_path / "session"
    media = session / "media"
    media.mkdir(parents=True)
    final_video = media / "video_final.mp4"
    final_video.write_bytes(b"final")

    def fail_prepend_cta_clip(input_video, concat_session):
        raise AssertionError("prepend_cta_clip should not run for zh->en")

    monkeypatch.setattr("backend.app.adapters.ffmpeg.prepend_cta_clip", fail_prepend_cta_clip)

    runner = PipelineRunner(task_id)
    runner.artifacts.session = session
    runner.artifacts.final_video = final_video
    runner._concat_video(database.get_task(task_id))

    assert runner.artifacts.concatenated_video == final_video
    assert not (media / "video_final_with_cta.mp4").exists()


def test_restore_cached_concat_stage_allows_missing_cta_for_zh_to_en(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    task_id = database.create_task("https://www.bilibili.com/video/BV1xx411c7mD", task_id="BV1xx411c7mD")
    session = tmp_path / "session"
    media = session / "media"
    media.mkdir(parents=True)
    final_video = media / "video_final.mp4"
    final_video.write_bytes(b"final")
    database.update_task(task_id, session_path=str(session))

    runner = PipelineRunner(task_id)
    runner._restore_cached_stage("concat_video", database.get_task(task_id))

    assert runner.artifacts.final_video == final_video
    assert runner.artifacts.concatenated_video == final_video


def test_pipeline_manual_pauses_after_each_stage(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    task_id = database.create_task(
        "https://www.youtube.com/watch?v=manualstepx",
        task_id="manualstepx",
        execution_mode="manual",
    )

    def download(self, task):
        session = tmp_path / "session"
        media = session / "media"
        media.mkdir(parents=True)
        video = media / "video_source.mp4"
        video.write_bytes(b"video")
        self.artifacts.session = session
        self.artifacts.video_file = video
        database.update_task(self.task_id, session_path=str(session), title="manual")

    monkeypatch.setattr(PipelineRunner, "_download", download)

    def fail_later(name):
        def handler(self, task):
            raise AssertionError(f"unexpected stage {name}")

        return handler

    for name in ("_separate", "_asr", "_asr_fix", "_translate", "_split_audio", "_tts", "_merge_audio", "_merge_video", "_concat_video", "_trim_video"):
        monkeypatch.setattr(PipelineRunner, name, fail_later(name))

    PipelineRunner(task_id).run()
    task = database.get_task(task_id)
    assert task["status"] == "paused"
    assert task["stages"][0]["status"] == "succeeded"
    assert task["stages"][1]["status"] == "pending"

    def separate(self, task):
        vocals = self.artifacts.session / "media" / "audio_vocals.wav"
        bgm = self.artifacts.session / "media" / "audio_bgm.wav"
        vocals.write_bytes(b"v")
        bgm.write_bytes(b"b")
        self.artifacts.vocals_file = vocals
        self.artifacts.bgm_file = bgm

    monkeypatch.setattr(PipelineRunner, "_separate", separate)
    database.queue_task_for_continue(task_id)
    PipelineRunner(task_id).run()
    task = database.get_task(task_id)
    assert task["status"] == "paused"
    assert task["stages"][1]["status"] == "succeeded"
    assert task["stages"][2]["status"] == "pending"


def test_pipeline_manual_switch_to_auto_runs_remaining_stages(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    task_id = database.create_task(
        "https://www.youtube.com/watch?v=manual2auto",
        task_id="manual2auto",
        execution_mode="manual",
    )
    final_path = tmp_path / "video_final.mp4"

    def download(self, task):
        session = tmp_path / "session"
        media = session / "media"
        media.mkdir(parents=True)
        video = media / "video_source.mp4"
        video.write_bytes(b"video")
        self.artifacts.session = session
        self.artifacts.video_file = video
        database.update_task(self.task_id, session_path=str(session), title="manual2auto")

    monkeypatch.setattr(PipelineRunner, "_download", download)

    for name in ("_separate", "_asr", "_asr_fix", "_translate", "_split_audio", "_tts", "_merge_audio", "_concat_video", "_trim_video"):
        monkeypatch.setattr(PipelineRunner, name, _noop_stage)

    def merge_video(self, task):
        self.artifacts.final_video = final_path

    monkeypatch.setattr(PipelineRunner, "_merge_video", merge_video)

    PipelineRunner(task_id).run()
    task = database.get_task(task_id)
    assert task["status"] == "paused"
    assert task["stages"][0]["status"] == "succeeded"

    database.update_task(task_id, execution_mode="auto")
    database.queue_task_for_continue(task_id)
    PipelineRunner(task_id).run()
    task = database.get_task(task_id)
    assert task["status"] == "succeeded"
    assert task["execution_mode"] == "auto"
    assert all(stage["status"] == "succeeded" for stage in task["stages"])


def test_pipeline_uses_uploaded_srt_and_skips_model_stages(monkeypatch, tmp_path):
    configure_db(monkeypatch, tmp_path)
    monkeypatch.setattr(pipeline, "WORKFOLDER", tmp_path)
    task_id = "local-subtitle-task"
    task_url = f"local://upload/{task_id}?direction=en-zh&filename=clip.mp4"
    task_id = database.create_task(task_url, task_id=task_id)
    session = tmp_path / "session"
    for directory in ("media", "metadata", "segments/vocals", "segments/tts", "tmp"):
        (session / directory).mkdir(parents=True, exist_ok=True)
    subtitle_file = tmp_path / "uploaded" / "clip.zh.srt"
    subtitle_file.parent.mkdir(parents=True)
    subtitle_file.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n你好\n\n"
        "2\n00:00:01,200 --> 00:00:02,000\n世界\n",
        encoding="utf-8",
    )
    (session / "metadata" / "local_info.json").write_text(
        json.dumps({"subtitle_path": str(subtitle_file)}),
        encoding="utf-8",
    )

    def fail_model_call(*args, **kwargs):
        raise AssertionError("model stage should be skipped")

    whisper_module = types.ModuleType("backend.app.adapters.whisper_asr")
    whisper_module.recognize_speech = fail_model_call
    fixer_module = types.ModuleType("backend.app.adapters.asr_sentence_fixer")
    fixer_module.fix_asr_sentences = fail_model_call
    translate_module = types.ModuleType("backend.app.adapters.openai_translate")
    translate_module.translate_asr = fail_model_call
    monkeypatch.setitem(sys.modules, "backend.app.adapters.whisper_asr", whisper_module)
    monkeypatch.setitem(sys.modules, "backend.app.adapters.asr_sentence_fixer", fixer_module)
    monkeypatch.setitem(sys.modules, "backend.app.adapters.openai_translate", translate_module)

    def download(self, task):
        self.artifacts.session = session
        self.artifacts.video_file = session / "media" / "video_source.mp4"
        self.artifacts.video_file.write_bytes(b"mp4")
        database.update_task(self.task_id, session_path=str(session), title="clip")

    def separate(self, task):
        self.artifacts.vocals_file = session / "media" / "audio_vocals.wav"
        self.artifacts.bgm_file = session / "media" / "audio_bgm.wav"
        self.artifacts.vocals_file.write_bytes(b"vocals")
        self.artifacts.bgm_file.write_bytes(b"bgm")

    def split_audio(self, task):
        self.artifacts.vocals_dir = session / "segments" / "vocals"

    def tts(self, task):
        self.artifacts.tts_dir = session / "segments" / "tts"

    def merge_audio(self, task):
        self.artifacts.dubbing_file = session / "tmp" / "audio_dubbing.wav"
        self.artifacts.timings_file = session / "metadata" / "timings.json"
        self.artifacts.dubbing_file.write_bytes(b"dubbing")
        self.artifacts.timings_file.write_text(
            (session / "metadata" / "translation.zh.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    def merge_video(self, task):
        self.artifacts.final_video = session / "media" / "video_final.mp4"
        self.artifacts.final_video.write_bytes(b"mp4")

    monkeypatch.setattr(PipelineRunner, "_download", download)
    monkeypatch.setattr(PipelineRunner, "_separate", separate)
    monkeypatch.setattr(PipelineRunner, "_split_audio", split_audio)
    monkeypatch.setattr(PipelineRunner, "_tts", tts)
    monkeypatch.setattr(PipelineRunner, "_merge_audio", merge_audio)
    monkeypatch.setattr(PipelineRunner, "_merge_video", merge_video)
    monkeypatch.setattr(PipelineRunner, "_concat_video", _noop_stage)
    monkeypatch.setattr(PipelineRunner, "_trim_video", _noop_stage)

    PipelineRunner(task_id).run()

    task = database.get_task(task_id)
    translation_file = session / "metadata" / "translation.zh.json"
    translation = json.loads(translation_file.read_text(encoding="utf-8"))["translation"]
    log_content = database.log_path(task_id).read_text(encoding="utf-8")
    assert task["status"] == "succeeded"
    assert [item["dst"] for item in translation] == ["你好", "世界"]
    assert "skipped Whisper" in log_content
    assert "skipped sentence splitting" in log_content
    assert "skipped OpenAI translation" in log_content

