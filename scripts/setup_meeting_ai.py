"""Explicit local-model installation and diagnostic commands; never reads recordings."""

import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import wave

import httpx
from app.services.meeting_ai import (
    configuration,
    readiness,
    LocalModels,
    ProcessingError,
)
from app.services.meeting_media import audio_for_model


def check_transcription():
    """Exercise the real local runtime on generated silence, never on a meeting."""
    print("Проверка распознавания на одной секунде искусственной тишины.")
    print("База и записи встреч не читаются; модели не скачиваются.")
    stream = io.BytesIO()
    with wave.open(stream, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(b"\0\0" * 16000)
    stage = "извлечение звука FFmpeg"
    try:
        with audio_for_model(stream.getvalue()) as path:
            stage = "загрузка локальной модели и распознавание"
            LocalModels().transcribe(path, lambda: None)
    except Exception as exc:
        print(f"Проверка не пройдена. Этап: {stage}.")
        if isinstance(exc, ProcessingError):
            from app.services.meetings import ERRORS

            print(
                f"Код: {exc.code}. {ERRORS.get(exc.code, 'Проверьте настройку модели.')}"
            )
        else:
            # Only this generated input is used: a backend error here cannot
            # contain meeting contents. Never use this output for real uploads.
            print(f"Техническая причина: {type(exc).__name__}: {exc}")
        return 1
    print("FFmpeg, локальная модель и короткий запуск распознавания работают.")
    print("Это проверка запуска, а не качества речи или обработки длинной встречи.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download-whisper", action="store_true")
    parser.add_argument("--pull-summary", action="store_true")
    parser.add_argument(
        "--check-transcription",
        action="store_true",
        help="Test local ASR on generated silence; never reads meetings or downloads models",
    )
    args = parser.parse_args(argv)
    if args.check_transcription:
        if args.download_whisper or args.pull_summary:
            parser.error(
                "Проверку распознавания запускают отдельно от загрузки моделей."
            )
        raise SystemExit(check_transcription())
    conf = configuration()
    if args.download_whisper:
        try:
            from huggingface_hub import HfApi, snapshot_download
        except ImportError:
            raise SystemExit("Сначала установите requirements-meetings.txt.")
        repo = "Systran/faster-whisper-small"
        info = HfApi().model_info(repo, files_metadata=True)
        model = next(s for s in info.siblings if s.rfilename == "model.bin")
        expected = (
            getattr(model.lfs, "sha256", None)
            if not isinstance(model.lfs, dict)
            else model.lfs.get("sha256")
        )
        if not expected:
            raise SystemExit(
                "Источник не сообщил контрольную сумму весов; загрузка не активируется."
            )
        folder = Path(conf["asr_path"])
        folder.mkdir(parents=True, exist_ok=True)
        snapshot_download(
            repo,
            revision=info.sha,
            local_dir=folder,
            allow_patterns=[
                "model.bin",
                "config.json",
                "tokenizer.json",
                "vocabulary.*",
                "preprocessor_config.json",
            ],
        )
        with (folder / "model.bin").open("rb") as weights:
            digest = hashlib.file_digest(weights, "sha256").hexdigest()
        if digest != expected:
            raise SystemExit(
                "Контрольная сумма модели не совпала. Не запускайте распознавание; скачайте модель заново из официального репозитория."
            )
        if not any(
            (folder / name).is_file() for name in ("vocabulary.json", "vocabulary.txt")
        ):
            raise SystemExit(
                "Не получен словарь модели vocabulary.json или vocabulary.txt. Загрузка не завершена."
            )
        (folder / "compass-model-source.json").write_text(
            json.dumps(dict(repo=repo, revision=info.sha, sha256=digest), indent=2),
            encoding="utf-8",
        )
        print(
            "Локальная модель речи скачана; SHA-256 весов совпала с метаданными источника."
        )
    if args.pull_summary:
        if not shutil.which("ollama"):
            raise SystemExit("Установите Ollama и откройте новый Terminal.")
        subprocess.run(["ollama", "pull", conf["model"]], check=True)
    status = readiness()
    try:
        import faster_whisper

        status["recognizer"] = True
    except (ImportError, RuntimeError, OSError):
        status["recognizer"] = False
    for label, key in [
        ("FFmpeg", "ffmpeg"),
        ("Пакет Faster Whisper", "recognizer"),
        ("Файлы модели речи", "model"),
    ]:
        print(label + ": " + ("готово" if status[key] else "требуется настройка"))
    try:
        response = httpx.get(
            conf["ollama_url"] + "/api/tags",
            trust_env=False,
            follow_redirects=False,
            timeout=5,
        )
        response.raise_for_status()
        names = {m["name"] for m in response.json()["models"]}
        ready = conf["model"] in names
    except (httpx.HTTPError, ValueError, KeyError):
        ready = False
    print(
        "Ollama / "
        + conf["model"]
        + ": "
        + ("готово" if ready else "нужны запущенная Ollama и загруженная модель")
    )
    if not ready or not all(status[k] for k in ("ffmpeg", "recognizer", "model")):
        raise SystemExit(1)
    print(
        "Локальные компоненты доступны. Это проверка настройки, а не качества клинической расшифровки."
    )


if __name__ == "__main__":
    main()
