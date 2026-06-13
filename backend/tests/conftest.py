"""Song lookup is stubbed to 'no match' for the whole suite, so pytest
never touches the network even on machines with fpcalc installed. Tests
that exercise lookup behavior monkeypatch the same attributes back in."""
import pytest


def _rmvpe_disabled():
    raise RuntimeError("RMVPE model disabled in tests")


@pytest.fixture(autouse=True)
def _no_song_lookup(monkeypatch):
    monkeypatch.setattr("barbershop.lookup.identify.identify", lambda *a, **k: None)
    monkeypatch.setattr("barbershop.lookup.lyrics.fetch_lyrics", lambda *a, **k: None)
    monkeypatch.setattr("barbershop.lookup.tabs.fetch_candidates", lambda *a, **k: [])
    # disable the heavy RMVPE model loader (no download); rmvpe_f0 stays real and
    # fail-softs to None -> the pyin fallback runs. Tests that exercise the
    # wrapper or routing override _get_model / rmvpe_f0 themselves.
    monkeypatch.setattr("barbershop.analysis.rmvpe._get_model", _rmvpe_disabled)
