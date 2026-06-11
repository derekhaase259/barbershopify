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
