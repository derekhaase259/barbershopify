"""Song lookup: AcoustID identification and LRCLIB lyrics, all network faked."""
import json
import subprocess
from types import SimpleNamespace

import requests

from barbershop.lookup.identify import SongIdentity, _best_match, identify

ACOUSTID_OK = {
    "status": "ok",
    "results": [
        {
            "id": "r1",
            "score": 0.93,
            "recordings": [
                {
                    "id": "mbid-123",
                    "title": "Shine On, Harvest Moon",
                    "artists": [{"name": "Ada Jones"}, {"name": "Billy Murray"}],
                    "releases": [
                        {"date": {"year": 1994}},  # reissue
                        {"date": {"year": 1909}},
                        {},  # release with no date
                    ],
                }
            ],
        },
        {"id": "r2", "score": 0.61, "recordings": []},
    ],
}


class _Resp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return self._payload


def test_best_match_parses_acoustid_response():
    ident = _best_match(ACOUSTID_OK)
    assert ident == SongIdentity(
        title="Shine On, Harvest Moon",
        artist="Ada Jones; Billy Murray",
        year=1909,  # earliest dated release
        recording_mbid="mbid-123",
        match_score=0.93,
    )


def test_best_match_rejects_low_scores_and_errors():
    assert _best_match({"status": "error"}) is None
    low = {"status": "ok", "results": [{"id": "x", "score": 0.3, "recordings": []}]}
    assert _best_match(low) is None
    assert _best_match({"status": "ok", "results": []}) is None


def test_best_match_skips_recordings_without_title_or_artist():
    data = {
        "status": "ok",
        "results": [{"id": "r", "score": 0.9, "recordings": [{"id": "m", "title": ""}]}],
    }
    assert _best_match(data) is None


def _fake_fpcalc(monkeypatch, payload=None, returncode=0):
    out = json.dumps(payload or {"duration": 180.4, "fingerprint": "AQAA_fake"})

    def run(cmd, capture_output, timeout):
        assert cmd[0] == "fpcalc" and cmd[1] == "-json"
        return SimpleNamespace(returncode=returncode, stdout=out.encode(), stderr=b"")

    monkeypatch.setattr(subprocess, "run", run)


def test_identify_happy_path(monkeypatch):
    _fake_fpcalc(monkeypatch)
    seen = {}

    def fake_get(url, params, timeout):
        seen.update(params)
        return _Resp(ACOUSTID_OK)

    monkeypatch.setattr("barbershop.lookup.identify.requests.get", fake_get)
    ident = identify("/tmp/song.mp3", duration=181.0)
    assert ident is not None and ident.title == "Shine On, Harvest Moon"
    assert seen["client"] == "crzNhTAC7w"
    assert seen["duration"] == 180  # fpcalc's duration, rounded
    assert "recordings" in seen["meta"] and "releases" in seen["meta"]


def test_identify_returns_none_when_fpcalc_missing(monkeypatch):
    def run(cmd, capture_output, timeout):
        raise FileNotFoundError("fpcalc")

    monkeypatch.setattr(subprocess, "run", run)
    assert identify("/tmp/song.mp3", duration=180.0) is None


def test_identify_returns_none_on_fpcalc_failure(monkeypatch):
    _fake_fpcalc(monkeypatch, returncode=2)
    assert identify("/tmp/song.mp3", duration=180.0) is None


def test_identify_returns_none_on_network_error(monkeypatch):
    _fake_fpcalc(monkeypatch)

    def fake_get(url, params, timeout):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr("barbershop.lookup.identify.requests.get", fake_get)
    assert identify("/tmp/song.mp3", duration=180.0) is None


from barbershop.lookup.lyrics import LookedUpLyrics, fetch_lyrics, parse_lrc  # noqa: E402

IDENT = SongIdentity(
    title="Shine On, Harvest Moon",
    artist="Ada Jones; Billy Murray",
    year=1909,
    recording_mbid="mbid-123",
    match_score=0.93,
)

LRC = "[ar:Ada Jones]\n[00:12.34] Shine on\n\n[00:15.00][01:15.00] harvest moon\n[10:05.5]up in the sky\n"


def test_parse_lrc():
    assert parse_lrc(LRC) == [
        (12.34, "Shine on"),
        (15.0, "harvest moon"),
        (75.0, "harvest moon"),  # repeated stamp = repeated line
        (605.5, "up in the sky"),
    ]
    assert parse_lrc("") == []
    assert parse_lrc("[ar:meta only]\nno timestamps here") == []


def test_fetch_lyrics_exact_hit(monkeypatch):
    record = {"instrumental": False, "syncedLyrics": LRC, "plainLyrics": "Shine on..."}

    def fake_get(url, params=None, headers=None, timeout=None):
        assert url.endswith("/get")
        assert params["track_name"] == IDENT.title
        assert params["artist_name"] == IDENT.artist
        assert params["duration"] == 180
        assert "barbershopify" in headers["User-Agent"]
        return _Resp(record)

    monkeypatch.setattr("barbershop.lookup.lyrics.requests.get", fake_get)
    got = fetch_lyrics(IDENT, duration=180.2)
    assert got is not None
    assert got.synced[0] == (12.34, "Shine on")
    assert got.plain == "Shine on..."


def test_fetch_lyrics_search_fallback_picks_closest_duration(monkeypatch):
    hits = [
        {"duration": 120, "plainLyrics": "wrong cut"},
        {"duration": 178, "plainLyrics": "right cut", "syncedLyrics": None},
        {"duration": 300, "plainLyrics": "other cut"},
    ]
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(url)
        if url.endswith("/get"):
            return _Resp({}, status=404)
        assert url.endswith("/search")
        return _Resp(hits)

    monkeypatch.setattr("barbershop.lookup.lyrics.requests.get", fake_get)
    got = fetch_lyrics(IDENT, duration=180.0)
    assert got == LookedUpLyrics(synced=None, plain="right cut")
    assert len(calls) == 2


def test_fetch_lyrics_rejects_far_durations_and_instrumentals(monkeypatch):
    def far(url, params=None, headers=None, timeout=None):
        if url.endswith("/get"):
            return _Resp({}, status=404)
        return _Resp([{"duration": 400, "plainLyrics": "different song"}])

    monkeypatch.setattr("barbershop.lookup.lyrics.requests.get", far)
    assert fetch_lyrics(IDENT, duration=180.0) is None

    def instrumental(url, params=None, headers=None, timeout=None):
        return _Resp({"instrumental": True, "plainLyrics": "", "syncedLyrics": ""})

    monkeypatch.setattr("barbershop.lookup.lyrics.requests.get", instrumental)
    assert fetch_lyrics(IDENT, duration=180.0) is None


def test_fetch_lyrics_never_raises(monkeypatch):
    def boom(url, params=None, headers=None, timeout=None):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr("barbershop.lookup.lyrics.requests.get", boom)
    assert fetch_lyrics(IDENT, duration=180.0) is None
