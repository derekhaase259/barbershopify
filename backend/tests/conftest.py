"""Song lookup is stubbed to 'no match' for the whole suite, so pytest
never touches the network even on machines with fpcalc installed. Tests
that exercise lookup behavior monkeypatch the same attributes back in."""
import pytest


@pytest.fixture(autouse=True)
def _no_song_lookup(monkeypatch):
    monkeypatch.setattr("barbershop.lookup.identify.identify", lambda *a, **k: None)
    monkeypatch.setattr("barbershop.lookup.lyrics.fetch_lyrics", lambda *a, **k: None)
    monkeypatch.setattr("barbershop.lookup.tabs.fetch_chords", lambda *a, **k: None)
