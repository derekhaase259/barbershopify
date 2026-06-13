# Duet mode: composed baritone counter-melody

**Date:** 2026-06-13 · **Status:** approved · **Owner:** Derek + Claude

## Why

Uploading a duet ("All I Ask of You") exposed a gap: the chart flattens two singers into one
lead. The wish was to render it as a true duet — lead and baritone each carrying a line.

The literal version (recover the *source's* second voice and give it to the bari) was spiked and
rejected: Demucs yields one combined vocal stem, not two singers, and on this material there is
little to recover — the voices alternate (solo verses), unite in unison/octaves (the lead already
captures that), and harmonize in close thirds whose overtones overlap too much for pitch tracking.
A two-pass-pyin probe on the most-harmonized window found **0%** coherent second line. The clean
polyphonic tool (`basic-pitch`) also won't install against our Python 3.12 / numpy-2 stack.

So we **compose** the second line instead of extracting it. The baritone gets an independent
counter-melody, generated from the chord changes, at the spots where a counter-line is idiomatic.
This is the "echo embellishment" long noted as unbuilt in DESIGN.md, scoped to its natural home.

## Decisions (made 2026-06-13)

| Decision | Choice |
|---|---|
| Source of the 2nd line | **Composed** by the arranger (rule-based), not extracted from audio. |
| Texture | Counter-melody baritone: lead always owns the main tune; bari diverges into its own line only where apt; normal TTBB otherwise. |
| Where it applies | Sustained-lead spots (the swipe slots) and phrase-end / cadence slots. A held or slow lead is what a bari sings against; not every note. Because swipe slots are themselves spice-gated (none at spice 1), the counter-line is sparse at low spice (phrase ends only) and richer as spice adds swipes — a deliberate, free consequence, not a separate dial. |
| Control | `ArrangerConfig.duet: bool = False`. Opt-in style toggle, **not** spice-linked, **not** audio-detected. Surfaced as a `duet` param on `/api/arrange` and `/api/upload`. |
| Legality | Bari targets are always chord tones; the voicing engine still enforces every hard/soft rule and **falls back to a free bari** on any slot it can't legally voice with the target pinned. Never crashes. |
| Lead | Untouched. The lead melody is byte-for-byte identical whether `duet` is on or off. |

Out of scope: source second-voice extraction (infeasible, above); duet auto-detection (the same
unreliable probe); the frontend toggle (a small follow-up once the backend lands).

## Architecture

Today: `transpose → segment → _add_swipes → harmonize → voice_slots → assemble`.

New pure stage between harmonize and voicing:

```
… → harmonize → compose_countermelody (NEW) → voice_slots(…, bari_targets) → assemble
```

Like the rest of `barbershop/arranger/`, the new module imports nothing from the web layer and is
unit-tested without audio or HTTP.

### `barbershop/arranger/countermelody.py` (new)

`compose_countermelody(slots, key, cfg) -> list[int | None]` — one optional baritone target MIDI
per slot, aligned to the `slots` list. Returns a pitch only on duet-apt slots; `None` elsewhere
(meaning: voicer chooses the bari freely, exactly as today).

Rules (a greedy melodic walk with light lookahead, the same flavor as the bass's motion logic):

1. **Apt slots only.** A slot is apt when it is a swipe slot (harmony moving under a held lead) or
   a phrase-end/cadence slot. Everywhere else → `None`.
2. **Always a chord tone**, in baritone range, sounding **below the lead** — so the target is
   harmonically legal by construction and keeps the bari under the melody.
3. **Contrary motion** to the lead (lead rises → bari falls), preferring **stepwise** motion from
   the previous bari target, so the result is a singable line rather than disconnected chord tones.
4. **Echo/motion under a hold:** when the lead sustains, the bari moves (that is the whole point of
   the counter-line); when the lead itself is moving melodically, the bari leans toward oblique/held
   tones so the two lines stay distinct rather than colliding rhythmically.

The walk is deterministic. No randomness (consistent with the engine's rule-based contract).

### Voicing change — `barbershop/arranger/voicing.py`

`voice_slots(slots, key, cfg, bari_targets=None)`. When `bari_targets[i]` is set, `candidates()`
for that slot emits only legal voicings whose `bari == target` (tenor still ≥ lead, bass still on
root/fifth ≤ lead, ranges and chord coverage still hard). The Viterbi then optimizes tenor + bass
around the fixed lead + bari.

**Fallback:** if the constrained candidate set is empty (the target chord tone can't be placed with
a legal tenor/bass — collision or ordering), that slot reverts to the unconstrained candidate set.
A counter-line is a preference, never a hard constraint; coverage and voice-leading win.

### `arrange.py` wiring

When `cfg.duet`, call `compose_countermelody` after harmonize and pass the targets into
`voice_slots`. The existing assembly loop already re-attacks a voice when its pitch changes across
slots, so a moving bari target produces the counter-melody's rhythm with no assembly change.

## Error handling & robustness

- Empty constrained candidates → free-bari fallback for that slot (above). No new crash path.
- `duet=False` is a strict no-op: `bari_targets=None`, identical output to today (guarded by a test
  over all demos).
- The composer only ever returns chord tones in range, so the validator's legality checks
  (ranges, doubled-third, parallels, 7th resolution) cannot be newly tripped by a target.

## Testing (the quality bar)

Unit (`tests/test_countermelody.py`):
- targets are chord tones, in bari range, below the lead, only on apt slots;
- contrary/stepwise motion is actually produced against a rising/falling lead;
- a target that cannot be voiced legally yields `None`/fallback rather than an illegal voicing.

Integration (`tests/test_arrange.py`, `tests/test_voicing.py`):
- `duet=True` charts still `validate()` clean on every demo;
- the bari has strictly more distinct pitches / more motion with `duet=True` than `False`;
- the **lead melody is byte-for-byte identical** with the toggle on vs off;
- `duet=False` reproduces today's bari exactly (no-op guard).

API (`tests/test_api.py`): the `duet` param threads through `/api/arrange` and `/api/upload`.

## Docs

- `SPEC.md`: short addendum — duet/counter-melody as an opt-in embellishment; the lead invariant
  is unchanged. (Same commit as the change, per CLAUDE.md.)
- `DESIGN.md`: a section on why the line is composed not extracted (the feasibility result) and the
  contrary-motion rule rationale.
