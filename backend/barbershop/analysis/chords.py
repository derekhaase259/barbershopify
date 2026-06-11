"""Chord recognition: per-beat chroma templates + Viterbi smoothing."""
from __future__ import annotations

import numpy as np

from barbershop.analysis.beats import BeatGrid
from barbershop.score import ChordSpan

_TEMPLATES: dict[str, tuple[int, ...]] = {
    "maj": (0, 4, 7),
    "min": (0, 3, 7),
    "dom7": (0, 4, 7, 10),
    "dim7": (0, 3, 6, 9),
}

_SELF_TRANSITION_BONUS = 0.15  # reward for staying on a chord; tuned against
# the bundled 78s: 0.35 froze entire songs onto 7 chords, 0.05 jitters


def _states() -> list[tuple[int, str]]:
    return [(root, q) for root in range(12) for q in _TEMPLATES]


def label_beats(y: np.ndarray, sr: int, beat_times: np.ndarray) -> list[tuple[int, str]]:
    """Smoothed per-beat (root_pc, quality) labels."""
    import librosa

    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    frame_times = librosa.times_like(chroma[0], sr=sr)
    beats = beat_times
    if len(beats) < 2:
        return []

    states = _states()
    templates = np.zeros((len(states), 12))
    for s, (root, q) in enumerate(states):
        for iv in _TEMPLATES[q]:
            templates[s, (root + iv) % 12] = 1.0
    templates /= np.linalg.norm(templates, axis=1, keepdims=True)

    # per-beat averaged chroma -> emission scores
    n_beats = len(beats) - 1
    emissions = np.zeros((n_beats, len(states)))
    for b in range(n_beats):
        mask = (frame_times >= beats[b]) & (frame_times < beats[b + 1])
        v = chroma[:, mask].mean(axis=1) if mask.any() else np.zeros(12)
        norm = np.linalg.norm(v)
        if norm > 0:
            v = v / norm
        emissions[b] = templates @ v

    # Viterbi with a flat transition matrix + self-transition bonus
    cost = emissions[0].copy()
    back = np.zeros((n_beats, len(states)), dtype=int)
    for b in range(1, n_beats):
        stay = cost + _SELF_TRANSITION_BONUS
        move = cost.max()
        best_prev = int(np.argmax(cost))
        for s in range(len(states)):
            if stay[s] >= move:
                back[b, s] = s
                newcost = stay[s]
            else:
                back[b, s] = best_prev
                newcost = move
            cost[s] = newcost + emissions[b, s]
    path = [int(np.argmax(cost))]
    for b in range(n_beats - 1, 0, -1):
        path.append(int(back[b, path[-1]]))
    path.reverse()
    return [states[s] for s in path]


def best_downbeat_phase(labels: list[tuple[int, str]], beats_per_measure: int = 4) -> int:
    """The beat phase where chord changes most often land: that's beat one."""
    if len(labels) < beats_per_measure * 2:
        return 0
    scores = [0] * beats_per_measure
    for b in range(1, len(labels)):
        if labels[b] != labels[b - 1]:
            scores[b % beats_per_measure] += 1
    return int(np.argmax(scores))


def best_meter_and_phase(labels: list[tuple[int, str]]) -> tuple[int, int]:
    """Vote 3/4 vs 4/4 by where chord changes cluster. Concentration is
    compared as lift above chance (1/meter), since a 3-beat bar
    concentrates more by accident than a 4-beat one."""
    changes = [b for b in range(1, len(labels)) if labels[b] != labels[b - 1]]
    if len(changes) < 4:
        return 4, best_downbeat_phase(labels)
    best = (4, 0, -1.0)
    for meter in (3, 4):
        scores = [0] * meter
        for b in changes:
            scores[b % meter] += 1
        phase = int(np.argmax(scores))
        lift = scores[phase] / len(changes) - 1.0 / meter
        if lift > best[2]:
            best = (meter, phase, lift)
    return best[0], best[1]


def spans_from_labels(labels: list[tuple[int, str]], grid: BeatGrid) -> list[ChordSpan]:
    """Merge per-beat labels into ChordSpans on the tick grid."""
    beats = grid.beat_times
    spans: list[ChordSpan] = []
    for b, (root, quality) in enumerate(labels):
        onset = int(round(grid.time_to_tick(beats[b]) / 240)) * 240
        end = int(round(grid.time_to_tick(beats[b + 1]) / 240)) * 240
        if end <= onset or end <= 0:
            continue
        onset = max(0, onset)
        if spans and spans[-1].root_pc == root and spans[-1].quality == quality and spans[-1].end >= onset:
            spans[-1].duration = end - spans[-1].onset
        else:
            spans.append(ChordSpan(onset=onset, duration=end - onset, root_pc=root, quality=quality))
    return spans
