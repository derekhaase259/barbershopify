# Song Lookup (Identification + Real Lyrics) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Identify uploaded songs via chromaprint/AcoustID and fetch their real lyrics from LRCLIB, replacing Whisper ASR when lookup hits, with strict fail-soft so offline behavior is byte-identical to today.

**Architecture:** A new pure module `backend/barbershop/lookup/` (no FastAPI imports, network mocked in tests) is called from a single integration point in `pipeline.analyze()`. Synced lyrics flow through a new time-anchored setter in `textset/align.py` that reuses the existing per-phrase DP. Cache bumps to `-v4` and carries the identity; API responses gain an `identity` field; the frontend shows a badge.

**Tech Stack:** Python 3.12, `requests` (already pinned, 2.34.2), `fpcalc` CLI (optional system dep `libchromaprint-tools`), FastAPI, React/TS. Spec: `docs/superpowers/specs/2026-06-11-song-lookup-design.md`.

**Conventions for every task:** run backend commands from `/home/dhaas/barbershopify/backend` using `.venv/bin/python -m pytest`. Commit messages end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`. Never bypass the fail-soft rule: lookup code may not raise past its module boundary.

---

### Task 1: `lookup/identify.py` — fingerprint → AcoustID → SongIdentity

**Files:**
- Create: `backend/barbershop/lookup/__init__.py` (empty)
- Create: `backend/barbershop/lookup/identify.py`
- Test: `backend/tests/test_lookup.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_lookup.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_lookup.py -v`
Expected: FAIL at import — `ModuleNotFoundError: No module named 'barbershop.lookup'`

- [ ] **Step 3: Implement**

Create empty `backend/barbershop/lookup/__init__.py` and `backend/barbershop/lookup/identify.py`:

```python
"""Song identification: chromaprint fingerprint -> AcoustID -> title/artist/year.

Strict fail-soft: identify() never raises — any failure (no fpcalc, no
network, malformed response) returns None and the caller proceeds as if
the feature didn't exist.
"""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass

import requests

ACOUSTID_APP_KEY = "crzNhTAC7w"  # registered app "Barbershopify"; an app
# identifier by design, not a secret — committing it is AcoustID's intended
# model (Picard and beets do the same), so cloners never need their own key
ACOUSTID_URL = "https://api.acoustid.org/v2/lookup"
MIN_MATCH_SCORE = 0.5
TIMEOUT = (3.05, 10)  # connect, read — offline must cost seconds, not minutes

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SongIdentity:
    title: str
    artist: str
    year: int | None
    recording_mbid: str
    match_score: float


def identify(path: str, duration: float) -> SongIdentity | None:
    """Best AcoustID match for the audio file, or None. Never raises."""
    try:
        proc = subprocess.run(["fpcalc", "-json", path], capture_output=True, timeout=60)
        if proc.returncode != 0:
            log.info("song lookup: fpcalc failed: %s", proc.stderr[:200])
            return None
        fp = json.loads(proc.stdout)
        resp = requests.get(
            ACOUSTID_URL,
            params={
                "client": ACOUSTID_APP_KEY,
                "fingerprint": fp["fingerprint"],
                "duration": int(round(fp.get("duration", duration))),
                "meta": "recordings releases",
            },
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return _best_match(resp.json())
    except FileNotFoundError:
        log.info("song lookup: fpcalc not installed, skipping identification")
        return None
    except Exception:
        log.info("song lookup: identification failed, skipping", exc_info=True)
        return None


def _best_match(data: dict) -> SongIdentity | None:
    if data.get("status") != "ok":
        return None
    results = [r for r in data.get("results", []) if r.get("score", 0) >= MIN_MATCH_SCORE]
    if not results:
        return None
    best = max(results, key=lambda r: r["score"])
    for rec in best.get("recordings", []):
        title = rec.get("title")
        artists = [a.get("name", "") for a in rec.get("artists") or [] if a.get("name")]
        if not title or not artists:
            continue
        years = [
            rel["date"]["year"]
            for rel in rec.get("releases", [])
            if rel.get("date", {}).get("year")
        ]
        return SongIdentity(
            title=title,
            artist="; ".join(artists),
            year=min(years) if years else None,
            recording_mbid=rec.get("id", ""),
            match_score=best["score"],
        )
    return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_lookup.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add barbershop/lookup/ tests/test_lookup.py
git commit -m "feat: song identification via chromaprint + AcoustID

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: `lookup/lyrics.py` — LRCLIB fetch + LRC parsing

**Files:**
- Create: `backend/barbershop/lookup/lyrics.py`
- Test: `backend/tests/test_lookup.py` (append)

- [ ] **Step 1: Write the failing tests** (append to `backend/tests/test_lookup.py`)

```python
from barbershop.lookup.lyrics import LookedUpLyrics, fetch_lyrics, parse_lrc

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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_lookup.py -v`
Expected: new tests FAIL at import — `No module named 'barbershop.lookup.lyrics'`; Task 1 tests still pass.

- [ ] **Step 3: Implement** — create `backend/barbershop/lookup/lyrics.py`:

```python
"""Real lyrics for an identified song, from LRCLIB (no key, no rate limit).

Strict fail-soft: fetch_lyrics() never raises.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import requests

from barbershop.lookup.identify import SongIdentity

LRCLIB_URL = "https://lrclib.net/api"
USER_AGENT = "barbershopify/1.0 (https://github.com/derekhaase259/barbershopify)"
TIMEOUT = (3.05, 10)
MAX_DURATION_GAP = 10.0  # seconds; a search hit further off is a different cut

log = logging.getLogger(__name__)

_LRC_TIME = re.compile(r"\[(\d+):(\d{2}(?:\.\d+)?)\]")


@dataclass(frozen=True)
class LookedUpLyrics:
    synced: list[tuple[float, str]] | None  # (seconds, line), sorted
    plain: str | None


def fetch_lyrics(identity: SongIdentity, duration: float) -> LookedUpLyrics | None:
    """LRCLIB lyrics for the identified song, or None. Never raises."""
    try:
        record = _get_exact(identity, duration) or _search(identity, duration)
        if not record or record.get("instrumental"):
            return None
        synced = parse_lrc(record.get("syncedLyrics") or "")
        plain = (record.get("plainLyrics") or "").strip() or None
        if not synced and not plain:
            return None
        return LookedUpLyrics(synced=synced or None, plain=plain)
    except Exception:
        log.info("song lookup: lyrics fetch failed, skipping", exc_info=True)
        return None


def _get_exact(identity: SongIdentity, duration: float) -> dict | None:
    resp = requests.get(
        f"{LRCLIB_URL}/get",
        params={
            "artist_name": identity.artist,
            "track_name": identity.title,
            "duration": int(round(duration)),
        },
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def _search(identity: SongIdentity, duration: float) -> dict | None:
    resp = requests.get(
        f"{LRCLIB_URL}/search",
        params={"q": f"{identity.artist} {identity.title}"},
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    hits = [h for h in resp.json() or [] if h.get("syncedLyrics") or h.get("plainLyrics")]
    if not hits:
        return None
    best = min(hits, key=lambda h: abs(h.get("duration", 0) - duration))
    if abs(best.get("duration", 0) - duration) > MAX_DURATION_GAP:
        return None
    return best


def parse_lrc(text: str) -> list[tuple[float, str]]:
    """LRC text -> sorted (seconds, line). Multiple stamps repeat the line;
    metadata tags like [ar:...] don't match the digit pattern and drop out."""
    out: list[tuple[float, str]] = []
    for raw in text.splitlines():
        stamps = _LRC_TIME.findall(raw)
        words = _LRC_TIME.sub("", raw).strip()
        if not stamps or not words:
            continue
        for minutes, seconds in stamps:
            out.append((int(minutes) * 60 + float(seconds), words))
    out.sort(key=lambda x: x[0])
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_lookup.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add barbershop/lookup/lyrics.py tests/test_lookup.py
git commit -m "feat: fetch real lyrics from LRCLIB for identified songs

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: `set_timed_lines` — time-anchored lyric setting in textset

**Files:**
- Modify: `backend/barbershop/textset/align.py` (extract shared loop; add `set_timed_lines`)
- Test: `backend/tests/test_textset.py` (append)

- [ ] **Step 1: Write the failing tests** (append to `backend/tests/test_textset.py`; reuse its existing imports/helpers if equivalent ones exist — read the file first)

```python
from barbershop.score import Note, TimeSig
from barbershop.textset.align import set_timed_lines


def _n(onset, dur=480, midi=60):
    return Note(onset=onset, duration=dur, midi=midi)


def _three_phrase_melody():
    # phrases at ticks [0,960), [1920,2880), [3840,4800) — gaps >= 120 split
    return [_n(0), _n(480), _n(1920), _n(2400), _n(3840), _n(4320)]


def test_timed_lines_land_in_their_phrases_and_skip_instrumental():
    out, reports = set_timed_lines(
        _three_phrase_melody(),
        [(0, "shine on"), (3840, "my love")],
        TimeSig(beats=4, beat_type=4),
    )
    texts = {n.onset: n.lyric.text for n in out if n.lyric}
    assert texts[0] == "shine" and texts[480] == "on"
    assert 1920 not in texts and 2400 not in texts  # instrumental interlude: no words
    assert texts[3840] == "my" and texts[4320] == "love"
    assert len(reports) == 3
    assert reports[1].status == "yellow" and "no text" in reports[1].detail


def test_timed_lines_grace_window_for_early_stamps():
    # LRC stamps (and singers) run a hair early: 1900 belongs to the phrase at 1920
    out, _ = set_timed_lines(
        _three_phrase_melody(), [(1900, "my love")], TimeSig(beats=4, beat_type=4)
    )
    texts = {n.onset: n.lyric.text for n in out if n.lyric}
    assert texts == {1920: "my", 2400: "love"}


def test_timed_lines_clamp_before_first_phrase():
    out, _ = set_timed_lines(
        _three_phrase_melody(), [(-500, "shine on")], TimeSig(beats=4, beat_type=4)
    )
    texts = {n.onset: n.lyric.text for n in out if n.lyric}
    assert texts == {0: "shine", 480: "on"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_textset.py -v`
Expected: new tests FAIL — `ImportError: cannot import name 'set_timed_lines'`; all existing textset tests pass.

- [ ] **Step 3: Implement** — in `backend/barbershop/textset/align.py`:

(a) Add `from bisect import bisect_right` to the imports.

(b) Extract the final per-phrase loop of `set_lyrics` (the `out_notes`/`reports` loop, currently lines 172–200) verbatim into:

```python
def _set_per_phrase(
    phrases: list[list[Note]],
    per_phrase: list[list[Syllable]],
    time: TimeSig,
    *,
    elasticity: float,
) -> tuple[list[Note], list[FitReport]]:
    out_notes: list[Note] = []
    reports: list[FitReport] = []
    for idx, (phrase, sylls) in enumerate(zip(phrases, per_phrase)):
        n, m = len(phrase), len(sylls)
        if m == 0:
            out_notes.extend(nn.model_copy(update={"lyric": None}) for nn in phrase)
            reports.append(FitReport(idx, "yellow", 0, n, 0, 0, "no text for this phrase"))
            continue
        ratio = abs(m - n) / max(1, n)
        aligned = _align_phrase(phrase, sylls, time)
        if aligned is None:
            # beyond what splits/melismas can absorb: hard-truncate
            usable = sylls[: n * _MAX_SPLIT]
            aligned = _align_phrase(phrase, usable, time)
            assert aligned is not None
            ratio = max(ratio, 1.0)
        notes, melismas, splits = aligned
        out_notes.extend(notes)
        if ratio > elasticity:
            status = "red"
            detail = f"{m} syllables vs {n} notes — severe mismatch"
        elif melismas or splits:
            status = "yellow"
            detail = f"{melismas} melismas, {splits} split notes"
        else:
            status = "green"
            detail = "natural fit"
        reports.append(FitReport(idx, status, m, n, melismas, splits, detail))
    return out_notes, reports
```

`set_lyrics` keeps its line-distribution logic and ends with `return _set_per_phrase(phrases, per_phrase, time, elasticity=elasticity)`.

(c) Add the new setter:

```python
def set_timed_lines(
    melody: list[Note],
    lines: list[tuple[int, str]],
    time: TimeSig,
    *,
    elasticity: float = 0.4,
) -> tuple[list[Note], list[FitReport]]:
    """Set time-anchored lyric lines (onset_tick, text) under a melody.
    Each line lands in the phrase containing its onset, so instrumental
    intros and interludes correctly receive no words. The caller converts
    wall-clock stamps to ticks; textset stays ignorant of beat grids."""
    GRACE = 240  # singers and LRC stamps run up to a half-beat early
    phrases = split_phrases(melody)
    starts = [phrase[0].onset for phrase in phrases]
    per_phrase: list[list[Syllable]] = [[] for _ in phrases]
    for tick, text in sorted(lines, key=lambda x: x[0]):
        idx = max(0, bisect_right(starts, tick + GRACE) - 1)
        per_phrase[idx].extend(syllabify_line(text))
    return _set_per_phrase(phrases, per_phrase, time, elasticity=elasticity)
```

- [ ] **Step 4: Run the full textset + composer + api suites** (the refactor touches shared code)

Run: `.venv/bin/python -m pytest tests/test_textset.py tests/test_composer.py tests/test_api.py -v`
Expected: all pass (3 new + all pre-existing)

- [ ] **Step 5: Commit**

```bash
git add barbershop/textset/align.py tests/test_textset.py
git commit -m "feat: time-anchored lyric setting for synced (LRC) lyrics

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Pipeline integration — identity, ASR skip, cache v4, offline-by-default tests

**Files:**
- Modify: `backend/barbershop/analysis/pipeline.py`
- Create: `backend/tests/conftest.py`
- Test: `backend/tests/test_analysis.py` (append)

- [ ] **Step 1: Create the offline-by-default conftest** — `backend/tests/conftest.py`:

```python
"""Song lookup is stubbed to 'no match' for the whole suite, so pytest
never touches the network even on machines with fpcalc installed. Tests
that exercise lookup behavior monkeypatch the same attributes back in."""
import pytest


@pytest.fixture(autouse=True)
def _no_song_lookup(monkeypatch):
    monkeypatch.setattr("barbershop.lookup.identify.identify", lambda *a, **k: None)
    monkeypatch.setattr("barbershop.lookup.lyrics.fetch_lyrics", lambda *a, **k: None)
```

- [ ] **Step 2: Write the failing tests** (append to `backend/tests/test_analysis.py`)

```python
from barbershop.lookup.identify import SongIdentity
from barbershop.lookup.lyrics import LookedUpLyrics

IDENT = SongIdentity(
    title="Synth Song", artist="Test Quartet", year=1999,
    recording_mbid="mbid-x", match_score=0.9,
)


def test_lookup_hit_uses_real_lyrics_and_skips_asr(test_wav, monkeypatch):
    monkeypatch.setattr("barbershop.lookup.identify.identify", lambda *a, **k: IDENT)
    monkeypatch.setattr(
        "barbershop.lookup.lyrics.fetch_lyrics",
        lambda *a, **k: LookedUpLyrics(
            synced=[(0.0, "shine on me"), (4.0, "harvest moon")], plain=None
        ),
    )
    monkeypatch.setattr(
        "barbershop.analysis.asr.transcribe",
        lambda path: pytest.fail("ASR must not run when lookup provides lyrics"),
    )
    result = analyze(str(test_wav), use_cache=False)
    assert result.identity == IDENT
    assert result.lyrics_source == "lrclib"
    assert result.input.title == "Synth Song"  # identified title beats filename
    words = " ".join(n.lyric.text for n in result.input.melody if n.lyric)
    assert "shine" in words


def test_lookup_plain_lyrics_fall_back_to_positional_setting(test_wav, monkeypatch):
    monkeypatch.setattr("barbershop.lookup.identify.identify", lambda *a, **k: IDENT)
    monkeypatch.setattr(
        "barbershop.lookup.lyrics.fetch_lyrics",
        lambda *a, **k: LookedUpLyrics(synced=None, plain="shine on me\nharvest moon"),
    )
    result = analyze(str(test_wav), use_cache=False)
    assert result.lyrics_source == "lrclib"
    assert any(n.lyric for n in result.input.melody)


def test_lookup_crash_changes_nothing(test_wav, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("lookup exploded")

    monkeypatch.setattr("barbershop.lookup.identify.identify", boom)
    monkeypatch.setattr("barbershop.analysis.asr.transcribe", lambda path: None)
    result = analyze(str(test_wav), use_cache=False)
    assert result.identity is None
    assert result.lyrics_source == "neutral"  # exactly today's fallback path


def test_cache_roundtrips_identity_and_title_rule(test_wav, monkeypatch, tmp_path):
    monkeypatch.setattr("barbershop.analysis.pipeline.CACHE_DIR", tmp_path)
    monkeypatch.setattr("barbershop.lookup.identify.identify", lambda *a, **k: IDENT)
    monkeypatch.setattr(
        "barbershop.lookup.lyrics.fetch_lyrics",
        lambda *a, **k: LookedUpLyrics(synced=None, plain="shine on me"),
    )
    first = analyze(str(test_wav), use_cache=True)
    assert first.identity == IDENT

    # cache hit: lookup must not run again; identified title survives the
    # filename-derived title parameter
    monkeypatch.setattr(
        "barbershop.lookup.identify.identify",
        lambda *a, **k: pytest.fail("cache hit must not re-identify"),
    )
    second = analyze(str(test_wav), use_cache=True, title="synth")
    assert second.identity == IDENT
    assert second.input.title == "Synth Song"
    assert second.lyrics_source == "lrclib"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_analysis.py -v`
Expected: 4 new tests FAIL (`AnalysisResult` has no `identity`; `analyze()` unchanged); the 3 pre-existing tests still pass.

- [ ] **Step 4: Implement** — modify `backend/barbershop/analysis/pipeline.py`:

(a) Imports: add `import logging`, `from dataclasses import asdict` (extend the existing `dataclass` import), and `from barbershop.lookup.identify import SongIdentity`. Add `log = logging.getLogger(__name__)` below `CACHE_DIR`.

(b) `AnalysisResult` gains a field: `identity: SongIdentity | None = None`.

(c) Cache version bumps: `cache_file = CACHE_DIR / f"{_cache_key(path)}-v4.json"`.

(d) Cache-hit block — replace the unconditional title override:

```python
    if use_cache and cache_file.exists():
        data = json.loads(cache_file.read_text())
        inp = ArrangeInput.model_validate(data["input"])
        identity = SongIdentity(**data["identity"]) if data.get("identity") else None
        if title and identity is None:
            inp.title = title  # filename titles never beat an identified title
        return AnalysisResult(
            input=inp,
            tempo=data["tempo"],
            duration_seconds=data["duration_seconds"],
            lyrics_source=data.get("lyrics_source", "none"),
            lyrics_confidence=data.get("lyrics_confidence", 0.0),
            identity=identity,
        )
```

(e) Signature: `def analyze(path, *, title=None, use_cache=True, lyrics=True, lookup=True)`.

(f) After the `meter, grid.downbeat_phase = ...` line, build the TimeSig once (`time = TimeSig(beats=meter, beat_type=4)`) and use it later in `ArrangeInput(...)` instead of constructing it inline.

(g) Replace the lyrics block (after the melody/chords ValueError guards) with:

```python
    identity = None
    if lookup:
        try:
            from barbershop.lookup.identify import identify

            identity = identify(path, duration=len(y) / sr)
        except Exception:
            log.info("song lookup: identification crashed, continuing", exc_info=True)

    lyrics_source, lyrics_confidence = "none", 0.0
    if lyrics:
        looked_up = None
        if identity is not None:
            try:
                from barbershop.lookup.lyrics import fetch_lyrics

                looked_up = fetch_lyrics(identity, duration=len(y) / sr)
            except Exception:
                log.info("song lookup: lyrics fetch crashed, continuing", exc_info=True)
        if looked_up is not None and looked_up.synced:
            from barbershop.textset.align import set_timed_lines

            lines = [(int(round(grid.time_to_tick(t))), txt) for t, txt in looked_up.synced]
            melody, _ = set_timed_lines(melody, lines, time)
            lyrics_source = "lrclib"
        elif looked_up is not None and looked_up.plain:
            from barbershop.textset.align import set_lyrics

            melody, _ = set_lyrics(melody, looked_up.plain, time)
            lyrics_source = "lrclib"
        else:
            from barbershop.analysis import asr

            words = asr.transcribe(path)
            if words:
                lyrics_confidence = asr.mean_confidence(words)
            if words and asr.attach_lyrics(melody, words, grid):
                lyrics_source = "asr"
            else:
                asr.neutral_lyrics(melody)  # honest fallback, never nonsense
                lyrics_source = "neutral"
```

(h) `ArrangeInput(...)`: `title=(identity.title if identity else None) or title or Path(path).stem` and `time=time`. `AnalysisResult(...)` gains `identity=identity`. The cache-write dict gains `"identity": asdict(identity) if identity else None`.

- [ ] **Step 5: Run the analysis suites**

Run: `.venv/bin/python -m pytest tests/test_analysis.py tests/test_analysis_quality.py -v`
Expected: all pass (7 in test_analysis.py: 3 old + 4 new)

- [ ] **Step 6: Run the full backend suite** (the conftest stub must not break anything)

Run: `.venv/bin/python -m pytest`
Expected: all pass, count = previous total + 15 new

- [ ] **Step 7: Commit**

```bash
git add barbershop/analysis/pipeline.py tests/conftest.py tests/test_analysis.py
git commit -m "feat: integrate song lookup into the analysis pipeline

Identified songs get real LRCLIB lyrics and skip Whisper entirely;
any lookup failure degrades to exactly the previous behavior. Cache
bumps to -v4 and carries the identity.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: API responses carry `identity`

**Files:**
- Modify: `backend/app/main.py` (the two `analyze()`-calling endpoints, lines ~157-200)
- Test: `backend/tests/test_api.py`

- [ ] **Step 1: Write the failing tests.** Read `test_upload_happy_path` in `backend/tests/test_api.py` first; lift its wav-synthesis lines into a module-level helper `_make_test_wav(tmp_path) -> Path` and make the existing test use it (pure refactor, no behavior change). Then append:

```python
def test_upload_reports_identity(monkeypatch, tmp_path):
    from barbershop.analysis import asr
    from barbershop.lookup.identify import SongIdentity

    monkeypatch.setattr(asr, "transcribe", lambda path: None)
    monkeypatch.setattr(
        "barbershop.lookup.identify.identify",
        lambda *a, **k: SongIdentity(
            title="Synth Song", artist="Test Quartet", year=1999,
            recording_mbid="mbid-x", match_score=0.9,
        ),
    )
    wav = _make_test_wav(tmp_path)
    with open(wav, "rb") as f:
        r = client.post("/api/upload?spice=2", files={"file": ("synth.wav", f, "audio/wav")})
    assert r.status_code == 200
    body = r.json()
    assert body["identity"]["title"] == "Synth Song"
    assert body["identity"]["year"] == 1999
    assert body["score"]["title"] == "Synth Song"
```

Also extend the existing `test_upload_happy_path` with one line (the conftest stub makes lookup miss): `assert body["identity"] is None`.

Note: uploads bypass the analysis cache only by content hash — `_make_test_wav` must produce different bytes than other cached runs, or monkeypatch `CACHE_DIR` to `tmp_path` in both tests (`monkeypatch.setattr("barbershop.analysis.pipeline.CACHE_DIR", tmp_path)`) — do the latter; it's deterministic.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_api.py -v`
Expected: `test_upload_reports_identity` FAILS with `KeyError: 'identity'`; `test_upload_happy_path` FAILS on the new assert; rest pass.

- [ ] **Step 3: Implement** — in `backend/app/main.py`, add `from dataclasses import asdict` to the imports, then in BOTH `arrange_test_song` and `upload_and_arrange`, next to the existing `response["lyrics"] = ...` line, add:

```python
    response["identity"] = asdict(result.identity) if result.identity else None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_api.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_api.py
git commit -m "feat: report song identity in upload and test-song responses

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Frontend — identity badge + LRCLIB lyric source

**Files:**
- Modify: `frontend/src/types.ts:68` (Arrangement interface)
- Modify: `frontend/src/App.tsx:284-308` (fine-print `<dl>`)

- [ ] **Step 1: Extend the Arrangement type** in `frontend/src/types.ts` — replace the `lyrics?` line and add `identity`:

```ts
  lyrics?: { source: 'asr' | 'neutral' | 'lrclib' | 'none'; confidence: number }
  identity?: {
    title: string
    artist: string
    year: number | null
    recording_mbid: string
    match_score: number
  } | null
```

- [ ] **Step 2: Add the badge** in `frontend/src/App.tsx` — inside the fine-print `<dl>` (before the "Dominant 7th share" row), add:

```tsx
              {arrangement.identity && (
                <>
                  <dt>Identified</dt>
                  <dd>
                    ♪ {arrangement.identity.title} — {arrangement.identity.artist}
                    {arrangement.identity.year ? ` (${arrangement.identity.year})` : ''}
                  </dd>
                </>
              )}
```

and extend the lyrics `<dd>` ternary:

```tsx
                  <dd>
                    {arrangement.lyrics.source === 'lrclib'
                      ? 'looked up (LRCLIB)'
                      : arrangement.lyrics.source === 'asr'
                        ? `heard (${Math.round(arrangement.lyrics.confidence * 100)}% sure)`
                        : 'doo/dah (transcription unclear)'}
                  </dd>
```

No store changes: the store keeps the whole `Arrangement` response, so `identity` rides along for free.

- [ ] **Step 3: Verify types and tests**

Run: `cd /home/dhaas/barbershopify/frontend && npm run build && npx vitest run`
Expected: build succeeds (tsc clean), all 10 vitest tests pass. (Display-only change; visual check happens in Task 8.)

- [ ] **Step 4: Commit**

```bash
git add src/types.ts src/App.tsx
git commit -m "feat: show identified song and lyric source in the sidebar

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Docs — README, SPEC addendum, DESIGN rationale

**Files:**
- Modify: `README.md` (install table ~line 14-19, troubleshooting table ~line 94-103, test count ~line 112)
- Modify: `SPEC.md` (append addendum)
- Modify: `DESIGN.md` (append section)

- [ ] **Step 1: README.** Add to the install table:

```markdown
| **chromaprint** | any recent — *optional: enables song identification* | `brew install chromaprint` | [acoustid.org/chromaprint](https://acoustid.org/chromaprint) / `sudo apt install libchromaprint-tools` |
```

Add to the troubleshooting table:

```markdown
| Uploaded song wasn't identified (no "Identified" line) | Identification needs `fpcalc` (chromaprint, table above) and internet, and works best on commercially released recordings. Without it the app falls back to on-device transcription — everything still works. |
| Looked-up lyrics are for the wrong song/version | Fingerprint matched a different release. Edit lyrics freely in the **Lyrics** panel — they're just a starting point. |
```

Under "Your own song" in *How to use it*, append: `When you're online, the app fingerprints the upload (the fingerprint — not your audio — goes to acoustid.org) and, if recognized, pulls the real lyrics from lrclib.net instead of guessing them from the recording.`

Update the test count in *Under the hood* to the number the full suite reports in Task 8.

- [ ] **Step 2: SPEC.md** — append:

```markdown
## Addendum (2026-06-11): song lookup

Agreed extension beyond the original brief: uploads are fingerprinted (chromaprint →
AcoustID) to identify the song, and identified songs get real lyrics from LRCLIB instead
of speech recognition, which is skipped entirely on a hit. Free services only; the one
AcoustID application key is registered to us and committed. Melody and chords remain
audio-analysis territory — no free database for them exists. Any lookup failure degrades
to the previous behavior. Full design: docs/superpowers/specs/2026-06-11-song-lookup-design.md.
```

- [ ] **Step 3: DESIGN.md** — append a "Song lookup" section covering, in this order: why lookup beats analysis for identity/lyrics but cannot replace it for melody/chords (no database exists; Shazam only matches fingerprints to names); the strict fail-soft rule and where it's enforced (inside `lookup/`, plus belt-and-suspenders in the pipeline); why ASR is skipped on a hit (15–60 s + first-run model download saved); the title precedence rule (identified > explicit param > filename); why the AcoustID app key is committed (application identifier, not a secret — Picard/beets precedent); and why `tests/conftest.py` stubs lookup by default (suite stays offline-deterministic).

- [ ] **Step 4: Commit**

```bash
git add README.md SPEC.md DESIGN.md
git commit -m "docs: song lookup — README setup/troubleshooting, spec addendum, design rationale

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: Real-audio verification + push

**Files:** none (verification only; fix anything found before pushing)

- [ ] **Step 1: Install fpcalc**

Run: `sudo apt install -y libchromaprint-tools && fpcalc -version`
Expected: prints a chromaprint version. (If sudo prompts interactively, ask the user to run `! sudo apt install -y libchromaprint-tools`.)

- [ ] **Step 2: Full backend suite, one last time**

Run: `.venv/bin/python -m pytest` (from `backend/`)
Expected: all pass. Note the total for the README count (Task 7).

- [ ] **Step 3: Live identification probe** (real network, real audio — bypass the suite's offline stub)

Run from `backend/`:
```bash
.venv/bin/python -c "
from barbershop.lookup.identify import identify
from barbershop.lookup.lyrics import fetch_lyrics
import librosa
path = '../test_songs/down-by-the-old-mill-stream.mp3'
dur = librosa.get_duration(path=path)
ident = identify(path, duration=dur)
print('identity:', ident)
print('lyrics:', (fetch_lyrics(ident, dur) is not None) if ident else 'n/a')
"
```
Expected: either a plausible `SongIdentity` (great) or `identity: None` (acceptable — century-old 78 transfers may not be fingerprinted; the design predicts this). **Both are passes; an unhandled traceback is the only failure.**

- [ ] **Step 4: End-to-end in the browser.** Clear the analysis cache (`rm -f backend/cache/*-v3.json` is moot — v4 keys are fresh anyway), confirm backend (8731) and frontend (5280) are running (`make dev` if not). With Playwright: load http://localhost:5280, click a Victrola song, wait for the chart, screenshot the sidebar. Verify: chart renders, no errors, and the fine-print shows either an "Identified" row or (on a miss) exactly the pre-feature display. If any modern .mp3 is available locally, upload it and verify the badge + LRCLIB lyrics appear.

- [ ] **Step 5: Offline fail-soft probe.** Re-run one Victrola analysis with networking broken for the lookup hosts:

```bash
.venv/bin/python -c "
import socket; socket.setdefaulttimeout(0.001)
from barbershop.analysis.pipeline import analyze
r = analyze('../test_songs/down-by-the-old-mill-stream.mp3', use_cache=False, lyrics=False)
print('ok:', r.input.title, r.identity)
"
```
Expected: completes without error, `identity` is `None`. (Timeout trick degrades all sockets; analysis itself is local so only lookup is affected.)

- [ ] **Step 6: Push**

```bash
git push origin main
```
Expected: clean push. Confirm with `git log origin/main -1 --oneline`.

---

## Self-review checklist (for the plan author — already run)

- Spec coverage: services ✓ (T1, T2), components ✓ (T1–T3), data flow incl. cache v4 + title rule ✓ (T4), API ✓ (T5), error handling ✓ (T1/T2 never-raise tests, T4 crash test, T8 offline probe), UI ✓ (T6), setup & docs ✓ (T7), testing ✓ (throughout), out-of-scope honored (no MIDI hunting anywhere).
- No placeholders: every code step shows the code; the two "read the file first" steps (T3 helpers, T5 wav refactor) reference existing committed code by exact test name.
- Type consistency: `SongIdentity(title, artist, year, recording_mbid, match_score)` and `LookedUpLyrics(synced, plain)` used identically in T1/T2/T4/T5; `set_timed_lines(melody, lines, time, *, elasticity)` identical in T3/T4; `identity` JSON field name identical in T4/T5/T6.
