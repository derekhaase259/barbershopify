"""RMVPE vocal pitch: accompaniment-robust f0 from the raw mixture.

librosa.pyin is monophonic and collapses under accompaniment (it tracks the
loudest source); RMVPE finds the vocal in the full mix. The ONNX model is
heavy, so it is lazy-imported and cached; the whole module is fail-soft —
any failure returns None and the caller falls back to pyin.
"""
from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger(__name__)

RMVPE_SR = 16000  # the model's native sample rate
_model = None


def _get_model():
    global _model
    if _model is None:
        from rmvpe_onnx import RMVPE

        _model = RMVPE()
    return _model


def rmvpe_f0(y: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """(times, frequencies_hz, confidence) at RMVPE's 16 kHz frame rate, or
    None on any failure (package missing, model download failed, onnx error)."""
    try:
        import librosa

        if sr != RMVPE_SR:
            y = librosa.resample(np.asarray(y, dtype="float32"), orig_sr=sr, target_sr=RMVPE_SR)
        times, freq, conf, _ = _get_model().predict(audio=np.asarray(y, dtype="float32"), sr=RMVPE_SR)
        return np.asarray(times), np.asarray(freq), np.asarray(conf)
    except Exception:
        log.info("RMVPE unavailable; falling back to pyin", exc_info=True)
        return None
