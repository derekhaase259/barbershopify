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
