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
