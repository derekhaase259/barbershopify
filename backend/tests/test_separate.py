"""Vocal isolation is an optional booster — it may never break analysis.
Every failure path must return None so the pipeline falls back to the mix."""
from barbershop.analysis import separate


def test_isolate_vocal_failsoft_on_error(monkeypatch):
    def boom():
        raise RuntimeError("model download exploded")

    monkeypatch.setattr(separate, "_load_model", boom)
    assert separate.isolate_vocal("/nonexistent.wav", 22050) is None


def test_isolate_vocal_failsoft_on_bad_path(monkeypatch):
    # a real model load but an unreadable file must still degrade to None,
    # not raise — guard without paying for the model download in CI
    sentinel = object()

    class FakeModel:
        samplerate = 44100
        audio_channels = 2
        sources = ["drums", "bass", "other", "vocals"]

    monkeypatch.setattr(separate, "_load_model", lambda: FakeModel())
    assert separate.isolate_vocal("/definitely/not/here.wav", 22050) is None
