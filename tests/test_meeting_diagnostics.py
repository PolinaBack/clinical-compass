"""Diagnostic uses generated silence and never opens an uploaded recording."""

import wave
import fnmatch
import hashlib
import json
import sys
from types import SimpleNamespace

import pytest

from scripts import setup_meeting_ai as script
from app.services.meeting_ai import ProcessingError
from app.services import meeting_ai


def test_diagnostic_runs_on_silence_without_printing_transcript(monkeypatch, capsys):
    class Probe:
        def transcribe(self, path, checkpoint):
            with wave.open(str(path), "rb") as audio:
                assert audio.getframerate() == 16000
                assert audio.getnchannels() == 1
                assert audio.readframes(audio.getnframes()) == b"\0\0" * 16000
            return [dict(text="DO NOT PRINT MODEL OUTPUT")], "synthetic test"

    monkeypatch.setattr(script, "LocalModels", Probe)
    with pytest.raises(SystemExit) as error:
        script.main(["--check-transcription"])
    assert error.value.code == 0
    output = capsys.readouterr().out
    assert "короткий запуск распознавания работают" in output
    assert "DO NOT PRINT" not in output


@pytest.mark.parametrize("known", [True, False])
def test_diagnostic_reports_model_error_on_generated_input(monkeypatch, capsys, known):
    class Broken:
        def transcribe(self, path, checkpoint):
            if known:
                raise ProcessingError("asr_model_missing")
            raise RuntimeError("SYNTHETIC BACKEND FAILURE")

    monkeypatch.setattr(script, "LocalModels", Broken)
    with pytest.raises(SystemExit) as error:
        script.main(["--check-transcription"])
    assert error.value.code == 1
    output = capsys.readouterr().out
    assert "Проверка не пройдена" in output
    assert (
        "Локальная модель Whisper отсутствует"
        if known
        else "RuntimeError: SYNTHETIC BACKEND FAILURE"
    ) in output


def test_diagnostic_cannot_be_combined_with_downloads(monkeypatch):
    def forbidden():
        pytest.fail("No model operation may start with conflicting CLI options")

    monkeypatch.setattr(script, "check_transcription", forbidden)
    with pytest.raises(SystemExit) as error:
        script.main(["--check-transcription", "--download-whisper"])
    assert error.value.code == 2


@pytest.mark.parametrize("filename", ["vocabulary.txt", "vocabulary.json"])
def test_model_readiness_requires_either_supported_vocabulary(
    tmp_path, monkeypatch, filename
):
    monkeypatch.setenv("COMPASS_WHISPER_MODEL", str(tmp_path))
    for name in (
        "model.bin",
        "config.json",
        "tokenizer.json",
        "compass-model-source.json",
    ):
        (tmp_path / name).write_text("synthetic")
    assert not meeting_ai.readiness()["model"]
    (tmp_path / filename).write_text("synthetic vocabulary")
    assert meeting_ai.readiness()["model"]


def test_download_includes_legacy_text_vocabulary(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPASS_WHISPER_MODEL", str(tmp_path))
    weights = b"synthetic weights"
    digest = hashlib.sha256(weights).hexdigest()

    class Source:
        def model_info(self, repo, files_metadata):
            assert repo == "Systran/faster-whisper-small" and files_metadata
            return SimpleNamespace(
                sha="synthetic-revision",
                siblings=[
                    SimpleNamespace(rfilename="model.bin", lfs={"sha256": digest})
                ],
            )

    def snapshot(repo, revision, local_dir, allow_patterns):
        assert revision == "synthetic-revision"
        for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"):
            if any(fnmatch.fnmatch(name, pattern) for pattern in allow_patterns):
                (local_dir / name).write_bytes(
                    weights if name == "model.bin" else b"synthetic"
                )

    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(HfApi=Source, snapshot_download=snapshot),
    )
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace())
    monkeypatch.setattr(
        script,
        "readiness",
        lambda: dict(
            ffmpeg=True, recognizer=True, model=(tmp_path / "vocabulary.txt").is_file()
        ),
    )
    monkeypatch.setattr(
        script.httpx,
        "get",
        lambda *a, **kw: SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: dict(models=[dict(name="qwen2.5:3b")]),
        ),
    )
    script.main(["--download-whisper"])
    assert (tmp_path / "vocabulary.txt").is_file()
    assert (
        json.loads((tmp_path / "compass-model-source.json").read_text())["sha256"]
        == digest
    )
