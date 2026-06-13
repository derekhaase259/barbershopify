# Duet Mode (Composed Baritone Counter-Melody) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An opt-in `duet` mode that gives the baritone an independent, composed counter-melody at held-lead and phrase-end spots, voiced around as a fixed second line, with the lead untouched.

**Architecture:** A new pure module composes one optional baritone target MIDI per slot (chord tone below the lead, contrary/stepwise motion) on apt slots. `voice_slots` pins the bari to a target when given and falls back to a free bari when a target can't be voiced legally. Wired into `arrange()` behind `ArrangerConfig.duet`; threaded through the API.

**Tech Stack:** Python 3.12, pure `barbershop/arranger/` module (no audio/HTTP), pytest. Spec: `docs/superpowers/specs/2026-06-13-duet-countermelody-design.md`.

---

## File Structure

- Create `backend/barbershop/arranger/countermelody.py` — the composer (one responsibility: pick bari targets).
- Create `backend/tests/test_countermelody.py` — composer unit tests.
- Modify `backend/barbershop/arranger/config.py` — add `duet: bool = False`.
- Modify `backend/barbershop/arranger/voicing.py` — `candidates(..., bari_target=None)` + `voice_slots(..., bari_targets=None)` with fallback.
- Modify `backend/barbershop/arranger/arrange.py` — compose + pass targets when `cfg.duet`.
- Modify `backend/app/main.py` — `duet` on `ArrangeOptions`/`ArrangeRequest`, `_arrangement_response`, and the upload endpoint.
- Modify `backend/tests/test_voicing.py`, `backend/tests/test_arrange.py`, `backend/tests/test_api.py` — constraint/fallback, integration, API wiring.
- Modify `SPEC.md`, `DESIGN.md` — addendum + rationale (same commit as the wiring).

All commands run from `backend/`. Test runner: `.venv/bin/pytest`.

---

## Task 1: Config flag

**Files:**
- Modify: `backend/barbershop/arranger/config.py`

- [ ] **Step 1: Add the flag** to `ArrangerConfig` (place right after `spice`):

```python
    duet: bool = False  # compose an independent baritone counter-melody
```

- [ ] **Step 2: Verify nothing broke**

Run: `.venv/bin/pytest -q`
Expected: PASS (231 passed) — adding an unused defaulted field changes nothing.

- [ ] **Step 3: Commit**

```bash
git add backend/barbershop/arranger/config.py
git commit -m "feat: add duet flag to ArrangerConfig"
```

---

## Task 2: The counter-melody composer

**Files:**
- Create: `backend/barbershop/arranger/countermelody.py`
- Test: `backend/tests/test_countermelody.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_countermelody.py`:

```python
"""Composer for the duet baritone counter-line."""
from barbershop.score import ChordSpan
from barbershop.texture import Slot
from barbershop.arranger.config import RANGES, ArrangerConfig
from barbershop.arranger.countermelody import compose_countermelody
from barbershop.vocabulary import chord_pcs

Q = 480


def _slot(onset, lead, root_pc, quality, *, swipe=False, phrase_end=False, structural=True):
    return Slot(
        onset=onset, duration=Q, melody_midi=lead, melody_max_midi=lead,
        melody_min_midi=lead, melody_last_midi=lead, melody_attack=not swipe,
        chord=ChordSpan(onset=onset, duration=Q, root_pc=root_pc, quality=quality),
        structural=structural, phrase_end=phrase_end, swipe=swipe,
    )


def test_targets_only_on_apt_slots():
    # a plain structural slot (no swipe, not a phrase end) gets no counter-line
    slots = [_slot(0, 64, 0, "maj")]
    assert compose_countermelody(slots, ArrangerConfig(duet=True)) == [None]


def test_target_is_a_chord_tone_below_the_lead_in_bari_range():
    slots = [_slot(0, 67, 0, "maj", swipe=True)]  # G4 lead over C major
    (t,) = compose_countermelody(slots, ArrangerConfig(duet=True))
    lo, hi = RANGES["bari"]
    assert t is not None
    assert t % 12 in chord_pcs(0, "maj")
    assert lo <= t <= hi
    assert t < 67


def test_contrary_motion_against_a_rising_lead():
    # lead climbs C5..E5 over swipe slots; the bari line should not climb with it
    leads = [60, 64, 67]
    slots = [_slot(i * Q, m, 0, "maj", swipe=True) for i, m in enumerate(leads)]
    targets = compose_countermelody(slots, ArrangerConfig(duet=True))
    assert all(t is not None for t in targets)
    assert targets[-1] <= targets[0]  # bari trends down (or holds) as the lead rises
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_countermelody.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'barbershop.arranger.countermelody'`.

- [ ] **Step 3: Write the composer**

Create `backend/barbershop/arranger/countermelody.py`:

```python
"""Compose an independent baritone counter-melody for duet mode.

Where the lead sustains (a swipe slot) or cadences (a phrase end), the
baritone leaves its harmony role and sings its own line: a chord tone
below the lead, moving in contrary motion and stepwise where it can. The
voicing engine pins the bari to these targets and solves the other voices
around them, falling back to a free bari when a target cannot be voiced.
"""
from __future__ import annotations

from barbershop.texture import Slot
from barbershop.vocabulary import chord_pcs
from barbershop.arranger.config import RANGES, ArrangerConfig


def _apt(slot: Slot) -> bool:
    """A held lead (swipe) or a cadence is where a counter-line belongs."""
    return slot.swipe or slot.phrase_end


def _cost(p: int, lead: int, prev: int | None, lead_dir: int) -> float:
    gap = lead - p
    cost = 0.0
    if gap < 3:            # sit a third to a sixth under the lead
        cost += (3 - gap) * 2.0
    elif gap > 9:
        cost += (gap - 9) * 1.0
    if prev is not None:
        bari_dir = p - prev
        # contrary motion: penalize moving the same way the lead just moved
        if (lead_dir > 0 and bari_dir > 0) or (lead_dir < 0 and bari_dir < 0):
            cost += 4.0
        # under a held lead, reward the bari for actually moving (the line)
        if lead_dir == 0 and bari_dir == 0:
            cost += 2.0
        cost += 0.3 * abs(bari_dir)  # mild smoothness
    return cost


def compose_countermelody(slots: list[Slot], cfg: ArrangerConfig) -> list[int | None]:
    """One optional baritone target MIDI per slot (None = voicer chooses)."""
    targets: list[int | None] = [None] * len(slots)
    if not cfg.duet:
        return targets
    lo, hi = RANGES["bari"]
    prev: int | None = None
    prev_lead: int | None = None
    for i, slot in enumerate(slots):
        lead = slot.melody_midi
        if not _apt(slot):
            prev_lead = lead
            continue
        pcs = chord_pcs(slot.chord.root_pc, slot.chord.quality)
        cands = [p for p in range(lo, hi + 1) if p % 12 in pcs and p < lead]
        if not cands:
            prev_lead = lead
            continue
        lead_dir = 0 if prev_lead is None else lead - prev_lead
        # tie-break toward the higher tone (closer under the lead)
        target = min(cands, key=lambda p: (_cost(p, lead, prev, lead_dir), -p))
        targets[i] = target
        prev, prev_lead = target, lead
    return targets
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/test_countermelody.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/barbershop/arranger/countermelody.py backend/tests/test_countermelody.py
git commit -m "feat: compose baritone counter-melody for duet mode"
```

---

## Task 3: Pin the bari in the voicing engine (with fallback)

**Files:**
- Modify: `backend/barbershop/arranger/voicing.py` (`candidates` ~line 76, `voice_slots` ~line 268)
- Test: `backend/tests/test_voicing.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_voicing.py`:

```python
def test_bari_target_is_honored_when_voiceable():
    # C major, lead on G4; pin the bari to E4 (the third) — a legal chord tone
    slots = [make_slot(0, 67, 0, "maj")]
    (v,) = voice_slots(slots, KEY_C, ArrangerConfig(), bari_targets=[64])
    assert v.bari == 64


def test_bari_target_falls_back_when_unvoiceable():
    # a target the voicer cannot place (well below the bari range) must not
    # crash or produce an illegal voicing — it falls back to a free bari
    slots = [make_slot(0, 67, 0, "maj")]
    (v,) = voice_slots(slots, KEY_C, ArrangerConfig(), bari_targets=[30])
    assert v.bari != 30
    assert classify(pcs_of(v, 67)) != []  # still a legal sonority
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_voicing.py -q -k bari_target`
Expected: FAIL — `voice_slots()` got an unexpected keyword argument `bari_targets`.

- [ ] **Step 3: Add the `bari_target` filter to `candidates`**

In `backend/barbershop/arranger/voicing.py`, change the `candidates` signature:

```python
def candidates(slot: Slot, cfg: ArrangerConfig, *, is_final: bool, bari_target: int | None = None) -> list[tuple[Voicing, float]]:
```

and inside its innermost `for bari in _pitches_for(...)` loop, immediately after the `if not (bass <= bari <= tenor): continue` line, add:

```python
                        if bari_target is not None and bari != bari_target:
                            continue
```

- [ ] **Step 4: Thread targets + fallback through `voice_slots`**

Replace the `voice_slots` signature and its candidate-building loop:

```python
def voice_slots(
    slots: list[Slot], key: KeySig, cfg: ArrangerConfig,
    bari_targets: list[int | None] | None = None,
) -> list[Voicing]:
    """Viterbi over per-slot voicing candidates."""
    if not slots:
        return []
    if bari_targets is None:
        bari_targets = [None] * len(slots)
    columns: list[list[tuple[Voicing, float]]] = []
    for i, slot in enumerate(slots):
        final = i == len(slots) - 1
        col = candidates(slot, cfg, is_final=final, bari_target=bari_targets[i])
        if not col and bari_targets[i] is not None:
            col = candidates(slot, cfg, is_final=final)  # counter-line is a preference, not a constraint
        if not col:
            raise ValueError(
                f"no legal voicing for slot at tick {slot.onset} "
                f"(chord root={slot.chord.root_pc} {slot.chord.quality}, melody={slot.melody_midi})"
            )
        columns.append(col)
```

(The Viterbi body below this loop is unchanged.)

- [ ] **Step 5: Run to verify pass**

Run: `.venv/bin/pytest tests/test_voicing.py -q`
Expected: PASS (all voicing tests, including the two new ones).

- [ ] **Step 6: Commit**

```bash
git add backend/barbershop/arranger/voicing.py backend/tests/test_voicing.py
git commit -m "feat: let voice_slots pin the bari to a target, with free-bari fallback"
```

---

## Task 4: Wire duet into `arrange()` + integration tests

**Files:**
- Modify: `backend/barbershop/arranger/arrange.py` (~line 117-119)
- Test: `backend/tests/test_arrange.py`

- [ ] **Step 1: Write the failing integration tests**

Append to `backend/tests/test_arrange.py` (the file already imports `arrange`, `ArrangerConfig`, `DEMOS`, `VoiceName`; add `from barbershop.arranger.validate import validate` at the top if not present):

```python
def _total_motion(notes):
    return sum(abs(b.midi - a.midi) for a, b in zip(notes, notes[1:]))


def test_duet_keeps_the_lead_identical():
    for demo in DEMOS.values():
        plain = arrange(demo, ArrangerConfig(spice=4))
        duet = arrange(demo, ArrangerConfig(spice=4, duet=True))
        assert duet.voices[VoiceName.lead] == plain.voices[VoiceName.lead]


def test_duet_makes_the_baritone_more_active():
    plain = sum(_total_motion(arrange(d, ArrangerConfig(spice=4)).voices[VoiceName.bari])
                for d in DEMOS.values())
    duet = sum(_total_motion(arrange(d, ArrangerConfig(spice=4, duet=True)).voices[VoiceName.bari])
               for d in DEMOS.values())
    assert duet > plain


def test_duet_charts_still_validate_clean():
    for demo in DEMOS.values():
        score = arrange(demo, ArrangerConfig(spice=4, duet=True))
        assert validate(score) == []
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_arrange.py -q -k duet`
Expected: FAIL — `test_duet_makes_the_baritone_more_active` fails (duet == plain; nothing wired yet).

- [ ] **Step 3: Wire the composer into `arrange()`**

In `backend/barbershop/arranger/arrange.py`, replace:

```python
    chosen = harmonize(slots, key, cfg)
    slots = [replace(slot, chord=chord) for slot, chord in zip(slots, chosen)]
    voicings = voice_slots(slots, key, cfg)
```

with:

```python
    chosen = harmonize(slots, key, cfg)
    slots = [replace(slot, chord=chord) for slot, chord in zip(slots, chosen)]
    bari_targets = None
    if cfg.duet:
        from barbershop.arranger.countermelody import compose_countermelody

        bari_targets = compose_countermelody(slots, cfg)
    voicings = voice_slots(slots, key, cfg, bari_targets)
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/test_arrange.py -q`
Expected: PASS (all arrange tests, including the three new duet tests).

- [ ] **Step 5: Run the full suite (no regressions)**

Run: `.venv/bin/pytest -q`
Expected: PASS. If `test_duet_makes_the_baritone_more_active` fails (counter-line too timid), tune `_cost` in `countermelody.py` — raise the held-lead motion penalty (the `+= 2.0`) — then re-run. Do not weaken the legality or lead-identity tests.

- [ ] **Step 6: Commit**

```bash
git add backend/barbershop/arranger/arrange.py backend/tests/test_arrange.py
git commit -m "feat: wire duet counter-melody into the arranger"
```

---

## Task 5: Expose `duet` through the API

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_api.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_api.py`:

```python
def test_arrange_endpoint_accepts_duet_flag():
    demo = client.get("/api/demos").json()[0]["id"]
    r = client.post(f"/api/demos/{demo}/arrange", json={"spice": 4, "duet": True})
    assert r.status_code == 200, r.text
    assert r.json()["score"]["voices"]["bari"]


def test_upload_threads_duet_to_arranger(monkeypatch, tmp_path):
    from barbershop.analysis import asr, separate
    import app.main as main

    monkeypatch.setattr(asr, "transcribe", lambda path: None)
    monkeypatch.setattr("barbershop.analysis.pipeline.CACHE_DIR", tmp_path)
    monkeypatch.setattr(separate, "isolate_vocal", lambda p, s: None)
    seen = {}
    real = main.arrange  # _arrangement_response calls the module global `arrange`

    def spy(inp, cfg):
        seen["duet"] = cfg.duet
        return real(inp, cfg)

    monkeypatch.setattr(main, "arrange", spy)
    with open(_make_test_wav(tmp_path), "rb") as f:
        r = client.post("/api/upload?spice=2&separate=false&duet=true",
                        files={"file": ("tiny.wav", f, "audio/wav")})
    assert r.status_code == 200, r.text
    assert seen["duet"] is True
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_api.py -q -k duet`
Expected: FAIL — `duet` is ignored (unknown field / `cfg.duet` is False).

- [ ] **Step 3: Add `duet` to the request models, the choke point, and the endpoints**

In `backend/app/main.py`:

Add `duet` to both option models:

```python
class ArrangeOptions(BaseModel):
    spice: int = Field(default=3, ge=1, le=5)
    duet: bool = False


class ArrangeRequest(BaseModel):
    input: ArrangeInput
    spice: int = Field(default=3, ge=1, le=5)
    duet: bool = False
```

Thread it through `_arrangement_response`:

```python
def _arrangement_response(inp: ArrangeInput, spice: int, duet: bool = False) -> dict:
    score = arrange(inp, ArrangerConfig(spice=spice, duet=duet))
```

Update the three call sites:

```python
    return _arrangement_response(DEMOS[demo_id], options.spice, options.duet)
```
```python
    return _arrangement_response(req.input, req.spice, req.duet)
```

And the test-songs arrange endpoint similarly (`_arrangement_response(..., options.spice, options.duet)`).

In `upload_and_arrange`, add the param and pass it:

```python
def upload_and_arrange(file: UploadFile, spice: int = 3, separate: bool = True, duet: bool = False) -> dict:
```
```python
    response = _arrangement_response(result.input, spice, duet)
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/test_api.py -q`
Expected: PASS (all API tests, including the two new ones).

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py backend/tests/test_api.py
git commit -m "feat: expose duet flag on the arrange and upload endpoints"
```

---

## Task 6: Docs

**Files:**
- Modify: `SPEC.md`, `DESIGN.md`

- [ ] **Step 1: SPEC.md addendum**

Find the arranger/embellishments section and add a short paragraph:

```markdown
**Duet mode (opt-in).** With `duet` enabled, the baritone may leave its harmony role on
held-lead (swipe) and phrase-end slots to sing a composed counter-melody — a chord tone
below the lead, moving in contrary motion. The lead invariant is unchanged: it still carries
the melody verbatim. Off by default.
```

- [ ] **Step 2: DESIGN.md section**

Add after the melody-extraction section:

```markdown
## Duet mode composes the baritone counter-line; it does not extract it

A duet upload tempted a "two singers → lead + bari" split. Recovering the *source's* second
voice was spiked and rejected: Demucs gives one combined vocal stem, the singers mostly
alternate or sing in unison/octaves, and their close-third harmony overlaps too much for pitch
tracking (a two-pass-pyin probe found 0% coherent second line). So duet mode *composes* the
baritone line from the chord changes instead — a chord tone below the lead, in contrary motion,
on held-lead and phrase-end slots — and the voicing engine solves tenor+bass around it, falling
back to a free bari when a target can't be voiced. Counter-line density rides the swipe machinery,
so it scales with spice for free.
```

- [ ] **Step 3: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add SPEC.md DESIGN.md
git commit -m "docs: document duet mode (SPEC addendum + DESIGN rationale)"
```

---

## Task 7: Verify in the running app

- [ ] **Step 1: Arrange a demo as a duet and eyeball the bari**

Run:
```bash
.venv/bin/python -c "
from barbershop.demos import DEMOS
from barbershop.arranger.arrange import arrange
from barbershop.arranger.config import ArrangerConfig
from barbershop.score import VoiceName
d = next(iter(DEMOS.values()))
for duet in (False, True):
    s = arrange(d, ArrangerConfig(spice=4, duet=duet))
    b = s.voices[VoiceName.bari]
    moves = sum(abs(y.midi-x.midi) for x,y in zip(b, b[1:]))
    print(('duet' if duet else 'plain'), 'bari notes', len(b), 'total motion', moves)
"
```
Expected: the duet line has more notes / more total motion than plain.

- [ ] **Step 2: (optional) drive it through the live app** at `http://localhost:5280/` — arrange a demo with `duet=true` via `/api/arrange` and confirm a 200 with a populated `bari` voice and zero `violations`.
