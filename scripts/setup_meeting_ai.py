"""Explicit local-model installation and diagnostic commands; never reads recordings."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import httpx
from app.services.meeting_ai import configuration, readiness


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download-whisper", action="store_true")
    parser.add_argument("--pull-summary", action="store_true")
    args = parser.parse_args()
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
                "vocabulary.json",
                "preprocessor_config.json",
            ],
        )
        with (folder / "model.bin").open("rb") as weights:
            digest = hashlib.file_digest(weights, "sha256").hexdigest()
        if digest != expected:
            raise SystemExit(
                "Контрольная сумма модели не совпала. Не запускайте распознавание; скачайте модель заново из официального репозитория."
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
        status['recognizer']=True
    except (ImportError,RuntimeError,OSError):
        status['recognizer']=False
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
