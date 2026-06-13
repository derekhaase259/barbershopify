"""The barbershop chord vocabulary (BHS-style consonance hierarchy).

Every vertical sonority in a finished chart must be classifiable here.
Qualities are defined by intervals above the root; a *realization* is a
legal way to cover four voices with those tones (4-tone chords: all four,
no doubling or omission; triads: triad plus one doubling; dom9: rootless
or fifth-omitted four-voice subsets).

Doubled thirds are classifiable but the doubling is reported so the
validator can reject them at points of repose.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple


class Interpretation(NamedTuple):
    root_pc: int
    quality: str
    doubled_pc: int | None  # pitch class that is doubled (None = complete 4-tone chord)


@dataclass(frozen=True)
class ChordDef:
    quality: str
    tier: int
    intervals: tuple[int, ...]  # chord tones, semitones above root
    degrees: dict[int, str]  # interval -> degree name
    # sorted 4-interval multiset -> doubled interval (None if no doubling)
    realizations: dict[tuple[int, ...], int | None] = field(default_factory=dict)


def _triad(quality: str, tier: int, third: int) -> ChordDef:
    intervals = (0, third, 7)
    return ChordDef(
        quality=quality,
        tier=tier,
        intervals=intervals,
        degrees={0: "root", third: "third", 7: "fifth"},
        realizations={
            (0, 0, third, 7): 0,
            (0, third, 7, 7): 7,
            (0, third, third, 7): third,
        },
    )


def _four_tone(quality: str, tier: int, intervals: tuple[int, ...], top: str) -> ChordDef:
    names = ["root", "third", "fifth", top]
    realizations: dict[tuple[int, ...], int | None] = {tuple(sorted(intervals)): None}
    if 7 in intervals:
        # The perfect fifth is the expendable tone: it may be dropped and the
        # root doubled (root-root-third-seventh). This is the standard
        # incomplete voicing when bass and lead both need the root — e.g. a
        # final root-position chord whose lead sits on the root. Chords with
        # an altered fifth (dim7/halfdim7/dom7b5/aug7) keep it; their fifth is
        # characteristic. Penalized in voicing so it is only a forced fallback.
        root, third, _fifth, top_iv = intervals
        realizations[tuple(sorted((root, root, third, top_iv)))] = root  # doubled root
    return ChordDef(
        quality=quality,
        tier=tier,
        intervals=intervals,
        degrees=dict(zip(intervals, names)),
        realizations=realizations,
    )


CHORDS: dict[str, ChordDef] = {
    # Tier 1 — the sound
    "maj": _triad("maj", 1, 4),
    "dom7": _four_tone("dom7", 1, (0, 4, 7, 10), "seventh"),
    # Tier 2 — idiomatic color
    "maj6": _four_tone("maj6", 2, (0, 4, 7, 9), "sixth"),
    "min": _triad("min", 2, 3),
    "min6": _four_tone("min6", 2, (0, 3, 7, 9), "sixth"),
    "min7": _four_tone("min7", 2, (0, 3, 7, 10), "seventh"),
    "dom9": ChordDef(
        quality="dom9",
        tier=2,
        intervals=(0, 4, 7, 10, 2),
        degrees={0: "root", 4: "third", 7: "fifth", 10: "seventh", 2: "ninth"},
        realizations={
            (2, 4, 7, 10): None,  # rootless
            (0, 2, 4, 10): None,  # fifth omitted
        },
    ),
    "dim7": _four_tone("dim7", 2, (0, 3, 6, 9), "seventh"),
    "halfdim7": _four_tone("halfdim7", 2, (0, 3, 6, 10), "seventh"),
    # Tier 3 — spice
    "aug": ChordDef(
        quality="aug",
        tier=3,
        intervals=(0, 4, 8),
        degrees={0: "root", 4: "third", 8: "fifth"},
        realizations={(0, 0, 4, 8): 0},
    ),
    "dom7b5": _four_tone("dom7b5", 3, (0, 4, 6, 10), "seventh"),
    "aug7": _four_tone("aug7", 3, (0, 4, 8, 10), "seventh"),
}


def chord_pcs(root_pc: int, quality: str) -> frozenset[int]:
    """Pitch classes of a chord built on root_pc."""
    return frozenset((root_pc + i) % 12 for i in CHORDS[quality].intervals)


def chord_degree(root_pc: int, quality: str, pc: int) -> str | None:
    """Degree name ('root'/'third'/...) of pc within the chord, or None."""
    return CHORDS[quality].degrees.get((pc - root_pc) % 12)


def classify(pcs: tuple[int, ...]) -> list[Interpretation]:
    """All vocabulary readings of a 4-voice sonority (empty = illegal)."""
    if len(pcs) != 4:
        raise ValueError(f"expected 4 pitch classes, got {len(pcs)}")
    out: list[Interpretation] = []
    for root in range(12):
        rel = tuple(sorted((p - root) % 12 for p in pcs))
        for quality, cdef in CHORDS.items():
            if rel in cdef.realizations:
                d = cdef.realizations[rel]
                doubled = None if d is None else (root + d) % 12
                interp = Interpretation(root, quality, doubled)
                if interp not in out:
                    out.append(interp)
    return out
