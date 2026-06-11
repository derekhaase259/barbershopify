"""Unit tests for the analysis-quality fixes driven by the test-78 audit:
double-time tempo correction, segment consolidation, meter detection,
and chord-derived key voting."""
import numpy as np

from barbershop.analysis.beats import BeatGrid
from barbershop.analysis.chords import best_meter_and_phase
from barbershop.analysis.key import key_from_chords
from barbershop.analysis.melody import consolidate_segments
from barbershop.analysis.pipeline import correct_tempo_level
from barbershop.score import ChordSpan


def make_grid(bpm: float, n_beats: int = 64) -> BeatGrid:
    period = 60 / bpm
    return BeatGrid(tempo=bpm, beat_times=np.arange(n_beats) * period, downbeat_phase=0)


# --- double-time correction ---------------------------------------------------

def _labels_with_span(span_beats: int, n: int = 64):
    return [((b // span_beats) % 12, "maj") for b in range(n)]


def test_fast_grid_with_static_chords_gets_halved():
    # ballad locked at 129 BPM: chords "hold" 8 detected beats = 2 real bars
    grid = make_grid(129.2)
    corrected = correct_tempo_level(grid, _labels_with_span(8))
    assert abs(corrected.tempo - 64.6) < 0.1
    assert len(corrected.beat_times) == 32


def test_fast_grid_with_normal_harmonic_rhythm_is_kept():
    # a brisk waltz really does run ~130: chords change every 3 beats
    grid = make_grid(129.2)
    corrected = correct_tempo_level(grid, _labels_with_span(3))
    assert corrected.tempo == 129.2


def test_moderate_tempo_is_never_halved():
    grid = make_grid(96)
    corrected = correct_tempo_level(grid, _labels_with_span(8))
    assert corrected.tempo == 96


# --- segment consolidation ----------------------------------------------------

def test_micro_gaps_between_same_pitch_segments_merge():
    # vibrato/noise chops one sung note into shards with ~30ms gaps
    shards = [(64.0, 0.0, 0.30), (64.1, 0.33, 0.62), (63.9, 0.65, 1.0)]
    merged = consolidate_segments(shards)
    assert len(merged) == 1
    midi, t0, t1 = merged[0]
    assert round(midi) == 64 and t0 == 0.0 and t1 == 1.0


def test_real_gaps_and_pitch_changes_stay_separate():
    segs = [(64.0, 0.0, 0.4), (64.0, 0.6, 1.0), (67.0, 1.0, 1.4)]
    merged = consolidate_segments(segs)
    assert len(merged) == 3


def test_ultra_short_shards_are_dropped():
    segs = [(60.0, 0.0, 0.05), (64.0, 0.1, 0.8)]
    merged = consolidate_segments(segs)
    assert len(merged) == 1 and round(merged[0][0]) == 64


# --- meter detection ----------------------------------------------------------

def test_waltz_chord_changes_vote_for_three_four():
    labels = []
    for bar in range(16):
        labels.extend([(bar % 4, "maj")] * 3)  # chord changes every 3 beats
    beats, phase = best_meter_and_phase(labels)
    assert beats == 3 and phase == 0


def test_common_time_chord_changes_vote_for_four_four():
    labels = []
    for bar in range(12):
        labels.extend([(bar % 4, "maj")] * 4)
    beats, phase = best_meter_and_phase(labels)
    assert beats == 4 and phase == 0


# --- chord-derived key --------------------------------------------------------

def _spans(items):
    out, t = [], 0
    for root, quality, dur in items:
        out.append(ChordSpan(onset=t, duration=dur, root_pc=root, quality=quality))
        t += dur
    return out


def test_major_progression_votes_major_key():
    # I-IV-V7-I in F major
    spans = _spans([(5, "maj", 1920), (10, "maj", 1920), (0, "dom7", 1920), (5, "maj", 3840)])
    key = key_from_chords(spans)
    assert key.mode == "major" and key.fifths == -1


def test_minor_progression_votes_minor_key():
    # i-iv-V7-i in A minor
    spans = _spans([(9, "min", 1920), (2, "min", 1920), (4, "dom7", 1920), (9, "min", 3840)])
    key = key_from_chords(spans)
    assert key.mode == "minor" and key.fifths == 0
