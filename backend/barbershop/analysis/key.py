"""Global key detection: Krumhansl-Schmuckler profile correlation."""
from __future__ import annotations

import numpy as np

from barbershop.score import KeySig

_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

# tonic pitch class -> key signature fifths (major); minor relative
_MAJOR_FIFTHS = {0: 0, 7: 1, 2: 2, 9: 3, 4: 4, 11: 5, 6: 6, 1: -5, 8: -4, 3: -3, 10: -2, 5: -1}

_MAJOR_STEPS = (0, 2, 4, 5, 7, 9, 11)
_MINOR_STEPS = (0, 2, 3, 5, 7, 8, 10)  # natural minor


def scale_pitch_classes(key: KeySig) -> frozenset[int]:
    """The seven pitch classes of the key's diatonic scale."""
    tonic = (key.fifths * 7) % 12  # major tonic from the signature
    steps = _MAJOR_STEPS
    if key.mode == "minor":
        tonic = (tonic - 3) % 12  # down a minor third to the relative minor
        steps = _MINOR_STEPS
    return frozenset((tonic + s) % 12 for s in steps)


def key_from_chords(spans) -> KeySig | None:
    """Vote the key from smoothed chord labels (duration-weighted diatonic
    membership + cadence bonus). On noisy recordings this beats raw-chroma
    profile correlation, which the smoothing has already denoised."""
    if not spans:
        return None
    # diatonic triad roots: (tonic-relative root pc, quality) per mode
    major_chords = {(0, "maj"), (5, "maj"), (7, "maj"), (7, "dom7"), (2, "min"), (9, "min"), (4, "min")}
    minor_chords = {(0, "min"), (5, "min"), (7, "min"), (7, "dom7"), (8, "maj"), (3, "maj"), (10, "maj"), (2, "dim7")}
    best: tuple[float, int, str] | None = None
    total = sum(c.duration for c in spans)
    for tonic in range(12):
        for mode, table in (("major", major_chords), ("minor", minor_chords)):
            score = 0.0
            for c in spans:
                rel = ((c.root_pc - tonic) % 12, c.quality)
                if rel in table:
                    score += c.duration
                    if rel[0] == 0:  # time spent on the tonic counts double
                        score += 0.5 * c.duration
            for edge in (spans[0], spans[-1]):  # songs start and end home
                if (edge.root_pc - tonic) % 12 == 0:
                    score += 0.15 * total
            if best is None or score > best[0]:
                best = (score, tonic, mode)
    score, tonic, mode = best
    if score < 0.5 * total:
        return None  # not confidently diatonic; let chroma decide
    if mode == "major":
        return KeySig(fifths=_MAJOR_FIFTHS[tonic], mode="major")
    return KeySig(fifths=_MAJOR_FIFTHS[(tonic + 3) % 12], mode="minor")


def detect(chroma_mean: np.ndarray) -> KeySig:
    best: tuple[float, int, str] = (-2.0, 0, "major")
    for tonic in range(12):
        rotated = np.roll(chroma_mean, -tonic)
        for profile, mode in ((_MAJOR, "major"), (_MINOR, "minor")):
            r = float(np.corrcoef(rotated, profile)[0, 1])
            if r > best[0]:
                best = (r, tonic, mode)
    _, tonic, mode = best
    if mode == "major":
        fifths = _MAJOR_FIFTHS[tonic]
    else:
        fifths = _MAJOR_FIFTHS[(tonic + 3) % 12]  # relative major's signature
    return KeySig(fifths=fifths, mode=mode)
