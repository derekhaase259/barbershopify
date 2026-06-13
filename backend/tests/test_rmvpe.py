"""RMVPE is an optional booster — every failure path returns None so the
caller falls back to pyin."""
import numpy as np

from barbershop.analysis import rmvpe


def test_rmvpe_f0_failsoft_on_model_error(monkeypatch):
    def boom():
        raise RuntimeError("onnx model download exploded")

    monkeypatch.setattr(rmvpe, "_get_model", boom)
    assert rmvpe.rmvpe_f0(np.zeros(16000, dtype="float32"), 16000) is None


def test_rmvpe_f0_returns_triplet_when_model_works(monkeypatch):
    class FakeModel:
        def predict(self, audio, sr):
            n = 5
            return np.arange(n) * 0.01, np.full(n, 220.0), np.full(n, 0.9), None

    monkeypatch.setattr(rmvpe, "_get_model", lambda: FakeModel())
    out = rmvpe.rmvpe_f0(np.zeros(32000, dtype="float32"), 16000)
    assert out is not None
    times, freq, conf = out
    assert len(times) == len(freq) == len(conf) == 5
    assert freq[0] == 220.0
