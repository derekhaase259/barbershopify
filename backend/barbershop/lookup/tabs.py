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
MAX_CANDIDATES = 10  # two searches' worth of links; fetched once per song, cached
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


def fetch_candidates(identity: SongIdentity) -> list[TabChords]:
    """Chordie chord sheets plausibly of this song: same-artist sheets first,
    then same-title covers (a cover keeps the harmony; the caller's alignment
    gate against the actual audio is the real arbiter). Never raises."""
    try:
        # two searches with different blind spots, merged: title-only finds
        # covers the artist tokens would crowd out, title+artist finds the
        # original when the title query is flooded
        links: list[str] = []
        for q in (identity.title, f"{identity.title} {identity.artist}"):
            page = requests.get(
                f"{CHORDIE_URL}/results.php?q={quote_plus(q)}",
                headers={"User-Agent": USER_AGENT},
                timeout=TIMEOUT,
            )
            page.raise_for_status()
            for link in _SONG_LINK.findall(page.text):
                if link not in links:
                    links.append(link)
        primary: list[TabChords] = []
        covers: list[TabChords] = []
        for link in links[:MAX_CANDIDATES]:
            found = _candidate(link, identity)
            if found is None:
                continue
            tab, artist_matched = found
            (primary if artist_matched else covers).append(tab)
        return primary + covers
    except Exception:
        log.info("song lookup: tab fetch failed, skipping", exc_info=True)
        return []


def _candidate(link: str, identity: SongIdentity) -> tuple[TabChords, bool] | None:
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
    if not _title_matches(title, identity.title):
        return None  # same-title is non-negotiable; artist only affects rank
    chords = parse_chordpro(pro)
    if len(chords) < MIN_TOKENS:
        return None
    tab = TabChords(chords=chords, url=CHORDIE_URL + link, artist=artist, title=title)
    return tab, _overlaps(artist, identity.artist)


def _overlaps(found: str, wanted: str) -> bool:
    return bool(_tokens(found) & _tokens(wanted))


def _title_matches(found: str, wanted: str) -> bool:
    """Majority of the wanted title's tokens must appear: any-overlap lets
    'Jude the Obscene' impersonate 'Hey Jude'."""
    want = _tokens(wanted)
    if not want:
        return False
    return len(_tokens(found) & want) / len(want) > 0.5


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
