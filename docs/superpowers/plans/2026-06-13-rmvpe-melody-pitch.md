# RMVPE Melody Pitch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `librosa.pyin` with RMVPE (accompaniment-robust, mixture-native) as the melody pitch source, run on the raw mix, with pyin as a fail-soft fallback.

**Architecture:** A new fail-soft `rmvpe.py` wraps `rmvpe-onnx`; `melody.extract_segments_rmvpe` runs RMVPE on the mix, gates frames by confidence, and reuses the existing note former — falling back to pyin if RMVPE returns `None`. `analyze()` calls it instead of the pyin/Demucs routing; the `separate_vocal` feature (now superseded) is removed; `snap_to_key` stays. A committed eval harness validates it against ground truth.

**Tech Stack:** Python 3.12, `rmvpe-onnx` (ONNX/CPU), existing numpy/librosa, pytest; dev-only `mir_eval`/`mirdata`. Spec: `docs/superpowers/specs/2026-06-13-rmvpe-melody-design.md`.

---

## File Structure

- Create `backend/barbershop/analysis/rmvpe.py` — fail-soft RMVPE f0 wrapper.
- Create `backend/tests/test_rmvpe.py` — wrapper fail-soft tests.
- Create `backend/tools/eval_melody.py` — ground-truth bake-off (dev tool).
- Modify `backend/barbershop/analysis/melody.py` — add `extract_segments_rmvpe`.
- Modify `backend/barbershop/analysis/pipeline.py` — RMVPE-on-mix; remove `separate_vocal`; cache v8.
- Modify `backend/app/main.py` — remove the `separate` upload param.
- Modify `backend/tests/conftest.py` — stub `rmvpe_f0` → None suite-wide.
- Modify `backend/tests/test_analysis.py`, `backend/tests/test_api.py` — drop `separate_vocal` tests, add RMVPE-routing tests.
- Modify `backend/requirements.txt`, `SPEC.md`, `DESIGN.md`.

All commands run from `backend/`. Runner: `.venv/bin/pytest`. Current branch: `feat/rmvpe-melody-pitch`.

---

## Task 1: Resolve the two gating risks (diligence)

This decides only whether `rmvpe-onnx` is **pinned** (default) or **documented as optional** (opt-in). The fail-soft code is identical either way: if `rmvpe-onnx` isn't installed, `rmvpe_f0` returns `None` and pyin runs.

- [ ] **Step 1: Install and observe the onnxruntime change**

```bash
.venv/bin/pip install "rmvpe-onnx==0.2.3"
.venv/bin/pip show onnxruntime | grep Version   # expect 1.26.x (bumped from 1.23.2)
```

- [ ] **Step 2: Runtime-test faster-whisper ASR under the bumped onnxruntime**

```bash
.venv/bin/python - <<'PY'
import numpy as np, soundfile as sf, tempfile, os
from faster_whisper import WhisperModel
# 3s of speech-like tone sweep; we only care that transcription RUNS without error
sr=16000; t=np.linspace(0,3,sr*3); y=(0.2*np.sin(2*np.pi*(200+50*np.sin(2*np.pi*3*t))*t)).astype('float32')
p=tempfile.mktemp(suffix='.wav'); sf.write(p,y,sr)
m=WhisperModel("tiny", device="cpu", compute_type="int8")
segs,_=m.transcribe(p); list(segs); os.unlink(p)
print("ASR ran OK under onnxruntime", __import__("onnxruntime").__version__)
PY
```
Expected: prints "ASR ran OK ...". If it raises, ASR is incompatible with 1.26 → RMVPE ships opt-in (Step 4b).

- [ ] **Step 3: Vet the checkpoint license**

```bash
.venv/bin/rmvpe-onnx download 2>&1 | tail -5   # note where the checkpoint comes from
.venv/bin/python -c "import rmvpe_onnx, os; print(os.path.dirname(rmvpe_onnx.__file__))"
```
Then check the source repo/model card of the downloaded checkpoint for a redistribution license. Record the finding in `DESIGN.md` (Task 7).

- [ ] **Step 4: Record the decision (drives Task 5's requirements.txt)**

- **4a (default):** ASR works under 1.26 AND the checkpoint is redistributable → pin `rmvpe-onnx` and bump `onnxruntime` in Task 5.
- **4b (opt-in):** ASR breaks under 1.26 OR the checkpoint isn't redistributable → do NOT pin `rmvpe-onnx`; document it as an optional install. The fail-soft code already degrades to pyin when it's absent. Note this in `DESIGN.md`.

No commit (no code yet).

---

## Task 2: The RMVPE wrapper

**Files:**
- Create: `backend/barbershop/analysis/rmvpe.py`
- Test: `backend/tests/test_rmvpe.py`

- [ ] **Step 1: Write the failing fail-soft test**

Create `backend/tests/test_rmvpe.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_rmvpe.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'barbershop.analysis.rmvpe'`.

- [ ] **Step 3: Write the wrapper**

Create `backend/barbershop/analysis/rmvpe.py`:

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/test_rmvpe.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/barbershop/analysis/rmvpe.py backend/tests/test_rmvpe.py
git commit -m "feat: fail-soft RMVPE vocal-pitch wrapper"
```

---

## Task 3: RMVPE extraction path in melody.py

**Files:**
- Modify: `backend/barbershop/analysis/melody.py`
- Test: `backend/tests/test_melody.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_melody.py`:

```python
def test_extract_segments_rmvpe_uses_rmvpe_when_available(monkeypatch):
    # a steady 220 Hz (A3) for ~1s at RMVPE's 16 kHz frame hop, all confident
    n = 100
    times = np.arange(n) * 0.01
    freq = np.full(n, 220.0)
    conf = np.full(n, 0.9)
    monkeypatch.setattr(
        "barbershop.analysis.rmvpe.rmvpe_f0", lambda y, sr: (times, freq, conf)
    )
    segs = melody_mod.extract_segments_rmvpe(np.zeros(16000, dtype="float32"), 16000)
    assert segs and all(round(m) == 57 for m, _, _ in segs)  # A3 = MIDI 57


def test_extract_segments_rmvpe_gates_low_confidence(monkeypatch):
    n = 100
    times = np.arange(n) * 0.01
    freq = np.full(n, 220.0)
    conf = np.full(n, 0.2)  # all below threshold -> nothing voiced
    monkeypatch.setattr(
        "barbershop.analysis.rmvpe.rmvpe_f0", lambda y, sr: (times, freq, conf)
    )
    assert melody_mod.extract_segments_rmvpe(np.zeros(16000, dtype="float32"), 16000) == []


def test_extract_segments_rmvpe_falls_back_to_pyin(monkeypatch):
    # rmvpe unavailable -> identical to the pyin path on the same audio
    monkeypatch.setattr("barbershop.analysis.rmvpe.rmvpe_f0", lambda y, sr: None)
    y = np.zeros(8000, dtype="float32")

    def fake_pyin(yy, *, fmin, fmax, sr, hop_length, fill_na):
        f = np.full(60, 220.0)
        return f, np.ones(60, bool), np.ones(60)

    monkeypatch.setattr("librosa.pyin", fake_pyin)
    segs = melody_mod.extract_segments_rmvpe(y, 22050)
    assert segs == melody_mod.extract_segments(y, 22050)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_melody.py -q -k rmvpe`
Expected: FAIL — `AttributeError: module 'barbershop.analysis.melody' has no attribute 'extract_segments_rmvpe'`.

- [ ] **Step 3: Implement `extract_segments_rmvpe`**

In `backend/barbershop/analysis/melody.py`, add after `extract_segments` (keep `extract_segments` as-is — it is the pyin fallback):

```python
RMVPE_MIN_CONFIDENCE = 0.5  # frames below this are treated as unvoiced


def extract_segments_rmvpe(
    y: np.ndarray, sr: int, *, min_confidence: float = RMVPE_MIN_CONFIDENCE
) -> list[tuple[float, float, float]]:
    """Melody segments from RMVPE on the raw mix (accompaniment-robust), with
    a pyin fallback when RMVPE is unavailable. Fragmentation is healed the
    same way as the pyin path."""
    from barbershop.analysis.rmvpe import rmvpe_f0

    out = rmvpe_f0(y, sr)
    if out is None:
        return extract_segments(y, sr)  # pyin on the mix
    times, freq, conf = out
    voiced = np.asarray(conf) >= min_confidence
    return consolidate_segments(
        _frames_to_notes(np.asarray(freq), voiced, np.asarray(times))
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/test_melody.py -q`
Expected: PASS (all melody tests).

- [ ] **Step 5: Commit**

```bash
git add backend/barbershop/analysis/melody.py backend/tests/test_melody.py
git commit -m "feat: RMVPE melody extraction path with pyin fallback"
```

---

## Task 4: Wire RMVPE into the pipeline; remove the superseded separate_vocal

**Files:**
- Modify: `backend/barbershop/analysis/pipeline.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/conftest.py`
- Modify: `backend/tests/test_analysis.py`, `backend/tests/test_api.py`

- [ ] **Step 1: Stub RMVPE suite-wide so CI never downloads the model**

In `backend/tests/conftest.py`, extend the autouse fixture:

```python
@pytest.fixture(autouse=True)
def _no_song_lookup(monkeypatch):
    monkeypatch.setattr("barbershop.lookup.identify.identify", lambda *a, **k: None)
    monkeypatch.setattr("barbershop.lookup.lyrics.fetch_lyrics", lambda *a, **k: None)
    monkeypatch.setattr("barbershop.lookup.tabs.fetch_candidates", lambda *a, **k: [])
    monkeypatch.setattr("barbershop.analysis.rmvpe.rmvpe_f0", lambda y, sr: None)
```

- [ ] **Step 2: Update the analysis-pipeline tests (TDD: they now describe the new routing)**

In `backend/tests/test_analysis.py`, DELETE `test_separate_vocal_extracts_melody_from_the_stem` and `test_separate_vocal_falls_back_to_mix_when_unavailable`. Add in their place:

```python
def test_melody_comes_from_rmvpe_when_available(test_wav, monkeypatch):
    # a steady B natural (pc 11) the C-major mix melody never sounds; in key,
    # so snap_to_key leaves it — its dominance proves RMVPE drove the melody.
    # ~8 s of frames at RMVPE's 10 ms hop, matching the test_wav duration.
    n = 800
    times = np.arange(n) * 0.01
    freq = np.full(n, 493.88)  # B4
    conf = np.full(n, 0.9)
    monkeypatch.setattr("barbershop.analysis.rmvpe.rmvpe_f0", lambda y, sr: (times, freq, conf))
    result = analyze(str(test_wav), use_cache=False)
    pcs = [n.midi % 12 for n in result.input.melody]
    assert pcs and sum(p == 11 for p in pcs) / len(pcs) > 0.5
    assert result.input.key.fifths == 0  # key still from the mix


def test_melody_falls_back_to_pyin_when_rmvpe_absent(test_wav, monkeypatch):
    monkeypatch.setattr("barbershop.analysis.rmvpe.rmvpe_f0", lambda y, sr: None)
    result = analyze(str(test_wav), use_cache=False)
    pcs = [n.midi % 12 for n in result.input.melody]
    assert pcs and sum(p == 11 for p in pcs) / len(pcs) < 0.2  # no B-natural takeover
```

- [ ] **Step 3: Run to verify failure**

Run: `.venv/bin/pytest tests/test_analysis.py -q -k "rmvpe or pyin_when"`
Expected: FAIL — `analyze()` still has the old routing; the RMVPE-driven test fails.

- [ ] **Step 4: Rewire `pipeline.analyze()`**

In `backend/barbershop/analysis/pipeline.py`:

(a) Delete the `_VOCAL_FMAX` / `_VOCAL_VOICED_PROB` constants near the top.

(b) Change the signature and cache key — remove `separate_vocal`:

```python
def analyze(
    path: str,
    *,
    title: str | None = None,
    use_cache: bool = True,
    lyrics: bool = True,
    lookup: bool = True,
) -> AnalysisResult:
    cache_file = CACHE_DIR / f"{_cache_key(path)}-v8.json"
```

(c) Replace the separate-vocal melody block with the RMVPE call:

```python
    segments = melody_mod.extract_segments_rmvpe(y, sr)  # RMVPE on the mix, pyin fallback inside
    melody = melody_mod.quantize(segments, grid)
    chord_spans = chords_mod.spans_from_labels(labels, grid)
```

(`snap_to_key`, the empty-melody guard, and everything else stay.)

- [ ] **Step 5: Remove the `separate` param from the upload endpoint**

In `backend/app/main.py`, change the signature back to:

```python
def upload_and_arrange(file: UploadFile, spice: int = 3, duet: bool = False) -> dict:
```

remove the `separate` mention from its docstring, and change the analyze call to:

```python
            result = analyze(
                tmp.name,
                title=Path(file.filename or "Upload").stem,
            )
```

- [ ] **Step 6: Update the API tests**

In `backend/tests/test_api.py`: DELETE `test_upload_separates_vocal_by_default`, `test_upload_separate_false_skips_isolation`, and the `_upload_counting_separation` helper. In `test_upload_happy_path` and `test_upload_reports_identity`, change the query string from `"/api/upload?spice=2&separate=false"` back to `"/api/upload?spice=2"`.

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS. (The suite uses the conftest RMVPE stub → pyin path; demos are unaffected.)

- [ ] **Step 8: Commit**

```bash
git add backend/barbershop/analysis/pipeline.py backend/app/main.py backend/tests/conftest.py backend/tests/test_analysis.py backend/tests/test_api.py
git commit -m "feat: melody from RMVPE-on-mix; retire the separate_vocal routing"
```

---

## Task 5: Dependencies

**Files:**
- Modify: `backend/requirements.txt`
- Create: `backend/requirements-dev.txt`

- [ ] **Step 1: Runtime dep (per Task 1 decision)**

If Task 1 chose **4a (default)**: in `backend/requirements.txt`, bump `onnxruntime==1.23.2` to the version Task 1 verified (e.g. `onnxruntime==1.26.0`) and add (alphabetically) `rmvpe-onnx==0.2.3`.

If Task 1 chose **4b (opt-in)**: leave `requirements.txt` unchanged; instead add a comment line documenting the optional install:
```
# Optional: RMVPE vocal pitch (better melody on dense mixes). Install separately:
#   pip install rmvpe-onnx==0.2.3   (pulls onnxruntime>=1.26; verify ASR if you depend on it)
```

- [ ] **Step 2: Dev deps for the eval harness**

Create `backend/requirements-dev.txt`:
```
-r requirements.txt
mir_eval==0.8.2
mirdata==0.3.10
```
(Pin to whatever Task 6 installs; these power `tools/eval_melody.py`, never the runtime.)

- [ ] **Step 3: Commit**

```bash
git add backend/requirements.txt backend/requirements-dev.txt
git commit -m "build: pin rmvpe-onnx (or document opt-in) + dev-only eval deps"
```

---

## Task 6: Eval harness (dev tool)

**Files:**
- Create: `backend/tools/eval_melody.py`

- [ ] **Step 1: Write the harness**

Create `backend/tools/eval_melody.py`:

```python
"""Ground-truth melody-pitch bake-off: pyin vs RMVPE on vocadito.

Not a unit test — it downloads vocadito (Zenodo, CC-BY) and runs heavy
inference. It is the harness the project uses to justify and tune any
melody-extraction change. Run: python -m tools.eval_melody [N_TRACKS]

Requires the dev deps: pip install -r requirements-dev.txt rmvpe-onnx
"""
from __future__ import annotations

import sys
import warnings

import numpy as np

warnings.filterwarnings("ignore")


def _accompaniment(n: int, sr: int, level: float, seed: int = 0) -> np.ndarray:
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
        return np.array([[a, b] for _, a, b in s]), np.array([440 * 2 ** ((m - 69) / 12) for m, _, _ in s])

    def score(gtf, gn, est_t, est_f, est_voiced):
        rpa = mir_eval.melody.evaluate(gtf.times, gtf.frequencies, est_t, np.where(est_voiced, est_f, 0.0))["Raw Pitch Accuracy"]
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
            rt, rf, rc = out
            r1, r2 = score(t.f0, t.notes_a1, rt, rf, rc >= 0.5)
            agg["rmvpe"][0].append(r1); agg["rmvpe"][1].append(r2)
        print(f"\n{label} ({len(ids)} tracks)  {'method':6} {'RPA':>8} {'note-F1':>9}")
        for m in ("pyin", "rmvpe"):
            print(f"{'':22}{m:6} {np.mean(agg[m][0])*100:>7.1f}% {np.mean(agg[m][1]):>9.3f}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 15)
```

- [ ] **Step 2: Smoke-run it (requires dev deps + rmvpe-onnx installed)**

Run: `.venv/bin/python -m tools.eval_melody 5`
Expected: prints a CLEAN and +ACCOMP table; RMVPE's +ACCOMP RPA is far above pyin's (~90% vs ~40%).

- [ ] **Step 3: Commit**

```bash
git add backend/tools/eval_melody.py
git commit -m "test: commit the vocadito ground-truth melody eval harness"
```

---

## Task 7: Docs

**Files:**
- Modify: `DESIGN.md`, `SPEC.md`

- [ ] **Step 1: DESIGN.md — replace the melody-extraction section's lede**

Add a new section after the existing "Melody extraction" section:

```markdown
## Pitch moved to RMVPE-on-mix (2026-06-13)

`librosa.pyin` is monophonic: measured on vocadito with ground truth, its raw-pitch accuracy
collapses from 92% to 39% the moment accompaniment is added, because it tracks the loudest source.
RMVPE (a learned, mixture-native vocal pitch model) holds 97%→91%. So the melody pitch stage now
runs RMVPE on the **raw mix** — pre-separation can propagate artifacts, so Demucs left the melody
path (its `isolate_vocal` stays for future duet diarization). pyin remains the fail-soft fallback
when RMVPE is unavailable. Note segmentation (still our crude former, ~0.61 note-F1 ceiling) and
duets are unaddressed and tracked separately. The bake-off lives in `backend/tools/eval_melody.py`.
```

- [ ] **Step 2: SPEC.md — dated addendum**

Append:

```markdown
## Addendum (2026-06-13): RMVPE melody pitch

The melody pitch stage now uses RMVPE on the raw mix instead of pyin, for accompaniment-robust
extraction on dense uploads (ground-truth-measured: pyin 39% vs RMVPE 91% raw-pitch accuracy under
accompaniment). pyin is retained as a fail-soft fallback. Full design:
docs/superpowers/specs/2026-06-13-rmvpe-melody-design.md.
```

- [ ] **Step 3: Commit**

```bash
git add SPEC.md DESIGN.md
git commit -m "docs: RMVPE melody pitch (DESIGN section + SPEC addendum)"
```

---

## Task 8: Verify on the real upload

- [ ] **Step 1: Full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS (RMVPE stubbed → pyin path).

- [ ] **Step 2: Real end-to-end on the Phantom file (RMVPE live)**

Run:
```bash
.venv/bin/python - <<'PY'
from barbershop.analysis.pipeline import analyze
from barbershop.analysis.key import scale_pitch_classes
SRC="/mnt/c/Users/dhaas/Downloads/All I Ask Of You (Official Lyric Video).mp3"
r = analyze(SRC, use_cache=False)
mel = r.input.melody; mids=[n.midi for n in mel]
pcs = scale_pitch_classes(r.input.key)
ook = sum(1 for n in mel if n.midi % 12 not in pcs)
print(f"key {r.input.key.fifths}/{r.input.key.mode}  notes {len(mel)}  range {min(mids)}-{max(mids)}  out-of-key {ook}/{len(mel)}")
PY
```
Expected: runs without error; melody in vocal register; in-key fraction high (snap_to_key still applies). This is RMVPE end-to-end on the real mix.

- [ ] **Step 3: (no commit — verification only)**

---

## Self-review notes

- Spec coverage: RMVPE wrapper (T2), extraction+fallback (T3), pipeline/cache/separate_vocal removal (T4), gating risks (T1→T5), eval harness (T6), docs (T7), verify (T8) — all spec sections mapped.
- The conftest stub (T4.1) is what keeps every existing test on the pyin path with no model download.
- `extract_segments` is kept unchanged as the fallback; `snap_to_key` and the note former are untouched, so legality/quantization behavior is unchanged when RMVPE is absent.
