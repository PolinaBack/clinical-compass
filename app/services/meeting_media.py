"""Probe local uploads; no URLs, network protocols or user-controlled shell code."""

import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from contextlib import contextmanager

from fastapi import HTTPException

MAX_BYTES = 200 * 1024 * 1024
MAX_SECONDS = 7200


@contextmanager
def media_file(content, suffix=""):
    with tempfile.TemporaryDirectory(prefix="compass-meeting-") as folder:
        path = Path(folder) / ("recording" + suffix)
        path.write_bytes(content)
        os.chmod(path, 0o600)
        yield path


def inspect_media(content):
    if not 44 <= len(content) <= MAX_BYTES:
        raise HTTPException(
            422, "Нужна запись до 200 МБ; пустые файлы не поддерживаются."
        )
    if not shutil.which("ffprobe"):
        raise HTTPException(
            503,
            "Не найден FFmpeg/ffprobe. Установите FFmpeg по инструкции настройки встреч.",
        )
    # Whitelist containers before probing; never open playlists or remote references.
    if content[:4] == b"RIFF" and content[8:12] == b"WAVE":
        suffix = ".wav"
    elif content[:4] == b"\x1aE\xdf\xa3":
        suffix = ".webm"
    elif content[4:8] == b"ftyp":
        suffix = ".mp4"
    elif content[:3] == b"ID3" or content[0] == 255 and content[1] & 224 == 224:
        suffix = ".mp3"
    elif content[:4] == b"OggS":
        suffix = ".ogg"
    elif content[:4] == b"fLaC":
        suffix = ".flac"
    else:
        raise HTTPException(
            422,
            "Поддерживаются WAV, MP3, FLAC, OGG, WebM, MP4/M4A; фактическое содержимое проверяется сервером.",
        )
    try:
        with media_file(content, suffix) as path:
            proc = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-protocol_whitelist",
                    "file",
                    "-show_streams",
                    "-show_format",
                    "-of",
                    "json",
                    str(path),
                ],
                capture_output=True,
                timeout=25,
                check=True,
            )
            meta = json.loads(proc.stdout)
        streams = meta.get("streams", [])
        duration = float(meta["format"]["duration"])
        if (
            not math.isfinite(duration)
            or not 0 < duration <= MAX_SECONDS
            or not any(s.get("codec_type") == "audio" for s in streams)
        ):
            raise ValueError()
        video = any(s.get("codec_type") == "video" for s in streams)
        fmt = meta["format"]["format_name"]
        if any(x in fmt for x in ["mov", "mp4"]):
            mime = "video/mp4" if video else "audio/mp4"
        elif "matroska" in fmt or "webm" in fmt:
            mime = "video/webm" if video else "audio/webm"
        else:
            mime = {
                "mp3": "audio/mpeg",
                "wav": "audio/wav",
                "ogg": "audio/ogg",
                "flac": "audio/flac",
            }.get(fmt)
        if not mime:
            raise ValueError()
        return dict(
            mime=mime, duration=duration, sha256=hashlib.sha256(content).hexdigest()
        )
    except (subprocess.SubprocessError, ValueError, KeyError, TypeError):
        raise HTTPException(
            422, "Файл не прочитан, в нём нет звука или длительность превышает 2 часа."
        )


@contextmanager
def audio_for_model(content):
    if not shutil.which("ffmpeg"):
        raise RuntimeError("media_unavailable")
    with media_file(content) as path:
        target = path.with_suffix(".wav")
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-protocol_whitelist",
                    "file",
                    "-i",
                    str(path),
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-t",
                    str(MAX_SECONDS),
                    str(target),
                ],
                check=True,
                capture_output=True,
                timeout=120,
            )
            os.chmod(target, 0o600)
            yield target
        except subprocess.SubprocessError:
            raise RuntimeError("media_decode_failed") from None
