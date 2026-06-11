# Tab Chords (Chordie Chord Correction) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fetch an identified song's chord progression from Chordie and use it to correct the audio-analyzed chord identities (never the timing), gated on ≥50% root agreement.

**Architecture:** Three new pure units in `backend/barbershop/lookup/` — `chordnames.py` (guitar name → vocabulary chord), `tabs.py` (Chordie scrape → `TabChords`), `align.py` (12-transposition Needleman–Wunsch → `TabAlignment`) — glued into `pipeline.analyze()` after span construction and before key detection, with identification moved earlier. Cache bumps to `-v5`; API/UI gain a `chords` source row. Spec: `docs/superpowers/specs/2026-06-11-tab-chords-design.md`.

**Tech Stack:** Python 3.12, `requests` (pinned already), regex parsing (no new deps), FastAPI, React/TS.

**Conventions:** backend commands run from `/home/dhaas/barbershopify/backend` with `.venv/bin/python -m pytest`. Work on branch `feat/tab-chords`. Commits end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`. The fail-soft contract is inviolable: no lookup code raises past its module boundary.

---

### Task 0: Live source probe — ✅ DONE during planning (2026-06-11)

Findings (recorded in the spec): UG search and tab pages, e-chords, UkuTabs, and Cifra Club song pages all return 403 Cloudflare challenges to plain `requests` (full browser headers included). **Chordie serves 200s**: `https://www.chordie.com/results.php?q=hey+jude` returns results with `/chord.pere/...` song links (duplicates present — dedupe), and song pages embed full ChordPro in `<textarea id="chordproContent" ...>{t:Hey Jude}\n{st:Wilson Pickett}...</textarea>` with inline `[C]`-style chords, `{sot}…{eot}` tablature blocks, and `#` comment lines. Top search hit can be a cover (Wilson Pickett, not The Beatles) → artist/title verification is mandatory.

---

### Task 1: `lookup/chordnames.py` — guitar chord names → vocabulary chords

**Files:**
- Create: `backend/barbershop/lookup/chordnames.py`
- Test: `backend/tests/test_tab_chords.py` (new file)

- [ ] **Step 1: Create branch**

```bash
git checkout main && git pull -q && git checkout -b feat/tab-chords
```

- [ ] **Step 2: Write the failing tests** — create `backend/tests/test_tab_chords.py`:

```python
"""Tab chords: Chordie scraping, chord-name parsing, and span alignment."""
import pytest

from barbershop.lookup.chordnames import parse_chord


@pytest.mark.parametrize(
    "name,expected",
    [
        ("C", (0, "maj")),
        ("F#m7", (6, "min7")),
        ("Bb7", (10, "dom7")),
        ("Cmaj7", (0, "maj6")),   # barbershop never voices maj7
        ("Am7b5", (9, "halfdim7")),
        ("Ddim", (2, "dim7")),
        ("Ebdim7", (3, "dim7")),
        ("G/B", (7, "maj")),      # slash bass is the arranger's business
        ("Esus4", (4, "maj")),
        ("A7sus4", (9, "dom7")),
        ("Cadd9", (0, "maj")),
        ("C6", (0, "maj6")),
        ("Am6", (9, "min6")),
        ("Caug", (0, "aug")),
        ("C9", (0, "dom9")),
        ("C13", (0, "dom9")),
        ("Amin", (9, "min")),
        ("Cmaj", (0, "maj")),
        ("B7", (11, "dom7")),
    ],
)
def test_parse_chord(name, expected):
    assert parse_chord(name) == expected


def test_parse_chord_rejects_garbage():
    assert parse_chord("N.C.") is None
    assert parse_chord("x2") is None
    assert parse_chord("") is None


def test_parse_chord_unknown_decoration_falls_back_to_major():
    assert parse_chord("C7b9") == (0, "dom7")  # startswith family
    assert parse_chord("Gweird") == (7, "maj")
```

- [ ] **Step 3: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_tab_chords.py -v`
Expected: collection ERROR — `No module named 'barbershop.lookup.chordnames'`

- [ ] **Step 4: Implement** — create `backend/barbershop/lookup/chordnames.py`:

```python
"""Guitar chord names -> (root_pc, quality) in the arranger vocabulary.

Lossy on purpose: tabs name chords guitarists play; the arranger needs the
closest member of the barbershop vocabulary (maj7 -> maj6, sus -> maj, slash
bass dropped — the bass voice is the arranger's business).
"""
from __future__ import annotations

import re

_ROOTS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

_STRIP = re.compile(r"\(?(sus[24]?|add\d+)\)?")

# ordered: within a shared-prefix family, longer suffixes first
_QUALITIES: list[tuple[str, str]] = [
    ("m7b5", "halfdim7"),
    ("dim7", "dim7"),
    ("dim", "dim7"),
    ("maj7", "maj6"),
    ("maj9", "maj6"),
    ("maj", "maj"),
    ("min7", "min7"),
    ("min6", "min6"),
    ("min", "min"),
    ("m7", "min7"),
    ("m6", "min6"),
    ("m9", "min7"),
    ("m", "min"),
    ("aug7", "aug7"),
    ("aug", "aug"),
    ("13", "dom9"),
    ("11", "dom9"),
    ("9", "dom9"),
    ("7", "dom7"),
    ("6", "maj6"),
    ("5", "maj"),
]


def parse_chord(name: str) -> tuple[int, str] | None:
    """(root_pc, vocabulary quality), or None for non-chords ('N.C.', 'x2')."""
    token = name.strip().split("/")[0]
    m = re.match(r"([A-G])([#b]?)(.*)$", token)
    if not m:
        return None
    root = _ROOTS[m.group(1)]
    root += 1 if m.group(2) == "#" else -1 if m.group(2) == "b" else 0
    suffix = _STRIP.sub("", m.group(3)).strip()
    if suffix and not re.match(r"[a-z0-9]", suffix, re.I):
        return None  # 'N.C.' style: a letter followed by punctuation
    for pat, quality in _QUALITIES:
        if suffix.startswith(pat):
            return root % 12, quality
    return root % 12, "maj"  # bare letter or unknown decoration on a triad
```

- [ ] **Step 5: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_tab_chords.py -v`
Expected: 22 passed (20 parametrized + 2)

Note: `parse_chord("N.C.")` — "N" is not in `[A-G]`... it is not (A-G only); `re.match` fails → None. "x2" lowercase → no match → None. Verify both in the run; if `C7b9` resolves wrong, check `_QUALITIES` ordering before touching anything else.

- [ ] **Step 6: Commit**

```bash
git add barbershop/lookup/chordnames.py tests/test_tab_chords.py
git commit -m "feat: parse guitar chord names into the arranger vocabulary

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: `lookup/tabs.py` — Chordie search + ChordPro extraction

**Files:**
- Create: `backend/barbershop/lookup/tabs.py`
- Test: `backend/tests/test_tab_chords.py` (append)

- [ ] **Step 1: Write the failing tests** (append to `backend/tests/test_tab_chords.py`):

```python
import requests

from barbershop.lookup.identify import SongIdentity
from barbershop.lookup.tabs import TabChords, fetch_chords, parse_chordpro

BEATLES = SongIdentity(
    title="Hey Jude", artist="The Beatles", year=1968,
    recording_mbid="mbid-hj", match_score=0.95,
)

CHORDPRO = """{t:Hey Jude}
{st:The Beatles}
#
# Intro tab:
{sot}
e--0--1--3--| [C] this is inside a tab block and must not count
{eot}
# [G] comment lines must not count either
Hey [C]Jude, don't make it [G]bad
Take a [G7]sad song and make it [C]better
Re[F]member to let her into your [C]heart
"""

SEARCH_HTML = (
    '<a href="/chord.pere/www.guitaretab.com/w/wilson/1.html">x</a>'
    '<a href="/chord.pere/www.guitaretab.com/w/wilson/1.html">dup</a>'
    '<a href="/chord.pere/www.guitaretab.com/b/beatles/2.html">y</a>'
)


def _song_html(pro):
    return f'<html><textarea id="chordproContent" name="chopro">{pro}</textarea></html>'

WILSON_PRO = "{t:Hey Jude}\n{st:Wilson Pickett}\n[Am]everybody [D]now"


class _Page:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


def test_parse_chordpro_skips_tab_blocks_and_comments():
    assert parse_chordpro(CHORDPRO) == ["C", "G", "G7", "C", "F", "C"]


def test_fetch_chords_verifies_artist_and_dedupes(monkeypatch):
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        if "results.php" in url:
            return _Page(SEARCH_HTML)
        if "/wilson/1.html" in url:
            return _Page(_song_html(WILSON_PRO))  # wrong artist: must be skipped
        return _Page(_song_html(CHORDPRO))

    monkeypatch.setattr("barbershop.lookup.tabs.requests.get", fake_get)
    tab = fetch_chords(BEATLES)
    assert tab is not None
    assert tab.artist == "The Beatles"
    assert tab.chords == ["C", "G", "G7", "C", "F", "C"]
    # search + wilson (rejected) + beatles = 3 requests; the dup link was not refetched
    assert len(calls) == 3


def test_fetch_chords_returns_none_when_no_artist_matches(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        if "results.php" in url:
            return _Page('<a href="/chord.pere/x/1.html">x</a>')
        return _Page(_song_html(WILSON_PRO))

    monkeypatch.setattr("barbershop.lookup.tabs.requests.get", fake_get)
    assert fetch_chords(BEATLES) is None


def test_fetch_chords_requires_minimum_chords(monkeypatch):
    short = "{t:Hey Jude}\n{st:The Beatles}\n[C]too [G]short"

    def fake_get(url, headers=None, timeout=None):
        if "results.php" in url:
            return _Page('<a href="/chord.pere/x/1.html">x</a>')
        return _Page(_song_html(short))

    monkeypatch.setattr("barbershop.lookup.tabs.requests.get", fake_get)
    assert fetch_chords(BEATLES) is None


def test_fetch_chords_never_raises(monkeypatch):
    def boom(url, headers=None, timeout=None):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr("barbershop.lookup.tabs.requests.get", boom)
    assert fetch_chords(BEATLES) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_tab_chords.py -v`
Expected: collection ERROR — `No module named 'barbershop.lookup.tabs'`; Task 1 tests unaffected once collection passes.

- [ ] **Step 3: Implement** — create `backend/barbershop/lookup/tabs.py`:

```python
"""Chord progressions from Chordie (free, no key) for identified songs.

Chordie aggregates third-party tab sites and embeds each song's ChordPro
source in the page; search relevance is loose (covers outrank originals),
so every candidate's {t:}/{st:} is verified against the AcoustID identity.
Strict fail-soft: fetch_chords() never raises.
"""
from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from urllib.parse import quote_plus

import requests

from barbershop.lookup.identify import SongIdentity

CHORDIE_URL = "https://www.chordie.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)  # chordie serves plain requests; a browser UA keeps us boring
TIMEOUT = (3.05, 10)
MAX_CANDIDATES = 5
MIN_TOKENS = 4

log = logging.getLogger(__name__)

_SONG_LINK = re.compile(r'href="(/chord\.pere/[^"]+)"')
_CHORDPRO = re.compile(r'<textarea id="chordproContent"[^>]*>(.*?)</textarea>', re.S)
_TITLE = re.compile(r"\{t:([^}]*)\}")
_ARTIST = re.compile(r"\{st:([^}]*)\}")
_TAB_BLOCK = re.compile(r"\{sot\}.*?\{eot\}", re.S)
_CHORD_TOKEN = re.compile(r"\[([A-G][^\]\s]*)\]")
_STOPWORDS = frozenset({"the", "a", "an"})


@dataclass(frozen=True)
class TabChords:
    chords: list[str]  # raw names, playing order
    url: str
    artist: str
    title: str


def fetch_chords(identity: SongIdentity) -> TabChords | None:
    """Best Chordie chord sheet for the identified song, or None. Never raises."""
    try:
        q = quote_plus(f"{identity.title} {identity.artist}")
        page = requests.get(
            f"{CHORDIE_URL}/results.php?q={q}",
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
        )
        page.raise_for_status()
        links: list[str] = []
        for link in _SONG_LINK.findall(page.text):
            if link not in links:
                links.append(link)
        for link in links[:MAX_CANDIDATES]:
            tab = _candidate(link, identity)
            if tab is not None:
                return tab
        return None
    except Exception:
        log.info("song lookup: tab fetch failed, skipping", exc_info=True)
        return None


def _candidate(link: str, identity: SongIdentity) -> TabChords | None:
    page = requests.get(
        CHORDIE_URL + html.unescape(link),
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT,
    )
    if page.status_code != 200:
        return None
    m = _CHORDPRO.search(page.text)
    if m is None:
        return None
    pro = html.unescape(m.group(1))
    title = t.group(1).strip() if (t := _TITLE.search(pro)) else ""
    artist = a.group(1).strip() if (a := _ARTIST.search(pro)) else ""
    if not (_overlaps(artist, identity.artist) and _overlaps(title, identity.title)):
        return None
    chords = parse_chordpro(pro)
    if len(chords) < MIN_TOKENS:
        return None
    return TabChords(chords=chords, url=CHORDIE_URL + link, artist=artist, title=title)


def _overlaps(found: str, wanted: str) -> bool:
    return bool(_tokens(found) & _tokens(wanted))


def _tokens(s: str) -> set[str]:
    return {
        t for t in re.findall(r"[a-z0-9']+", s.lower())
        if t not in _STOPWORDS and len(t) >= 2
    }


def parse_chordpro(pro: str) -> list[str]:
    """Inline chord tokens in order; tablature blocks and # comments dropped."""
    body = _TAB_BLOCK.sub("", pro)
    lines = [ln for ln in body.splitlines() if not ln.lstrip().startswith("#")]
    return _CHORD_TOKEN.findall("\n".join(lines))
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_tab_chords.py -v`
Expected: 27 passed

- [ ] **Step 5: Commit**

```bash
git add barbershop/lookup/tabs.py tests/test_tab_chords.py
git commit -m "feat: fetch chord progressions from Chordie for identified songs

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: `lookup/align.py` — 12-transposition alignment + gate

**Files:**
- Create: `backend/barbershop/lookup/align.py`
- Test: `backend/tests/test_tab_chords.py` (append)

- [ ] **Step 1: Write the failing tests** (append):

```python
from barbershop.lookup.align import TabAlignment, apply_tab
from barbershop.score import ChordSpan


def _spans(seq):
    return [
        ChordSpan(onset=i * 1920, duration=1920, root_pc=r, quality=q)
        for i, (r, q) in enumerate(seq)
    ]


def _tab(chords):
    return TabChords(chords=chords, url="http://x", artist="A", title="T")


def test_apply_tab_fixes_false_minor():
    # audio heard Am where the song plays C; everything else agrees
    audio = _spans([(0, "maj"), (9, "min"), (7, "dom7"), (0, "maj")])
    tab = _tab(["C", "C", "G7", "C"])
    fixed = apply_tab(audio, tab)
    assert fixed is not None
    assert fixed.transposition == 0
    assert [(s.root_pc, s.quality) for s in fixed.spans] == [
        (0, "maj"), (0, "maj"), (7, "dom7"), (0, "maj")
    ]
    assert fixed.agreement == 0.75  # 3 of 4 roots agreed before correction


def test_apply_tab_recovers_transposition():
    # tab written 3 semitones low (capo 3): A F#m E7 A vs audio in C
    audio = _spans([(0, "maj"), (9, "min7"), (7, "dom7"), (0, "maj"), (5, "maj")])
    tab = _tab(["A", "F#m7", "E7", "A", "D"])
    fixed = apply_tab(audio, tab)
    assert fixed is not None
    assert fixed.transposition == 3
    assert [(s.root_pc, s.quality) for s in fixed.spans] == [
        (0, "maj"), (9, "min7"), (7, "dom7"), (0, "maj"), (5, "maj")
    ]
    assert fixed.agreement == 1.0


def test_apply_tab_absorbs_extra_tab_chords_as_gaps():
    audio = _spans([(0, "maj"), (7, "dom7"), (0, "maj")])
    tab = _tab(["C", "Em", "Am", "F", "G7", "C"])  # fuller progression than audio saw
    fixed = apply_tab(audio, tab)
    assert fixed is not None
    assert [s.root_pc for s in fixed.spans] == [0, 7, 0]


def test_apply_tab_rejects_disagreeing_tab():
    audio = _spans([(0, "maj"), (5, "maj"), (7, "dom7"), (0, "maj")] * 3)
    tab = _tab(["Eb", "Bbm", "F#", "B", "Dbm", "Ab"])  # unrelated song
    assert apply_tab(audio, tab) is None


def test_apply_tab_needs_enough_chords():
    audio = _spans([(0, "maj"), (7, "dom7")])
    assert apply_tab(audio, _tab(["C", "C", "C", "G7"])) is not None  # compresses to 2 -> below min
```

Wait — that last test compresses `["C","C","C","G7"]` to 2 distinct chords, below `MIN_TAB_CHORDS = 4`, so it must assert `is None`. Use this corrected version:

```python
def test_apply_tab_needs_enough_chords():
    audio = _spans([(0, "maj"), (7, "dom7")])
    assert apply_tab(audio, _tab(["C", "C", "C", "G7"])) is None  # compresses to 2 distinct
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_tab_chords.py -v`
Expected: collection ERROR — `No module named 'barbershop.lookup.align'`

- [ ] **Step 3: Implement** — create `backend/barbershop/lookup/align.py`:

```python
"""Align an untimed tab chord sequence onto analyzed chord spans.

Timing always comes from audio; the tab only corrects chord identities.
All 12 transpositions are tried (subsumes capo, transposed tabs, off-pitch
transfers); the winner must agree with >= 50% of the analyzed roots or the
tab is rejected wholesale — a bad tab can never make the chords worse.
"""
from __future__ import annotations

from dataclasses import dataclass

from barbershop.lookup.chordnames import parse_chord
from barbershop.lookup.tabs import TabChords
from barbershop.score import ChordSpan

GATE = 0.5
MIN_TAB_CHORDS = 4
_SUB_QUALITY = 0.25  # same root, different quality
_SUB_ROOT = 1.0      # different root
_GAP_SPAN = 0.6      # analyzed span with no tab counterpart
_GAP_TAB = 0.4       # tab chord skipped (tabs spell out every repeat)


@dataclass(frozen=True)
class TabAlignment:
    spans: list[ChordSpan]
    agreement: float
    transposition: int
    url: str


def apply_tab(spans: list[ChordSpan], tab: TabChords) -> TabAlignment | None:
    seq: list[tuple[int, str]] = []
    for name in tab.chords:
        parsed = parse_chord(name)
        if parsed and (not seq or seq[-1] != parsed):
            seq.append(parsed)  # consecutive duplicates compressed
    if len(seq) < MIN_TAB_CHORDS or not spans:
        return None
    audio = [(s.root_pc, s.quality) for s in spans]
    best: tuple[float, int, list[tuple[int, int]], list[tuple[int, str]]] | None = None
    for t in range(12):
        shifted = [((r + t) % 12, q) for r, q in seq]
        cost, pairs = _align(audio, shifted)
        if best is None or cost < best[0]:
            best = (cost, t, pairs, shifted)
    _, t, pairs, shifted = best
    matched = sum(1 for i, j in pairs if audio[i][0] == shifted[j][0])
    agreement = matched / len(spans)
    if agreement < GATE:
        return None
    out = list(spans)
    for i, j in pairs:
        root, quality = shifted[j]
        out[i] = spans[i].model_copy(update={"root_pc": root, "quality": quality})
    return TabAlignment(
        spans=out, agreement=round(agreement, 3), transposition=t, url=tab.url
    )


def _sub(x: tuple[int, str], y: tuple[int, str]) -> float:
    if x[0] != y[0]:
        return _SUB_ROOT
    return 0.0 if x[1] == y[1] else _SUB_QUALITY


def _align(
    a: list[tuple[int, str]], b: list[tuple[int, str]]
) -> tuple[float, list[tuple[int, int]]]:
    """Global Needleman-Wunsch. Returns (cost, substitution pairs (i, j))."""
    n, m = len(a), len(b)
    cost = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        cost[i][0] = i * _GAP_SPAN
    for j in range(1, m + 1):
        cost[0][j] = j * _GAP_TAB
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost[i][j] = min(
                cost[i - 1][j - 1] + _sub(a[i - 1], b[j - 1]),
                cost[i - 1][j] + _GAP_SPAN,
                cost[i][j - 1] + _GAP_TAB,
            )
    pairs: list[tuple[int, int]] = []
    i, j = n, m
    while i > 0 and j > 0:
        if abs(cost[i][j] - (cost[i - 1][j - 1] + _sub(a[i - 1], b[j - 1]))) < 1e-9:
            pairs.append((i - 1, j - 1))
            i -= 1
            j -= 1
        elif abs(cost[i][j] - (cost[i - 1][j] + _GAP_SPAN)) < 1e-9:
            i -= 1
        else:
            j -= 1
    pairs.reverse()
    return cost[n][m], pairs
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_tab_chords.py -v`
Expected: 32 passed. If `test_apply_tab_fixes_false_minor` reports agreement ≠ 0.75, inspect the backtrack tie-breaking (diagonal must win ties) before changing constants.

- [ ] **Step 5: Commit**

```bash
git add barbershop/lookup/align.py tests/test_tab_chords.py
git commit -m "feat: 12-transposition alignment of tab chords onto analyzed spans

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Pipeline integration — identify earlier, correct spans, cache v5

**Files:**
- Modify: `backend/barbershop/analysis/pipeline.py`
- Modify: `backend/tests/conftest.py`
- Test: `backend/tests/test_analysis.py` (append)

- [ ] **Step 1: Extend the conftest stub** — `backend/tests/conftest.py` becomes:

```python
"""Song lookup is stubbed to 'no match' for the whole suite, so pytest
never touches the network even on machines with fpcalc installed. Tests
that exercise lookup behavior monkeypatch the same attributes back in."""
import pytest


@pytest.fixture(autouse=True)
def _no_song_lookup(monkeypatch):
    monkeypatch.setattr("barbershop.lookup.identify.identify", lambda *a, **k: None)
    monkeypatch.setattr("barbershop.lookup.lyrics.fetch_lyrics", lambda *a, **k: None)
    monkeypatch.setattr("barbershop.lookup.tabs.fetch_chords", lambda *a, **k: None)
```

- [ ] **Step 2: Write the failing tests** (append to `backend/tests/test_analysis.py`; `IDENT` and the lookup imports already exist there from the lyrics feature):

```python
def test_tab_correction_applies(test_wav, monkeypatch):
    from barbershop.lookup.align import TabAlignment
    from barbershop.lookup.tabs import TabChords

    monkeypatch.setattr("barbershop.lookup.identify.identify", lambda *a, **k: IDENT)
    monkeypatch.setattr(
        "barbershop.lookup.tabs.fetch_chords",
        lambda *a, **k: TabChords(chords=["C", "F", "G7"], url="http://tab", artist="x", title="y"),
    )

    def fake_apply(spans, tab):
        fixed = [s.model_copy(update={"quality": "min7"}) for s in spans]
        return TabAlignment(spans=fixed, agreement=0.83, transposition=0, url=tab.url)

    monkeypatch.setattr("barbershop.lookup.align.apply_tab", fake_apply)
    monkeypatch.setattr("barbershop.analysis.asr.transcribe", lambda path: None)
    result = analyze(str(test_wav), use_cache=False)
    assert result.chord_source == "tab"
    assert result.chord_agreement == 0.83
    assert result.tab_url == "http://tab"
    assert all(c.quality == "min7" for c in result.input.chords)


def test_tab_rejection_keeps_audio_chords(test_wav, monkeypatch):
    from barbershop.lookup.tabs import TabChords

    monkeypatch.setattr("barbershop.lookup.identify.identify", lambda *a, **k: IDENT)
    monkeypatch.setattr(
        "barbershop.lookup.tabs.fetch_chords",
        lambda *a, **k: TabChords(chords=["Eb", "Bbm", "F#", "B"], url="http://tab", artist="x", title="y"),
    )
    monkeypatch.setattr("barbershop.lookup.align.apply_tab", lambda spans, tab: None)
    monkeypatch.setattr("barbershop.analysis.asr.transcribe", lambda path: None)
    result = analyze(str(test_wav), use_cache=False)
    assert result.chord_source == "audio"
    assert result.chord_agreement is None


def test_tab_crash_changes_nothing(test_wav, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("tab fetch exploded")

    monkeypatch.setattr("barbershop.lookup.identify.identify", lambda *a, **k: IDENT)
    monkeypatch.setattr("barbershop.lookup.tabs.fetch_chords", boom)
    monkeypatch.setattr("barbershop.analysis.asr.transcribe", lambda path: None)
    result = analyze(str(test_wav), use_cache=False)
    assert result.chord_source == "audio"


def test_cache_roundtrips_chord_fields(test_wav, monkeypatch, tmp_path):
    from barbershop.lookup.align import TabAlignment
    from barbershop.lookup.tabs import TabChords

    monkeypatch.setattr("barbershop.analysis.pipeline.CACHE_DIR", tmp_path)
    monkeypatch.setattr("barbershop.lookup.identify.identify", lambda *a, **k: IDENT)
    monkeypatch.setattr(
        "barbershop.lookup.tabs.fetch_chords",
        lambda *a, **k: TabChords(chords=["C", "F", "G7", "C"], url="http://tab", artist="x", title="y"),
    )
    monkeypatch.setattr(
        "barbershop.lookup.align.apply_tab",
        lambda spans, tab: TabAlignment(spans=list(spans), agreement=0.9, transposition=2, url=tab.url),
    )
    monkeypatch.setattr("barbershop.analysis.asr.transcribe", lambda path: None)
    analyze(str(test_wav), use_cache=True)
    monkeypatch.setattr(
        "barbershop.lookup.tabs.fetch_chords",
        lambda *a, **k: pytest.fail("cache hit must not refetch the tab"),
    )
    second = analyze(str(test_wav), use_cache=True)
    assert second.chord_source == "tab"
    assert second.chord_agreement == 0.9
    assert second.tab_url == "http://tab"
```

- [ ] **Step 3: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_analysis.py -v`
Expected: the 4 new tests FAIL (`AnalysisResult` has no `chord_source`); the 7 existing pass.

- [ ] **Step 4: Implement** — modify `backend/barbershop/analysis/pipeline.py`:

(a) `AnalysisResult` gains three fields after `identity`:

```python
    chord_source: str = "audio"  # audio / tab
    chord_agreement: float | None = None
    tab_url: str | None = None
```

(b) Cache version: `-v4` → `-v5` in the `cache_file = ...` line.

(c) Cache-hit return gains the three fields:

```python
            chord_source=data.get("chord_source", "audio"),
            chord_agreement=data.get("chord_agreement"),
            tab_url=data.get("tab_url"),
```

(d) **Move identification earlier.** Delete the `identity = None / if lookup:` block from its current position (just before the lyrics block) and insert it immediately after `grid = beats_mod.track(y, sr)`:

```python
    identity = None
    if lookup:
        try:
            from barbershop.lookup.identify import identify

            identity = identify(path, duration=len(y) / sr)
        except Exception:
            log.info("song lookup: identification crashed, continuing", exc_info=True)
```

(e) Insert tab correction between `chord_spans = chords_mod.spans_from_labels(labels, grid)` and `detected_key = key_mod.key_from_chords(chord_spans)`:

```python
    chord_source, chord_agreement, tab_url = "audio", None, None
    if identity is not None and chord_spans:
        try:
            from barbershop.lookup.align import apply_tab
            from barbershop.lookup.tabs import fetch_chords

            tab = fetch_chords(identity)
            if tab is not None:
                fixed = apply_tab(chord_spans, tab)
                if fixed is not None:
                    chord_spans = fixed.spans
                    chord_source = "tab"
                    chord_agreement = fixed.agreement
                    tab_url = fixed.url
        except Exception:
            log.info("song lookup: tab correction crashed, continuing", exc_info=True)
```

(f) `AnalysisResult(...)` construction gains `chord_source=chord_source, chord_agreement=chord_agreement, tab_url=tab_url`; the cache-write dict gains `"chord_source": chord_source, "chord_agreement": chord_agreement, "tab_url": tab_url`.

IMPORTANT (call-time binding): inside `apply_tab` is imported at call time, so the monkeypatch target `barbershop.lookup.align.apply_tab` works — do not hoist these imports to module level.

- [ ] **Step 5: Run the analysis + full suites**

Run: `.venv/bin/python -m pytest tests/test_analysis.py -v && .venv/bin/python -m pytest`
Expected: 11 pass in test_analysis.py; full suite = 169 + 4 + 13 (Tasks 1–3) = 186 passed.

- [ ] **Step 6: Commit**

```bash
git add barbershop/analysis/pipeline.py tests/conftest.py tests/test_analysis.py
git commit -m "feat: correct analyzed chords from tab lookup in the pipeline

Identification moves before chord labeling so tab correction can run
before key detection; cache bumps to -v5 with chord provenance.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: API + frontend — `chords` field and sidebar row

**Files:**
- Modify: `backend/app/main.py` (both `analyze()`-calling endpoints)
- Modify: `backend/tests/test_api.py`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Write the failing test** — in `backend/tests/test_api.py`, add one line to `test_upload_happy_path` after the `identity` assert:

```python
    assert body["chords"] == {"source": "audio", "agreement": None, "tab_url": None}
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_api.py::test_upload_happy_path -v`
Expected: FAIL with `KeyError: 'chords'`

- [ ] **Step 3: Implement** — in `backend/app/main.py`, next to each of the two existing `response["identity"] = ...` lines, add:

```python
    response["chords"] = {
        "source": result.chord_source,
        "agreement": result.chord_agreement,
        "tab_url": result.tab_url,
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_api.py -v`
Expected: 13 passed

- [ ] **Step 5: Frontend types** — in `frontend/src/types.ts`, below the `identity?` field:

```ts
  chords?: { source: 'audio' | 'tab'; agreement: number | null; tab_url: string | null }
```

- [ ] **Step 6: Frontend row** — in `frontend/src/App.tsx`, inside the fine-print `<dl>`, directly after the `arrangement.identity && (...)` block:

```tsx
              {arrangement.chords?.source === 'tab' && (
                <>
                  <dt>Chords</dt>
                  <dd>
                    matched tab ({Math.round((arrangement.chords.agreement ?? 0) * 100)}%
                    agreement)
                  </dd>
                </>
              )}
```

- [ ] **Step 7: Verify frontend**

Run: `cd /home/dhaas/barbershopify/frontend && npm run build && npx vitest run`
Expected: build clean, 10 vitest pass.

- [ ] **Step 8: Commit**

```bash
git add backend/app/main.py backend/tests/test_api.py frontend/src/types.ts frontend/src/App.tsx
git commit -m "feat: surface tab-chord provenance in API responses and the sidebar

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

(Run from the repo root so both directories stage.)

---

### Task 6: Docs

**Files:**
- Modify: `README.md` (troubleshooting table; privacy sentence under "Your own song"; test count)
- Modify: `SPEC.md` (extend the song-lookup addendum)
- Modify: `DESIGN.md` (new subsection)

- [ ] **Step 1: README.** Troubleshooting table, after the "Looked-up lyrics are for the wrong song/version" row:

```markdown
| No "Chords — matched tab" line in the sidebar | Normal for most songs: it needs the song identified, a Chordie chord sheet by the same artist, and ≥50% agreement with what the audio analysis heard. Anything less and the chords come purely from audio analysis, as before. |
```

Extend the privacy sentence under **Your own song** to: `When you're online, the app fingerprints the upload (the fingerprint — not your audio — goes to acoustid.org) and, if recognized, pulls the real lyrics from lrclib.net and cross-checks the chords against chordie.com instead of relying on the recording alone. The sidebar shows what was identified and matched.`

Update the test count in *Under the hood* to the number Task 7's full run reports.

- [ ] **Step 2: SPEC.md.** Append to the existing "Addendum (2026-06-11): song lookup" section:

```markdown
Second extension, same date: identified songs also get their chord progression
cross-checked against Chordie. Audio analysis keeps all timing; the tab corrects chord
identities only, and only when the best of 12 transpositions agrees with ≥50% of the
analyzed roots — otherwise the tab is discarded. Ultimate Guitar and the other major tab
sites Cloudflare-block plain requests (probed 2026-06-11); Chordie serves embedded
ChordPro cleanly. Full design: docs/superpowers/specs/2026-06-11-tab-chords-design.md.
```

- [ ] **Step 3: DESIGN.md.** Append under the "Song lookup (2026-06-11)" section:

```markdown
### Tab chords (same date)

**Align-and-relabel, not trust-the-tab.** Tabs are untimed and often transposed, so the
audio keeps every span boundary (which also feeds meter/downbeat detection) and the tab
only corrects identities — the exact error class chroma gets wrong (false minors, wrong
roots, missed sevenths). A global alignment is run at all 12 transpositions (subsuming
capo and off-pitch transfers; the tab's own key/capo metadata is ignored as unreliable),
and the winner must agree with ≥50% of analyzed roots or the tab is rejected wholesale.
There is no path where a tab degrades the chords without first agreeing with half of them.
Key detection runs on the corrected spans, so a fixed false-minor can also fix the key.

**Why Chordie.** The 2026-06-11 probe found Ultimate Guitar, e-chords, UkuTabs, and
Cifra Club all behind Cloudflare for plain requests; Chordie serves 200s and embeds each
song's ChordPro source verbatim. Its search relevance is poor (covers outrank originals),
so candidates are verified against the AcoustID identity ({st:} artist / {t:} title token
overlap) before parsing. maj7 maps to maj6 — barbershop never voices a major seventh.

**Order matters.** Identification now runs right after beat tracking: tab correction must
precede key detection, and lyrics need the identity later anyway. Tempo, meter, and
downbeat phase remain purely audio-derived — a tab has no timing to contribute.
```

- [ ] **Step 4: Commit**

```bash
git add README.md SPEC.md DESIGN.md
git commit -m "docs: tab-chord correction — README, spec addendum, design rationale

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Verification + PR

**Files:** none (verification; fix anything found before pushing)

- [ ] **Step 1: Full backend suite**

Run: `.venv/bin/python -m pytest` (from `backend/`)
Expected: all pass (≈186). Use the exact count to fix the README number from Task 6.

- [ ] **Step 2: Live Chordie probe** (real network — bypasses the conftest stub by calling directly):

```bash
.venv/bin/python -c "
from barbershop.lookup.identify import SongIdentity
from barbershop.lookup.tabs import fetch_chords
ident = SongIdentity(title='Hey Jude', artist='The Beatles', year=1968, recording_mbid='', match_score=1.0)
tab = fetch_chords(ident)
print('tab:', tab.url if tab else None)
print('artist match:', tab.artist if tab else 'n/a', '| chords:', tab.chords[:10] if tab else 'n/a')
"
```
Expected: a Chordie URL with a Beatles chord sheet and a plausible chord list, **or** `None` without a traceback (acceptable — coverage gap); a traceback is the only failure. If the Wilson Pickett cover comes back instead, the artist verification has a bug — stop and fix.

- [ ] **Step 3: Offline fail-soft probe**

```bash
.venv/bin/python -c "
import socket; socket.setdefaulttimeout(0.001)
from barbershop.analysis.pipeline import analyze
r = analyze('../test_songs/down-by-the-old-mill-stream.mp3', use_cache=False, lyrics=False)
print('ok:', r.input.title, '| chords:', r.chord_source)
"
```
Expected: completes, `chords: audio`.

- [ ] **Step 4: Browser regression.** With both servers running (backend 8731, frontend 5280 — restart uvicorn to load new code), click a Victrola song: chart renders, no "Chords" row (unidentified → no tab), no console errors. The positive badge path is covered by injecting `chords: {source:'tab', agreement:0.83, tab_url:'x'}` into an arrangement response via a page-context fetch wrapper (same technique as the identity badge check) and confirming the row renders "matched tab (83% agreement)".

- [ ] **Step 5: Push and PR**

```bash
git push -u origin feat/tab-chords
gh pr create --title "Tab chords: correct analyzed progressions from Chordie" --body "..."
```
PR body: summary of the four units + gate, test plan checkboxes mirroring this task, the probe findings (UG blocked, Chordie open), and the standing footer. Merge per the standing create/merge authorization after the suite is green, then `git checkout main && git pull` and run the suite once more.

---

## Self-review (run by the plan author)

- **Spec coverage:** components (T1–T3) ✓, data flow incl. reorder + cache v5 (T4) ✓, API/UI (T5) ✓, error handling (never-raise tests T2, crash test T4, gate tests T3) ✓, testing section mirrored ✓, docs (T6) ✓, Task 0 probe — done during planning, findings in spec ✓, out-of-scope respected (no boundary edits, no manual override UI) ✓.
- **Placeholders:** Task 7 PR body is summarized rather than verbatim — acceptable (content enumerated); all code steps show full code. One deliberate correction is embedded in Task 3 Step 1 (the `needs_enough_chords` test) — the corrected version is the one to use.
- **Type consistency:** `TabChords(chords, url, artist, title)` in T2/T3/T4; `TabAlignment(spans, agreement, transposition, url)` in T3/T4; `parse_chord -> tuple[int, str] | None` in T1/T3; `AnalysisResult.chord_source/chord_agreement/tab_url` in T4/T5; monkeypatch targets match the call-time import paths noted in T4.
