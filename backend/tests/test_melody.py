"""Melody extraction unit tests — confidence gating on the vocal path.

The mix path (min_voiced_prob=0) must stay byte-for-byte the behavior the
78-rpm analysis-quality suite is tuned to; gating only tightens the
Demucs-separated path, where bleed and reverb read as low-confidence frames.
"""
import numpy as np

from barbershop.analysis import melody as melody_mod
from barbershop.analysis.key import scale_pitch_classes
from barbershop.score import KeySig, Note


def _fake_pyin(f0, voiced, prob):
    """Return a stand-in for librosa.pyin yielding fixed frames."""
    def pyin(y, *, fmin, fmax, sr, hop_length, fill_na):
        return np.asarray(f0), np.asarray(voiced), np.asarray(prob)
    return pyin


def test_gate_drops_low_confidence_frames(monkeypatch):
    # 15 frames of a confident A3, then 15 of a *spurious* A4 at low
    # confidence (the kind of octave grab separation bleed produces)
    n = 15
    f0 = np.array([220.0] * n + [440.0] * n + [0.0] * 10)
    voiced = np.array([True] * (2 * n) + [False] * 10)
    prob = np.array([0.9] * n + [0.3] * n + [0.0] * 10)
    monkeypatch.setattr("librosa.pyin", _fake_pyin(f0, voiced, prob))

    ungated = melody_mod.extract_segments(np.zeros(8000), 22050)
    gated = melody_mod.extract_segments(np.zeros(8000), 22050, min_voiced_prob=0.5)

    # without gating both notes survive; gating removes the low-confidence A4
    assert len(ungated) == 2
    assert len(gated) == 1
    assert round(gated[0][0]) == 57  # A3, the confident note


def test_default_path_ignores_confidence(monkeypatch):
    # default min_voiced_prob=0 must not gate, whatever the probabilities
    n = 15
    f0 = np.array([220.0] * n + [440.0] * n)
    voiced = np.array([True] * (2 * n))
    prob = np.array([0.01] * (2 * n))  # all "low" — yet nothing is dropped
    monkeypatch.setattr("librosa.pyin", _fake_pyin(f0, voiced, prob))
    assert len(melody_mod.extract_segments(np.zeros(8000), 22050)) == 2


def test_scale_pitch_classes_db_major():
    # D-flat major (five flats): Db Eb F Gb Ab Bb C
    assert scale_pitch_classes(KeySig(fifths=-5, mode="major")) == {1, 3, 5, 6, 8, 10, 0}


def test_scale_pitch_classes_a_minor():
    assert scale_pitch_classes(KeySig(fifths=0, mode="minor")) == {9, 11, 0, 2, 4, 5, 7}


def test_snap_to_key_pulls_out_of_key_notes_onto_the_scale():
    key = KeySig(fifths=0, mode="major")  # C major: out-of-key = C#,D#,F#,G#,A#
    notes = [
        Note(onset=0, duration=240, midi=60),    # C — in key, untouched
        Note(onset=240, duration=240, midi=61),   # C# — snap by one semitone
        Note(onset=480, duration=240, midi=66),   # F# — snap by one semitone
        Note(onset=720, duration=240, midi=64),   # E — in key, untouched
    ]
    out = melody_mod.snap_to_key(notes, key)
    pcs = scale_pitch_classes(key)
    assert all(n.midi % 12 in pcs for n in out)       # everything now diatonic
    assert out[0].midi == 60 and out[3].midi == 64    # in-key notes unchanged
    assert all(abs(o.midi - m) <= 1 for o, m in zip(out, [60, 61, 66, 64]))  # ≤1 semitone moves
