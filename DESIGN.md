# DESIGN.md — decisions and why

Running log of non-obvious engineering decisions. The requirements live in `SPEC.md`; this file
explains the choices the spec left open.

## Score model: JSON source of truth, MusicXML as render contract

The frontend holds the working score as JSON (mirroring pydantic models in
`backend/barbershop/score.py`); the backend is a stateless transformer. Every edit mutates the
JSON and round-trips `POST /api/render` → fresh MusicXML → OSMD re-render. Rationale: MusicXML is
excellent as a *rendering/interchange* contract but miserable as a *mutable editor model* (ties,
divisions, voice interleaving). Keeping one MusicXML serializer — in Python, where the music logic
and its tests live — avoids maintaining duplicate serializers in two languages. The round-trip
costs ~tens of ms locally, dominated by OSMD's own layout pass, which we'd pay anyway.

- **Time**: integer ticks, 480/quarter. Integer arithmetic kills float-drift bugs, divides cleanly
  for triplets/dotted values, and maps 1:1 onto MusicXML `<divisions>` and MIDI PPQ.
- **Chord annotations travel with the score** (root pitch class + quality per harmonic slot).
  Both the legality validator and just-intonation playback need to know "what chord is this
  vertical" — recovering it by re-analysis would be fragile where it must be exact.

## OSMD over VexFlow

OSMD consumes MusicXML directly and owns engraving layout (systems, beams, lyrics, extenders);
VexFlow is a glyph-drawing library that would require building a layout engine — a project in
itself. Editing doesn't need glyph-level mutation: we map a click to a score-JSON note via
deterministic ordering (part, voice, measure, index — identical traversal on both sides), edit the
model, re-render. OSMD also ships a playback cursor API we use for the moving highlight.

## Playback synthesizes from score JSON, not from MusicXML or OSMD playback

Just intonation requires commanding exact frequencies per note per chord context; OSMD's playback
and general MIDI paths quantize to 12-TET. Tone.js lets us schedule each voice's events with
explicit Hz. The engine is frequency-first from day one (M2) so the JI toggle (M7) only swaps the
pitch→Hz function, not the scheduler.

## Just-intonation drift strategy (root-anchored, lead-pinned)

Each chord is tuned as pure ratios (4:5:6:7 family) relative to its **root taken at equal
temperament**; the lead's line is always rendered at ET. Comma drift is the classic failure of
"tune each chord relative to the previous one" — anchoring every chord's root to the fixed ET grid
makes drift structurally impossible, at the cost of small horizontal steps in harmony voices
between chords (real quartets do exactly this: melody holds the pitch center, harmony voices
adjust vertically). Documented here per spec; numeric tests assert 4:5:6:7 within a cent.

## Arranger: two-stage Viterbi (chords, then voicings)

Joint optimization over (chord × voicing) per slot explodes combinatorially; factoring into chord
selection (melody-note containment hard, tier bias / function preservation / circle-of-fifths
rewards soft) followed by voicing selection (range & crossing & parallels hard, ring / cone /
smoothness soft) keeps each DP small, debuggable, and independently testable. The voicing stage
sees the chosen chord sequence, which is what determines voice-leading anyway. Weights live in one
config object; the spice dial selects scaled presets rather than exposing raw weights.

## Hand-written MusicXML serializer (no music21)

We emit a narrow, fixed MusicXML subset (two parts, two voices each, fixed clefs, lyrics on one
voice). A direct ElementTree serializer is ~hundreds of lines, fully controlled (stem direction,
8vb clef, extenders — things music21 can fight you on), imports in milliseconds, and adds zero
dependencies. music21 stays out of the runtime; MIDI export uses `mido` (a tiny pure-Python dep).

## Two interpretations in the 7th-resolution rule (theory nerds: argue here)

The spec demands "chordal 7ths resolve down by step," enforced as a hard constraint and a
validator check. Two principled exceptions, both forced by other hard rules:

1. **Transferred resolution.** V7→I with the melody landing on the I's 3rd: barbershop dom7s are
   complete (no omissions) and 3rds are never doubled, so the inner-voice 7th *cannot* fall by
   step — the lead owns the resolution tone. Standard practice (and ours): the resolution
   transfers to the lead; the 7th-voice moves to another chord tone, preferring downward motion.
   Allowed only when the lead actually sounds the resolution pitch class.
2. **dim7 has no functional 7th.** A diminished 7th chord is fully symmetric; which tone is "the
   7th" is an artifact of root spelling (we pick one of four equivalent roots). Enforcing
   down-by-step on that label created provably unvoiceable progressions (verified by exhaustive
   search over the voicing lattice). dim7 tones follow the general smoothness costs — step or
   hold — which is how passing/neighbor diminished chords actually behave. Half-diminished and
   minor 7ths keep the strict rule; their 7ths are real.

## Demo tunes: certainty over period flavor

The bundled no-audio demos are "Yankee Doodle" (trad.) and "Good Morning to All" (Mildred J.
Hill, 1893) rather than the spec's example suggestions ("My Wild Irish Rose," "Shine On, Harvest
Moon") — chosen because I can transcribe these two note-perfectly from memory, and a demo whose
melody is *wrong* fails the "sounds like barbershop" bar worse than one that's merely older.
Both are public domain; GMtA is squarely in the right era. A true barbershop-era tune joins in
Milestone 3 once verified against actual sheet music. The deliberately coarse demo chord inputs
(one or two per measure) are a feature: the engine's substitutions and dominant chains are the
demo.

## Octave folding is artifact correction, not melody bending

pyin's classic failure mode is the octave jump. Two defenses: extraction folds notes sitting more
than a fifth from their local median back toward it in octaves, and `arrange()` folds any note
that no global transposition could bring inside the Lead's range. Pitch classes — and therefore
all harmony decisions — are untouched, so this does not violate "melody is sacrosanct"; it
corrects transcription artifacts the way a human transcriber silently would.

## Residual violations on noisy real-world audio are reported, not hidden

The arranger guarantees a complete chart even when the (noisy, chromatic) extracted melody plus
the chord chain make some legality constraint locally unsatisfiable — hard constraints become
10k-cost edges, so Viterbi picks the least-bad chart rather than crashing. On clean inputs (all
demo tunes, all spice levels, and two of the four bundled 78s) the validator reports zero
violations; on the noisiest two 78s a handful (≤6) survive at feasibility crunches, and the UI
shows the count. A joint chord+voicing feasibility pass is planned with the M7 voice-leading
refinements. The audio pipeline assumes 4/4 in v1 — wrong for waltz-time songs, which simply get
re-barred, not mis-harmonized; 3/4 detection is a known gap.

## Affect → music mapping (the part to argue about)

Composition mode scores text with a deterministic valence/arousal lexicon (negation-aware,
offline, testable). The mapping:

- **Valence → mode and color.** Below −0.15 the chart goes minor (A-minor frame; the arranger
  picks the final singable key), leaning on barbershop's minor palette — ii⌀7→V motion is baked
  into the closing template. Otherwise major. The *ending stanza's* valence decides the last
  chord of a minor chart: brightening texts earn a picardy major; unrelieved ones end minor (the
  validator accepts a minor final only in minor keys).
- **Arousal → energy.** Tempo = 92 + arousal×36 BPM, clamped to 60–132 (a sad poem lands in the
  ~63–79 band, an exuberant one ~105–128). Melodic span = 12 + (arousal+1)×2.5 semitones. Above
  0.25 arousal, closing phrases get cadential acceleration: the dominant bar splits into
  predominant→dominant halves.
- **Rhyme → form.** Lines map 1:1 to 4-bar phrases. The second occurrence of a rhyme letter
  closes its couplet (authentic cadence); first occurrences stay open (half cadence). Rhyming
  lines answer with the same cadence scale degree. An iambic opening shifts the phrase onto a
  weak-beat start so stressed syllables land on beats.
- **Melody.** Eight seeded candidates per phrase, scored for stress–meter concordance, peak near
  the golden section, leap economy, and chord-tone placement on strong beats; re-compose bumps
  the seed.

Three counterpoint rulings tightened while testing composed charts, now applied engine-wide:
parallel fifths/octaves require *same-direction* motion (contrary "anti-parallels" are legal, as
in the classic ragtime turnaround); a predominant 7th (min7/ii⌀7) may *hold* its 7th into a chord
containing it (common-tone resolution, e.g. ii⌀7→i); and parallels are not counted across a
phrase rest (no linear connection). The harmonizer also avoids the "fifth-to-fifth trap" —
harmonizing a rising melody as the 5th of two consecutive complete 7th chords, which would force
bass/lead parallels no voicing can escape.

## Embellishments reuse the legality machinery (and what's not built yet)

Swipes and tags are not bolted onto a finished score; they are *harmonic-rhythm subdivisions*
created before harmonization. A sustained slot splits in two (the swipe lands on the last beat),
the new sub-slot carries a "prefer to move" nudge, and the ordinary harmonize→voice pipeline does
the rest — so every swipe is vocabulary-legal and voice-led by construction. The tag is the same
idea writ large: at spice ≥3 the lead's final note extends two measures (the only sanctioned
rhythm change to the melody — pitch untouched) and the trio walks in half-measure swipe steps,
settling on the enforced root-position final triad. The walk's chords aren't scripted; they
emerge from circle-of-fifths rewards under the melody-containment constraint, which is why a
root-post yields II7/♭VI7 colors while a fifth-post walks differently.

Building the tag surfaced the general fix promised earlier: the harmonizer now rejects (at
near-hard cost) any chord transition whose 7th has no continuation — no step-down target in the
next chord, no common-tone hold, no lead transfer. That joint chord/voicing feasibility check
cleaned 3 of the 4 noisy test 78s completely (the fourth keeps a single honestly-reported
violation).

**Not yet implemented** (documented rather than half-done): key changes (needs mid-score key
signatures in the model and serializer), bell chords, and echo embellishments (both spice ≥4).
The ring metric also treats only major finals as "ringing," so minor-key composed charts report
`final_chord_ring: no` — cosmetic, but worth knowing.

## Melody extraction: pyin on the mix, with Demucs vocal isolation for dense material

`librosa.pyin` is pure-Python/numpy, deterministic, and well-suited to the bundled test material
(mono, melody-dominant acoustic-era recordings) — so it stays the default on the raw mix.

But pyin is *monophonic*: on a dense modern production (orchestra + multiple vocalists) it locks
onto whichever source is loudest frame to frame, jumping between voice, bass and strings, and the
extracted "melody" is garbage — wide-ranging, octave-churning, unsingable. The fix is to isolate
the vocal stem first (`analysis/separate.py`, Demucs `htdemucs` on CPU) and track pitch on that.
Melody only: harmony, beats and key still come from the full recording. The isolated stem also
gets a tighter pitch ceiling (≤ ~E5) and a voicing-confidence gate (`min_voiced_prob`), because
separation bleed and reverb tails survive as low-confidence frames; dropping them is most of what
turns the raw f0 into a line. Measured on "All I Ask of You" (a full studio duet): octave-sized
jumps fell from 37 to 7 and the median pitch rose from G♯3 into vocal register. On the sparse 78s
separation is neutral-to-helpful, so it's safe to leave on.

Defaults: **on for uploads** (`POST /api/upload?separate=true`), since uploads are typically dense
mixes; `?separate=false` skips it. Demucs is a heavy Torch dependency, so `separate.py` lazy-imports
everything and is **fail-soft** — any failure (package missing, download failed, decode error)
returns `None` and the pipeline falls back to extracting from the mix, mirroring the song-lookup
rule that an optional booster may never break analysis.

Two honest limits. (1) It's a *duet*: when two voices overlap, "the lead" is genuinely ambiguous,
and the tracker will wander between them — separation can't resolve who the melody *is*. (2)
`basic-pitch` (a polyphonic transcriber, TensorFlow) was considered for the same problem but doesn't
isolate the voice, so it still needs melody-line selection out of a polyphonic transcript; separation
is the more direct lever and the one we built.

## Pitch moved to RMVPE-on-mix; Demucs left the melody path (2026-06-13)

The section above is superseded for the *pitch* stage. `librosa.pyin` is monophonic, and measured on
vocadito with ground truth (`backend/tools/eval_melody.py`), its raw-pitch accuracy collapses from
~92% to ~39% the moment accompaniment is added — it tracks the loudest source, not the voice. RMVPE
(a learned, mixture-native vocal-pitch model) holds ~97%→~91%. So melody pitch now runs **RMVPE on
the raw mix**: it is built to find the vocal *in* a mixture, and pre-separation can propagate
artifacts, so Demucs left the melody path (its `isolate_vocal` stays, dormant, for future duet
diarization). `pyin` remains the fail-soft fallback when RMVPE is unavailable. The checkpoint is
`rmvpe.onnx` from the RVC repo `lj1995/VoiceConversionWebUI` (HF), declared **MIT** — redistributable
for an open project (the upstream RMVPE paper's own terms aren't separately restated; the
redistribution point is MIT). Still unaddressed and tracked separately: **note segmentation** (our
crude pitch-jump former caps note-F1 ~0.61 regardless of pitch source) and **duets**.

## Duet mode composes the baritone counter-line; it does not extract it

A duet upload ("All I Ask of You") tempted a "two singers → lead + bari" split. Recovering the
*source's* second voice was spiked and rejected: Demucs gives one combined vocal stem, the singers
mostly alternate or sing in unison/octaves, and their close-third harmony overlaps too much for pitch
tracking — a two-pass-pyin probe on the most-harmonized window found 0% coherent second line, and
`basic-pitch` won't install against our Python 3.12 / numpy-2 stack. So duet mode *composes* the
baritone line from the chord changes instead (`arranger/countermelody.py`): a chord tone below the
lead, in contrary motion, on held-lead and phrase-end slots. The voicing engine then solves
tenor+bass around it. The pin is a strong **soft cost** (`w_bari_target`), not a hard filter — a hard
filter once left a dom9's 7th unresolved in Yankee Doodle, because it stripped the Viterbi's freedom
to resolve it. As a cost, the bari follows its counter-line but yields when a hard rule (7th
resolution, parallels) demands, so duet charts still validate clean. Counter-line density rides the
swipe machinery, so it scales with spice for free, and the lead stays byte-for-byte identical.

## Song lookup (2026-06-11)

**Why lookup at all, and why only for identity and lyrics.** Crowdsourced databases
already know what a song *is* (AcoustID fingerprints) and what its words are (LRCLIB),
and both beat reconstructing those from a noisy waveform — our weakest analysis link was
Whisper guessing words through 78-rpm surface noise. But no free database serves melodies
or chords; even Shazam only matches fingerprints to names. So analysis remains the
backbone for everything musical, and lookup augments it. (MIDI-archive hunting was
considered and rejected: spotty coverage, wild quality variance, and melody-channel
detection is its own analysis problem.)

**The fail-soft rule.** Lookup may never break analysis. It's enforced twice: both
`lookup/identify.py` and `lookup/lyrics.py` catch everything internally and return
`None`, and `pipeline.analyze()` wraps the calls in a second `try/except`. Offline, the
app behaves exactly as before the feature existed. HTTP timeouts are (3.05 s, 10 s) so a
dead network costs seconds, not minutes.

**ASR is skipped on a hit.** Real lyrics make transcription pointless, so a LRCLIB hit
saves the 15–60 s Whisper pass and the first-run 150 MB model download entirely.

**Synced lyrics use time anchoring.** `textset.set_timed_lines` assigns each LRC line to
the melody phrase containing its onset (with a half-beat grace window, since stamps run
early), then reuses the same per-phrase DP as pasted lyrics. Instrumental intros and
interludes correctly get no words — something the paste path's positional 1:1 mapping
can't do. The pipeline converts seconds → ticks; textset stays ignorant of beat grids.

**Title precedence:** identified title > explicit `title=` parameter > filename stem.
The analysis cache (now `-v4`) stores the identity, and a cached identified title is not
clobbered by the filename-derived parameter on cache hits.

**The committed AcoustID key.** `ACOUSTID_APP_KEY` is an *application* identifier, not a
secret — AcoustID's intended model is that the app developer registers once and ships the
key (MusicBrainz Picard and beets do the same in public repos). This is what lets a fresh
clone work with zero key setup. Never commit anything from the account side of
acoustid.org (user keys, login credentials).

**Tests stay offline.** `backend/tests/conftest.py` stubs both lookup functions to "no
match" suite-wide, so `pytest` is deterministic and network-free even on machines with
fpcalc installed; lookup-behavior tests monkeypatch the real values back in.

### Tab chords (same date)

**Align-and-relabel, not trust-the-tab.** Tabs are untimed and often transposed, so the
audio keeps every span boundary (which also feeds meter/downbeat detection) and the tab
only corrects identities — the exact error class chroma gets wrong (false minors, wrong
roots, missed sevenths). A global alignment is run at all 12 transpositions (subsuming
capo and off-pitch transfers; the tab's own key/capo metadata is ignored as unreliable),
and the winner must agree with ≥50% of analyzed roots or the tab is rejected wholesale.
There is no path where a tab degrades the chords without first agreeing with half of them.
Key detection runs on the corrected spans, so a fixed false-minor can also fix the key.
The tab sequence is aligned *uncompressed*: a tab restating C across lines is what lets a
false-minor span pair with the real chord instead of falling into a gap (the 4-distinct-
chord minimum still rejects two-chord ditties).

**Why Chordie.** The 2026-06-11 probe found Ultimate Guitar, e-chords, UkuTabs, and
Cifra Club all behind Cloudflare for plain requests; Chordie serves 200s and embeds each
song's ChordPro source verbatim. Its search relevance is poor (covers outrank originals,
and adding the artist to the query returns that artist's *other* songs — hence two merged
searches), so candidates are verified against the AcoustID identity before parsing: the
title must match by a majority of its tokens (any-overlap let "Jude the Obscene"
impersonate "Hey Jude"), while the artist only *ranks* — same-artist sheets first, then
same-title covers, because a cover keeps the harmony and the alignment gate against the
actual audio is the real arbiter (strict artist matching cost us every Hallelujah:
Chordie has it only as covers). maj7 maps to maj6 — barbershop never voices a major
seventh.

**Order matters.** Identification now runs right after beat tracking: tab correction must
precede key detection, and lyrics need the identity later anyway. Tempo, meter, and
downbeat phase remain purely audio-derived — a tab has no timing to contribute.

### Progression adherence (same date)

"Ensure the arrangement matches the chord progression" cannot mean per-chord equality:
the spice dial *exists* to substitute (secondary dominants, passing diminisheds, swipes),
and the melody is sacrosanct — when a structural melody note isn't in the input chord,
realizing that chord is impossible by construction. So adherence is measured, not
mandated: every structural vertical's four sung pitches are classified through the
vocabulary (never by trusting `score.chords` labels) and count as adhering if any reading
is the input chord, a same-root recoloring, a dominant-family chord rooted a fifth above
the *next* input chord, a passing dim7, or a suspension/anticipation within a beat of a
chord change. Filigree verticals and forced-substitution verticals leave the denominator;
material beyond the input (the tag) is excluded. The number is a metric; only below a 50%
floor does it become a violation — that means the chart wandered off the song, which is a
bug, not artistry. Calibration on the demos: spice 1–2 = 1.00 exactly (below the
substitution spice, the chart IS the song), spice 5 = 0.67–0.77.
