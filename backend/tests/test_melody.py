"""Melody extraction unit tests — confidence gating on the vocal path.

The mix path (min_voiced_prob=0) must stay byte-for-byte the behavior the
78-rpm analysis-quality suite is tuned to; gating only tightens the
Demucs-separated path, where bleed and reverb read as low-confidence frames.
"""
import numpy as np

from barbershop.analysis import melody as melody_mod


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
