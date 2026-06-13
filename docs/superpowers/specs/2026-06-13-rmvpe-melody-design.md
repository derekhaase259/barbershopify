# RMVPE melody pitch: accompaniment-robust vocal pitch

**Date:** 2026-06-13 · **Status:** approved · **Owner:** Derek + Claude

## Why

The lead extracted from a dense upload is garbage because the *pitch* stage fails: `librosa.pyin`
is a generic monophonic tracker that, on a mix, locks onto whichever source is loudest. We measured
this on real singing with ground truth (vocadito + `mir_eval`, 15 tracks):

| | clean RPA | +accompaniment RPA | note-F1 (COnP), +accomp |
|---|---|---|---|
| **pyin** | 92.3% | **38.8%** | 0.276 |
| **RMVPE** | 96.8% | **91.3%** | 0.612 |

pyin loses more than half its raw-pitch accuracy the moment accompaniment is present; RMVPE
([Interspeech 2023](https://arxiv.org/abs/2306.15412)) barely degrades, because it was built to
find the vocal *in* a polyphonic mixture. This is the single highest-leverage fix for the
"musically sucks" complaint on dense uploads. It does **not** fix note segmentation (note-F1 is
capped ~0.61 by our crude segmenter regardless of pitch source) or duets — both are separate later
work.

## Decisions (made 2026-06-13)

| Decision | Choice |
|---|---|
| Pitch model | **RMVPE** via `rmvpe-onnx` (ONNX, CPU, MIT wrapper), replacing pyin's f0 stage. |
| Input | The **raw mix** — RMVPE is mixture-native; pre-separation can propagate artifacts. Demucs leaves the default melody path. |
| Fallback | **pyin-on-mix** when RMVPE is unavailable (package/model/onnx failure) — the system never hard-depends on RMVPE. |
| Scope | Pitch stage only. Keep `_frames_to_notes`/`consolidate`/`quantize` and `snap_to_key`. |
| Demucs | `isolate_vocal`/`separate.py` stay in the repo, **dormant** (future duet diarization). The `separate_vocal` melody routing added in the prior branch is removed (superseded). |
| Cache | Bump `v7` → `v8` (the melody changes). |
| Eval harness | Committed as a dev tool; `mir_eval`/`mirdata` are **dev-only** deps, not in the runtime pin. |

Out of scope: a real note-segmentation/transcription model (the ~0.61 note-F1 ceiling); duet
handling; the symbolic-melody-lookup alternative. Each is its own later effort.

## Architecture

Today: `load_audio → beats → chords → key → pyin(mix or Demucs stem) → quantize → snap_to_key`.
New:

```
load_audio → beats → chords → key → RMVPE(raw mix, 16 kHz) → confidence-gate
           → frames→notes → quantize → snap_to_key
```

Like the rest of `barbershop/analysis/`, the new code is pure and unit-testable; the web layer is
untouched.

### `barbershop/analysis/rmvpe.py` (new)

Thin, fail-soft wrapper over `rmvpe-onnx`:

```python
def rmvpe_f0(y: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """(times, frequencies_hz, confidence) at RMVPE's 16 kHz frame rate, or None
    on any failure (package missing, model download failed, onnx error)."""
```

Lazy-imports `rmvpe_onnx`; resamples `y` to 16 kHz; caches the `RMVPE()` model in a module global
(load + checkpoint download are costly). Returns `None` on any exception — mirroring the
song-lookup / separation fail-soft rule that an optional model may never break analysis.

### `barbershop/analysis/melody.py`

Add an RMVPE extraction path that gates frames on confidence and reuses the existing note former:

```python
def extract_segments_rmvpe(y, sr, *, min_confidence=0.5) -> list[tuple[float, float, float]]:
    out = rmvpe_f0(y, sr)
    if out is None:
        return extract_segments(y, sr)  # pyin fallback on the mix
    times, freq, conf = out
    voiced = conf >= min_confidence
    return consolidate_segments(_frames_to_notes(freq, voiced, times))
```

`consolidate_segments`' gap/tolerance/min-duration constants were tuned for pyin's hop; they may
need light retuning for RMVPE's 10 ms hop. The eval harness (below) is how we tune them, not guesswork.

### `barbershop/analysis/pipeline.py`

The melody line becomes:

```python
segments = melody_mod.extract_segments_rmvpe(y, sr)   # RMVPE on the mix, pyin fallback inside
melody = melody_mod.quantize(segments, grid)
```

`snap_to_key` stays. The `separate_vocal` parameter and its melody routing are removed. Cache key
suffix `v7`/`v7-sep` → `v8` (single key; no more `-sep` variant).

### `backend/tools/eval_melody.py` (new, dev tool)

Pulls vocadito (`mirdata`, Zenodo, CC-BY, 40 tracks, ~56 MB), runs pyin vs RMVPE, and reports RPA +
note-F1 (`mir_eval`) on clean audio and on audio with synthetic accompaniment. This is the
ground-truth harness the project lacked; it is how every future melody change is validated. Not a
unit test (it downloads data and runs heavy inference); a documented script.

## Risks & resolution (both merge-gating)

1. **onnxruntime pin.** `rmvpe-onnx` pulls onnxruntime ≥1.26; the project pins `onnxruntime==1.23.2`
   for `faster-whisper` (ASR). It *imports* fine under 1.26. Resolution: run a real ASR transcription
   under 1.26 during implementation; if it passes, bump the pin to a version both accept; if it
   fails, isolate RMVPE (separate process/venv) or keep RMVPE opt-in. **Must be resolved before merge.**
2. **Checkpoint license.** The `rmvpe-onnx` *code* is MIT, but the RMVPE model *weights* carry their
   own terms. Vet the bundled checkpoint against the project's free-services / open rule. If the
   weights are not redistributable for an open project, RMVPE ships **opt-in** and pyin stays the
   default. **Must be vetted before merge.**

## Error handling

- `rmvpe_f0` returns `None` on any failure → `extract_segments_rmvpe` falls back to pyin-on-mix.
- An empty melody still raises the existing `"no melody could be extracted"` (unchanged).
- `snap_to_key` and all downstream stages are unchanged, so legality guarantees hold.

## Testing

- **`rmvpe_f0` fail-soft** (`tests/test_rmvpe.py`): force the lazy import / model load to raise →
  returns `None`, never propagates.
- **Pipeline routing** (`tests/test_analysis.py`): with `rmvpe_f0` monkeypatched to a known
  `(times, freq, conf)`, the melody reflects it; with `rmvpe_f0` → `None`, the melody falls back to
  pyin (the current synthetic-audio assertions still hold).
- **No heavy inference in CI:** `conftest.py` stubs `rmvpe_f0` to `None` suite-wide (like lookup),
  so the suite runs the pyin path and never downloads the model; the routing tests monkeypatch it back.
- **Regression:** every existing demo/analysis/api test stays green.
- **Acceptance (manual, via the eval harness):** RMVPE beats pyin on +accompaniment RPA on vocadito,
  and the real Phantom upload produces a visibly more in-key, less-wandering lead.

## Docs

- `DESIGN.md`: a section on why pitch moved to RMVPE-on-mix (the ground-truth numbers + the
  mixture-native rationale) and why Demucs left the melody path.
- `SPEC.md`: a dated addendum noting the pitch-engine change.
- `requirements.txt`: `rmvpe-onnx` pinned (+ resolved onnxruntime); `mir_eval`/`mirdata` documented
  as dev-only.
