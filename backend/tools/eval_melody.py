"""Ground-truth melody-pitch bake-off: pyin vs RMVPE on vocadito.

Not a unit test — it downloads vocadito (Zenodo, CC-BY) and runs heavy
inference. It is the harness the project uses to justify and tune any
melody-extraction change. Run: python -m tools.eval_melody [N_TRACKS]

Requires the dev deps: pip install -r requirements-dev.txt
"""
from __future__ import annotations

import sys
import warnings

import numpy as np

warnings.filterwarnings("ignore")


def _accompaniment(n: int, sr: int, level: float, seed: int = 0) -> np.ndarray:
    """A spectrally-rich chord+bass+air bed at `level` RMS, to stress trackers."""
    rng = np.random.RandomState(seed)
    t = np.arange(n) / sr
    out = np.zeros(n)
    chords = [[48, 52, 55, 58], [53, 57, 60, 63], [55, 59, 62, 65], [48, 52, 55, 58]]
    seg = int(2.0 * sr)
    for i in range(0, n, seg):
        ch = chords[(i // seg) % len(chords)]
        for midi in ch:
            f = 440 * 2 ** ((midi - 69) / 12)
            for h, amp in ((1, 1.0), (2, 0.4), (3, 0.2)):
                w = np.sin(2 * np.pi * f * h * t[i : i + seg])
                out[i : i + seg] += amp * w[: len(out[i : i + seg])]
        fb = 440 * 2 ** ((ch[0] - 12 - 69) / 12)
        out[i : i + seg] += 1.5 * np.sin(2 * np.pi * fb * t[i : i + seg])[: len(out[i : i + seg])]
    out += 0.15 * rng.randn(n)
    return out * level / (np.sqrt((out**2).mean()) + 1e-9)


def main(n_tracks: int = 15) -> None:
    import librosa
    import mir_eval
    import mirdata

    from barbershop.analysis.melody import _frames_to_notes, consolidate_segments
    from barbershop.analysis.rmvpe import rmvpe_f0

    v = mirdata.initialize("vocadito", data_home="/tmp/vocadito")
    v.download()
    ids = v.track_ids[:n_tracks]

    def to_notes(freq, voiced, times):
        s = consolidate_segments(_frames_to_notes(np.asarray(freq), np.asarray(voiced), np.asarray(times)))
        if not s:
            return np.zeros((0, 2)), np.zeros(0)
        return np.array([[a, b] for _, a, b in s]), librosa.midi_to_hz(np.array([m for m, _, _ in s]))

    def score(gtf, gn, est_t, est_f, est_voiced):
        rpa = mir_eval.melody.evaluate(
            gtf.times, gtf.frequencies, est_t, np.where(est_voiced, est_f, 0.0)
        )["Raw Pitch Accuracy"]
        iv, p = to_notes(est_f, est_voiced, est_t)
        f1 = 0.0 if len(iv) == 0 else mir_eval.transcription.evaluate(gn.intervals, gn.pitches, iv, p)["F-measure_no_offset"]
        return rpa, f1

    for label, with_accomp in (("CLEAN", False), ("+ACCOMP", True)):
        agg = {"pyin": [[], []], "rmvpe": [[], []]}
        for tid in ids:
            t = v.track(tid)
            y22, _ = librosa.load(t.audio_path, sr=22050, mono=True)
            if with_accomp:
                y22 = y22 + _accompaniment(len(y22), 22050, 0.7 * np.sqrt((y22**2).mean()))
            y16 = librosa.resample(y22, orig_sr=22050, target_sr=16000)
            f0, vo, _ = librosa.pyin(y22, fmin=80, fmax=1000, sr=22050, hop_length=256, fill_na=0.0)
            pt = librosa.times_like(f0, sr=22050, hop_length=256)
            r1, r2 = score(t.f0, t.notes_a1, pt, np.nan_to_num(f0), np.asarray(vo))
            agg["pyin"][0].append(r1); agg["pyin"][1].append(r2)
            out = rmvpe_f0(y16, 16000)
            if out is None:
                raise SystemExit("RMVPE unavailable — `pip install rmvpe-onnx` to run the bake-off")
            rt, rf, rc = out
            r1, r2 = score(t.f0, t.notes_a1, rt, rf, rc >= 0.5)
            agg["rmvpe"][0].append(r1); agg["rmvpe"][1].append(r2)
        print(f"\n{label} ({len(ids)} tracks)  {'method':6} {'RPA':>8} {'note-F1':>9}")
        for m in ("pyin", "rmvpe"):
            print(f"{'':22}{m:6} {np.mean(agg[m][0])*100:>7.1f}% {np.mean(agg[m][1]):>9.3f}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 15)
