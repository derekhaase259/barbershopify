"""Melody extraction: pyin f0 -> note events -> beat-grid quantization.

librosa.pyin is the default (pure-python stack, deterministic, suits
melody-dominant material). basic-pitch / demucs are deliberate non-
defaults — see DESIGN.md.
"""
from __future__ import annotations

import numpy as np

from barbershop.analysis.beats import BeatGrid
from barbershop.score import Note

HOP = 256
GRID = 240  # eighth-note resolution by default (480 = quarter)


def _frames_to_notes(
    f0: np.ndarray, voiced: np.ndarray, times: np.ndarray
) -> list[tuple[float, float, float]]:
    """Group voiced frames into (midi, t0, t1) segments, splitting on jumps."""
    notes: list[tuple[float, float, float]] = []
    midi = 69 + 12 * np.log2(np.where(f0 > 0, f0, 1) / 440.0)
    start = None
    pitches: list[float] = []
    for i in range(len(f0)):
        ok = bool(voiced[i]) and f0[i] > 0
        if ok and start is None:
            start, pitches = i, [midi[i]]
        elif ok:
            if abs(midi[i] - float(np.median(pitches))) > 0.6:
                notes.append((float(np.median(pitches)), times[start], times[i]))
                start, pitches = i, [midi[i]]
            else:
                pitches.append(midi[i])
        elif start is not None:
            notes.append((float(np.median(pitches)), times[start], times[i]))
            start, pitches = None, []
    if start is not None:
        notes.append((float(np.median(pitches)), times[start], times[-1]))
    return notes


def consolidate_segments(
    segments: list[tuple[float, float, float]],
    *,
    max_gap: float = 0.12,
    pitch_tolerance: float = 0.8,
    min_duration: float = 0.09,
) -> list[tuple[float, float, float]]:
    """Heal pyin fragmentation: vibrato and surface noise chop one sung
    note into shards. Merge same-pitch neighbors across micro-gaps
    (true legato breaks are longer), then drop ultra-short slivers."""
    merged: list[list[float]] = []
    for midi, t0, t1 in segments:
        if (
            merged
            and t0 - merged[-1][2] <= max_gap
            and abs(midi - merged[-1][0]) <= pitch_tolerance
        ):
            prev = merged[-1]
            total = (prev[2] - prev[1]) + (t1 - t0)
            prev[0] = (prev[0] * (prev[2] - prev[1]) + midi * (t1 - t0)) / total
            prev[2] = t1
        else:
            merged.append([midi, t0, t1])
    return [(m, a, b) for m, a, b in merged if b - a >= min_duration]


def _fold_octave_outliers(notes: list[Note], window: int = 5, tolerance: int = 7) -> None:
    """pyin's classic failure is the octave jump: fold notes that sit more
    than a fifth from their local median back toward it, in octaves."""
    for i, note in enumerate(notes):
        neighbors = [n.midi for n in notes[max(0, i - window) : i]] or [note.midi]
        med = float(np.median(neighbors))
        while note.midi - med > tolerance:
            note.midi -= 12
        while med - note.midi > tolerance + 5:  # asymmetric: low notes are rarer errors
            note.midi += 12


def extract_segments(
    y: np.ndarray,
    sr: int,
    *,
    fmin: float = 110.0,
    fmax: float = 1000.0,
    min_voiced_prob: float = 0.0,
) -> list[tuple[float, float, float]]:
    """Raw pyin (midi, t0, t1) segments in seconds, fragmentation healed.
    Separated from quantization so the pipeline can use real note
    durations to sanity-check the beat tracker's metrical level.

    ``min_voiced_prob`` gates frames on pyin's voicing confidence — left at
    0 it is a no-op (the default mix path is unchanged). The vocal-isolation
    path raises it: on a Demucs-separated stem the bleed and reverb tails
    that survive read as low-confidence frames, and dropping them is what
    turns the raw f0 into a singable line (validated on a dense duet mix:
    octave-jump errors fell by more than half)."""
    import librosa

    f0, voiced, voiced_prob = librosa.pyin(
        y, fmin=fmin, fmax=fmax, sr=sr, hop_length=HOP, fill_na=0.0
    )
    voiced = np.asarray(voiced)
    if min_voiced_prob > 0.0:
        voiced = voiced & (np.asarray(voiced_prob) >= min_voiced_prob)
    times = librosa.times_like(f0, sr=sr, hop_length=HOP)
    raw = _frames_to_notes(np.asarray(f0), voiced, np.asarray(times))
    return consolidate_segments(raw)


def quantize(segments: list[tuple[float, float, float]], grid: BeatGrid) -> list[Note]:
    out: list[Note] = []
    for midi, t0, t1 in segments:
        a = grid.time_to_tick(t0)
        b = grid.time_to_tick(t1)
        onset = int(round(a / GRID)) * GRID
        end = int(round(b / GRID)) * GRID
        if onset < 0 or end - onset < GRID:  # too short / before downbeat
            if b - a < GRID * 0.45 or onset < 0:
                continue
            end = onset + GRID
        note = Note(onset=onset, duration=end - onset, midi=int(round(midi)))
        if out and note.onset < out[-1].end:
            if note.onset == out[-1].onset:  # quantized onto the previous note
                continue
            out[-1].duration = note.onset - out[-1].onset
        out.append(note)
    _fold_octave_outliers(out)
    return out


def extract(y: np.ndarray, sr: int, grid: BeatGrid, *, fmin: float = 110.0, fmax: float = 1000.0) -> list[Note]:
    return quantize(extract_segments(y, sr, fmin=fmin, fmax=fmax), grid)
