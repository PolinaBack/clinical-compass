"""Real optional decoder integration: no model weights or patient recordings."""

import importlib.util
import wave

import pytest


def test_faster_whisper_decodes_generated_wav(tmp_path):
    if importlib.util.find_spec("faster_whisper") is None:
        pytest.skip("Install requirements-meetings.txt for the optional audio runtime")
    from faster_whisper.audio import decode_audio

    path = tmp_path / "synthetic-silence.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0" * 16000)
    samples = decode_audio(str(path), sampling_rate=16000)
    assert samples.shape == (16000,)
    assert str(samples.dtype) == "float32"
    assert not samples.any()
