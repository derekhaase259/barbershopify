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
    # align the UNCOMPRESSED sequence: a tab restating C across lines is
    # what lets a false-minor span pair with the real chord instead of a
    # gap; spare repeats just absorb cheap tab-gaps
    seq = [pc for name in tab.chords if (pc := parse_chord(name)) is not None]
    distinct: list[tuple[int, str]] = []
    for item in seq:
        if not distinct or distinct[-1] != item:
            distinct.append(item)
    if len(distinct) < MIN_TAB_CHORDS or not spans:
        return None  # a two-chord ditty can't vouch for anything
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
