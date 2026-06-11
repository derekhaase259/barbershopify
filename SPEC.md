# Project: barbershopify

You are starting a brand-new project in an empty working directory. **Before anything else, save this entire brief, verbatim, as `SPEC.md` in the project root and commit it** — it is the canonical specification for this project. Re-read the relevant sections of `SPEC.md` at the start of each milestone and whenever resuming work in a fresh session, and treat it as the authority when memory and spec disagree. If we agree to change the spec mid-project, update `SPEC.md` in the same commit as the change so it never drifts from reality. Also reference it from `CLAUDE.md` (create one) so future sessions in this repo know to consult it.

Read this entire brief before writing any code, then propose a short plan (architecture + milestone order) before implementing. I care as much about your engineering taste and judgment as the feature list — make deliberate decisions and explain the non-obvious ones briefly in a DESIGN.md as you go.

## What this is

A local web application that takes an uploaded audio file of a song (.mp3, .m4a, or .wav), analyzes its melody, harmony, chord progression, and lyrics, and automatically generates a TTBB barbershop quartet arrangement. The output is rendered as interactive sheet music — think a lightweight MuseScore: full notation rendering with lyrics, synchronized four-part playback, and the ability to edit notes after the automatic arrangement is generated. Beyond audio uploads, the app supports substituting user-pasted lyrics under an analyzed melody, and a lyrics-only composition mode that writes an original arrangement from a poem's prosody and emotional content (all detailed below).

This is a single-user local tool. No auth, no deployment concerns, no multi-tenancy. Optimize for correctness of the music, quality of the arrangement engine, and a genuinely usable editor.

## High-level pipeline

1. **Upload & decode** — Accept .mp3, .m4a, .wav. Decode to mono PCM for analysis (ffmpeg is available / installable).
2. **Audio analysis (Python backend)**
   - **Tempo & beat tracking** — establish a beat grid and downbeats so the arrangement can be quantized to measures.
   - **Key detection** — global key, with awareness that you may want to transpose into a singable key for TTBB (see ranges below).
   - **Melody extraction** — extract the dominant melodic line as a sequence of (pitch, onset, duration) events. Recommended: Spotify's `basic-pitch` for polyphonic-tolerant transcription, or `librosa.pyin` if the source is melody-dominant. If the mix is dense, consider optional source separation (e.g., `demucs` vocals stem) as a quality booster — but make it optional/configurable since it's heavy. Quantize the melody to the beat grid (eighth-note resolution by default, configurable).
   - **Chord recognition** — per-beat or per-half-measure chroma-based chord estimation (template matching over major, minor, dominant 7th, diminished, etc.), smoothed with something like an HMM/Viterbi pass so the progression is musically coherent rather than jittery.
   - **Lyric transcription** — automatic by default: run ASR with word-level timestamps (e.g., `faster-whisper`) on the audio (or the separated vocal stem when available) and carry the timed words forward for alignment to the melody (see the Lyrics section below).
3. **Arrangement engine (the heart of the project — see detailed rules below)** — take the quantized melody + chord progression and produce four voice parts: Tenor, Lead, Baritone, Bass.
4. **Notation output** — serialize the arrangement to **MusicXML** as the canonical internal format (use `music21` or write it directly). MusicXML is the contract between backend and frontend.
5. **Frontend: render, play, edit** — render the MusicXML as engraved sheet music in the browser, with playback and note editing.

## Barbershop arrangement rules (the core spec — the audience is barbershop singers and theory nerds, and they will audit this)

The arrangement engine should be a rule-based/constraint-based system (not ML). Architect it as a weighted-cost optimizer over candidate harmonizations and voicings: hard constraints (legality) are inviolable, soft constraints (quality) are scored. Suggested cost terms, roughly in priority order: melody fidelity → chord-vocabulary legality → voicing "ring" potential → voice-leading smoothness → range comfort → harmonic interest. Make the weights configurable and expose a single user-facing **"spice" parameter (1–5)** that scales reharmonization density and embellishment frequency — spice 1 is a faithful, singable chart; spice 5 is a contest-style showpiece. Default to 3.

### Voices, ranges, and notation (sounding pitch)

- **Tenor**: harmonizes *above* the lead. Range roughly G3–C5. Mostly sits a 3rd–6th above the melody; lives on the 5th and 7th of the chord more than any other voice. Light by nature — keep it off sustained low notes.
- **Lead**: carries the melody. Range roughly A2–G4. The extracted melody should be transposed (pick the best global transposition) so it sits comfortably here, ideally centered around C3–E4.
- **Baritone**: fills whatever chord tone is left after bass, lead, and tenor are assigned. Range roughly A2–G4.
- **Bass**: range roughly E2–C4; role detailed in the voicing rules below.

**Notation convention:** two staves — Tenor + Lead on a treble clef with an "8vb" (vocal tenor clef), Baritone + Bass on a bass clef. Tenor stems up / Lead stems down on the top staff; Bari stems up / Bass stems down on the bottom.

### Chord vocabulary (BHS-style consonance hierarchy)

Treat the chord palette as tiered, and bias chord selection toward the top tiers:

- **Tier 1 (the sound — use constantly):** major triad; **dominant seventh ("barbershop 7th") — this is the signature sonority and should typically account for somewhere around 30–60% of chords in a finished chart**, mostly as secondary dominants resolving down the circle of fifths.
- **Tier 2 (idiomatic color):** major 6th, minor triad, minor 6th, minor 7th, dominant 9th (voiced rootless or 5th-omitted only when the melody forces it — prefer keeping the root), diminished 7th, half-diminished 7th.
- **Tier 3 (spice, use sparingly and purposefully):** augmented triad (almost always as a passing chord with a chromatically ascending line), dominant 7♭5 (the "French-sixth sound," great in tags), augmented dominant (V+7 pushing to I or IV).
- **Forbidden/avoid (not idiomatic):** major 7ths, add9/add2 colors, sus chords held as stable sonorities (suspensions are fine but must resolve), quartal voicings, power chords / open fifths as destination chords, and any chord missing its 3rd at a point of repose.

Every vertical sonority in the output must be classifiable into this vocabulary — include a chord-legality validator that runs over the finished score and names any violation by measure/beat.

### Harmonic language & reharmonization

- **Circle-of-fifths motion is the engine.** Where the source progression allows, recast diatonic motion as chains of secondary dominants (e.g., I–vi–ii–V becomes I–VI7–II7–V7 — the classic ragtime/barbershop turnaround). Target: strong-beat arrivals approached by their own dominant whenever the melody permits.
- **Tritone substitutions and diminished passing chords** (♯iv°7, ♯i°7, ♭iii°7) are encouraged at higher spice levels to connect diatonic chords chromatically — especially under stepwise melody motion.
- **Cadential acceleration:** increase harmonic rhythm approaching phrase endings (e.g., one chord per bar mid-phrase → two or more chords per bar at the cadence), and decorate final cadences with idiomatic formulas: II7–V7–I, ♭VI7–V7–I, or a iv6–I plagal color.
- **Melody is sacrosanct.** Every melody note of structural length must be a chord tone (see NCT rules below). When the detected chord clashes with the melody, substitute a vocabulary chord containing the melody note that preserves the bass line's logic — never bend the melody to fit a chord you'd prefer.
- The reharmonization should still *track the original song's harmonic skeleton*: keep the original chord's function (tonic/predominant/dominant) at phrase boundaries and only get adventurous in between. A theory nerd should hear both the original tune and the arranger's wit.

### Voicing rules

- **Cone-shaped voicing:** larger intervals at the bottom of the chord, smaller at the top. Keep bass–bari commonly a 4th–octave apart; tenor–lead commonly a 3rd–6th apart. Avoid close-position clusters low in the staff (muddy) and wide gaps between adjacent upper voices (>octave between tenor and lead is a defect except momentarily).
- **Tenor stays above the lead at all times** (brief unisons allowed). Baritone freely crosses above/below the lead as voice-leading demands — that's the bari's job — but should not sit above the tenor.
- **Bass sings the root or 5th the overwhelming majority of the time**, root at every cadence and on most strong beats. The bass should *never* carry the 7th of a dominant chord, and only rarely the 3rd (passing motion).
- **Chord-tone allocation defaults:** on a dominant 7th, all four tones present (no doubling, no omissions); the 7th most often lands in bari or tenor. On triads, double the root (octave or unison), never the 3rd at points of repose; doubling the 5th is acceptable mid-phrase when voice leading earns it.
- **Ring/lock heuristic:** voicings where the lead sings the root or 5th, the chord is in root position, and the spacing is cone-shaped reinforce the overtone series and "ring." Score candidate voicings for ring potential and prefer high-ring voicings on sustained, accented, and cadential chords; tolerate lower-ring voicings on fast-moving connective chords.

### Voice leading

- Prefer common tones and stepwise motion in tenor and bari; the bass may leap (root motion by 4th/5th is its native language). Resolve tendency tones conventionally: chordal 7ths resolve down by step, leading tones up by step (the classic exception — leading tone may drop to the 5th in an inner voice to complete the next chord — is allowed and idiomatic).
- No parallel perfect octaves or unisons between any voice pair; avoid parallel perfect 5ths between bass and any upper voice (brief parallels inside swipes are tolerated, as in real charts).
- Minimize aggregate semitone motion between consecutive chords *except* where a deliberate dramatic leap serves the line (e.g., bass octave drops into a cadence).
- Each part, sung alone, should be a learnable, musical line — run a per-voice singability check: limit awkward intervals (avoid augmented 2nds/4ths and 7ths within a part), cap sustained extreme-range passages, and keep each voice's tessitura centered in its comfortable zone (ranges above).

### Non-chord tones & harmonic rhythm

- Define a structural-length threshold (default: one beat at the detected tempo). Melody notes at or above the threshold get full four-part chord support. Shorter notes may be treated as passing/neighbor tones over sustained harmony — in which case the other three voices hold — or harmonized with brief passing chords (diminished 7ths shine here) at higher spice.
- The other voices need not be strictly homorhythmic with every melody ornament: barbershop texture is homophonic at the *structural* level. Sustain the trio through melodic filigree; move the trio when the harmony moves.

### Embellishments (gate by spice level; only after the core arranger passes its tests)

- **Swipes:** on melody notes sustained ≥2 beats, move one or more harmony voices through an intermediate vocabulary chord while the lead holds (e.g., I → I° → I, or sliding the bari/bass to convert a triad into its own dominant 7th). The classic two-chord swipe on a phrase-ending note is the single most recognizable barbershop gesture — implement this first.
- **Echoes/back-time:** harmony voices echo a short melodic fragment during a melody sustain (spice ≥4, sparing).
- **Bell chords:** voices enter one at a time, bottom-up, stacking a chord — effective on the first chord of a tag or a section opening (spice ≥4).
- **Key change:** optional modulation up a half or whole step for a final section/chorus at spice ≥3, executed with a pivot dominant (V7 of the new key) rather than a cold jump.
- **Tag:** auto-generate a 2–6 bar tag ending at spice ≥3: typically a sustained "post" (lead or tenor holds a high chord tone) while the other voices walk through a II7–V7–(♭VI7)–I progression beneath it, ending on a root-position major triad — optionally with the tenor resolving a suspended 9th or 4th into the final chord. The final chord must ring: root position, lead on root or 5th, cone spacing.

### Tuning & playback (the detail this audience will love)

- Implement a **just intonation playback mode** (toggle in the UI, default on): tune each chord to pure ratios relative to its root — pure major 3rd 5:4, perfect 5th 3:2, and the **harmonic/barbershop 7th at 7:4 (≈31 cents flat of equal temperament)** — while keeping the lead's melodic line anchored near equal temperament / stable pitch center so the song doesn't drift. Document the drift-management strategy in DESIGN.md. Equal temperament remains available as the comparison toggle; the audible difference on locked dominant 7ths is exactly the demo moment that will sell this crowd.
## Lyrics: detection, substitution, and lyric-driven composition

Lyrics are first-class citizens, not decoration. There are three lyric pathways, sharing one underlying text-setting engine.

### Shared foundation: the text-setting engine

Build a reusable module that maps text to notes correctly. It needs:

- **Syllabification & stress**: use the CMU Pronouncing Dictionary (via `pronouncing` or `g2p-en` for out-of-vocabulary words — there will be plenty in user-pasted lyrics) to get per-word syllable counts and lexical stress patterns (1 = primary, 2 = secondary, 0 = unstressed).
- **Prosodic alignment scoring**: a good text setting places stressed syllables on metrically strong beats (downbeats > beat 3 > weak beats) and on agogically accented (longer/higher) notes. Score candidate alignments on stress–meter concordance, and penalize stressed-syllable placement on weak offbeats and unstressed syllables on long climactic notes.
- **Melisma and split handling**: when syllable count < note count for a phrase, assign melismas (one syllable across multiple notes) preferring melismas on open vowels and at phrase peaks; when syllable count > note count, split notes (e.g., a quarter into two eighths at the same pitch) to absorb extra syllables, preferring splits on repeated-pitch or stepwise spots. Both operations must round-trip cleanly through the score model.
- **Correct MusicXML lyric encoding**: `<lyric>` elements with proper `syllabic` values (single/begin/middle/end), hyphenation between syllables of a word, and extender lines under melismas. Lyrics attach to the **Lead** (all four parts sing the same words on the same rhythm at the structural level; harmony-part word echoes are out of scope except inside the echo embellishment).

### Pathway 1 — automatic detection (default for audio uploads)

Align the ASR word timestamps to the extracted melody notes (the onsets won't agree perfectly — use a tolerance window / DTW-style alignment, and trust the melody's rhythm over the ASR's timing). ASR on music is unreliable, so: display a confidence indication, make every lyric editable in the score view, and degrade gracefully — if transcription is garbage (confidence below threshold), fall back to neutral syllables ("doo"/"dah") rather than committing nonsense to the chart.

### Pathway 2 — pasted lyric substitution

The user pastes their own lyrics to be set under the analyzed melody (yes, the canonical test case is homemade rap verses set to Yankee Doodle — the engine should handle wildly mismatched text with grace, not crash or produce unsingable garbage):

- Detect the melody's phrase structure (rests, long notes, cadences delimit phrases) and the pasted text's line structure; align lines to phrases.
- Within each phrase, run the prosodic alignment scorer to choose the best syllable-to-note mapping, using melisma/split operations to absorb syllable-count mismatches up to a sane elasticity bound (configurable; default ±40% syllables per phrase before warning).
- Report a per-phrase **fit diagnosis** in the UI: green (natural fit), yellow (required splits/melismas — show where), red (severe mismatch — show the phrase and let the user edit text or accept the distortion). Never silently mangle either the melody or the text: rhythmic alterations to accommodate lyrics are allowed, pitch alterations are not.

### Pathway 3 — lyric-driven composition (no audio at all)

A separate input mode: paste a poem or lyrics, get an original barbershop arrangement composed from the text alone. This is a text-to-music pipeline with the same arrangement engine as its back half — the composer's job is to produce the (melody, chord progression, lyric alignment) triple that the existing arranger already consumes. Stages:

1. **Prosodic analysis**: scan the text — syllable counts per line, stress patterns (detect the dominant foot: iambic/trochaic/anapestic/dactylic or free), line groupings into stanzas, and rhyme scheme via phoneme-tail matching from CMUdict (AABB, ABAB, AABA…). These determine **meter and form**: e.g., iambic lines suggest pickup-beat phrases; common meter (8.6.8.6) maps naturally to 4-bar phrases; rhyme scheme maps to musical form (rhymed couplets → parallel-period phrase pairs; an AABA stanza → an AABA 32-bar-style form with a contrasting bridge).
2. **Affect analysis**: estimate valence and arousal per stanza and for the whole text. Implement a deterministic lexicon-based core (e.g., NRC-VAD / VADER-style scoring with negation handling) so it's testable, and optionally enrich it with a single Anthropic API call (`claude-sonnet-4-20250514`, JSON-only response: per-stanza valence/arousal in [-1,1], dominant emotion label, and 2–3 imagery keywords) behind a flag, falling back to the lexicon when offline.
3. **Affect → musical parameter mapping** (document this mapping explicitly in DESIGN.md — it's the intellectually interesting part and the part my friends will argue about):
   - *Valence* → mode and harmonic color: high valence → major, bright 6th chords, plagal warmth; low valence → minor keys, with barbershop's minor-adjacent palette (minor 6ths, half-diminished ii⌀7–V7 motion, ♭VI7 borrowings); ambivalent texts → major with prominent borrowed-chord shadows.
   - *Arousal* → tempo (map to a defensible BPM range, e.g., ~60–76 for low, ~96–132 for high), harmonic rhythm density, melodic range width, and embellishment frequency (interacting with, not overriding, the spice slider).
   - *Emotional arc* → key plan and dynamics: a sad-to-hopeful poem might earn a picardy-third ending or a modulation up a step into the final stanza; a darkening arc earns the reverse — mode mixture intensifying toward the end.
4. **Melody generation**: rule-based, constrained generation over the chosen meter/key — arch or wave contours per phrase, predominantly stepwise with expressive leaps (≤ octave) landing on stressed syllables at phrase climaxes, chord tones on strong beats and at line-ends, cadence tones following the rhyme scheme (rhyming lines get rhyming cadential gestures: same scale degree or a deliberate answer, half cadences mid-couplet resolving to authentic cadences at couplet ends), all within the Lead's comfortable tessitura. Seeded randomness for variety; a "re-compose" button re-rolls melody candidates without re-analyzing the text, and candidates are ranked by a melodic-quality cost (contour balance, stress concordance, climax placement near the golden-section point of the form).
5. **Hand-off**: the generated (melody, progression, lyrics) feeds the standard arranger, spice slider and all. A sad poem should come back as a minor-key chart that still sounds like barbershop — minor barbershop exists and has its own conventions (lean harder on the half-diminished and minor-6th colors; final chords may be minor triads or picardy, choose by the text's ending sentiment).

Mode selection in the UI: **(a)** audio only, **(b)** audio + pasted lyrics, **(c)** lyrics only. Pasted-lyrics input is a plain textarea preserving line breaks (line structure is analytical signal — say so in the placeholder text).



- **Rendering**: Use **OpenSheetMusicDisplay (OSMD)** or **VexFlow** to engrave the MusicXML in-browser. Choose whichever gives you a cleaner path to *editing*, and justify the choice in DESIGN.md.
- **Playback**: Four-part synchronized playback with **Tone.js** (or OSMD's playback if it proves sufficient). Requirements:
  - Play / pause / stop, tempo adjustment, and a moving cursor/highlight on the score synced to playback.
  - Per-voice mute and solo (critical for learning parts) and per-voice volume sliders.
  - **Just intonation vs. equal temperament toggle** (per the tuning spec above) — make the A/B comparison effortless during playback.
  - Use a vocal-ish or warm sustained timbre (e.g., a soft synth pad or sampled "ahh") rather than a piano default, if practical.
- **Arranger controls**: the **spice slider (1–5)** lives in the UI with a "re-arrange" button, so the same song can be regenerated at different difficulty levels without re-running audio analysis (cache the extracted melody/chords).
- **Editing** (MuseScore-lite, scoped tightly):
  - Click a note to select it; arrow up/down to change pitch (with audible feedback); keyboard shortcuts for duration changes; delete/insert rest.
  - **Lyric editing**: lyrics render under the Lead's staff with correct hyphenation and melisma extenders; click a syllable to edit it; an "edit all lyrics" panel accepts pasted replacement text and re-runs the text-setting engine, surfacing the per-phrase fit diagnosis.
  - Edits update the underlying MusicXML model and re-render + affect playback immediately.
  - Undo/redo.
  - Export buttons: download MusicXML and download MIDI of the current (edited) arrangement.
- **UX flow**: entry screen offering the three modes (audio / audio + pasted lyrics / lyrics only) with drag-and-drop upload and the lyric textarea → progress states for each pipeline stage (decoding, analyzing, transcribing, arranging, rendering) with honest status, since analysis can take ~30s+ → score view.
- Clean, intentional visual design. Not a Bootstrap default. Doesn't need to be flashy — it needs to look considered.

## Architecture & stack guidance

- **Backend**: Python (FastAPI suggested) handling upload, audio analysis, arrangement, and MusicXML generation. Keep the arrangement engine a pure, well-tested module independent of the web layer.
- **Frontend**: your call — vanilla + Vite, React, or Svelte — but keep the build simple and the editor state model clean (the score model is the single source of truth; rendering is a projection of it).
- Pin dependencies. If a library is flaky to install (e.g., madmom), pick the pragmatic alternative.

## GitHub repository setup (do this first, before writing code)

- A private GitHub repository already exists at `https://github.com/derekhaase259/barbershopify`. Do **not** create a new repo. Instead: `git init`, add the remote (`git remote add origin https://github.com/derekhaase259/barbershopify.git` or use `gh repo clone`/SSH if configured), and verify you can actually push before doing anything else.
- My `gh` authentication state is uncertain. As your very first action, run `gh auth status` and report what you find. If it's not authenticated, stop and tell me exactly what to run (`gh auth login` and the prompts to expect) — do not work around it by embedding tokens, switching the repo to public, or using credentials from anywhere else. If `gh` is unauthenticated but plain `git push` over SSH or cached HTTPS credentials works, that's acceptable — confirm with a test push of the initial commit.
- Add a sensible `.gitignore` from the start (Python venv, `node_modules`, build artifacts, uploaded user audio, model caches like demucs/basic-pitch weights, `.DS_Store`).
- Commit at meaningful checkpoints — at minimum once per milestone, with clear commit messages. Push after each milestone so the remote always reflects a working state.
- The repo will be shared with friends who are not developers-by-trade, so the README has to carry them (see README section below).

## Test songs (public domain audio from the web)

Find and download 2–4 real public-domain audio recordings from the web to use as bundled test songs, and commit them to a `test_songs/` directory (keep each file reasonably small — trim to ~60–90 seconds with ffmpeg if needed, and keep the total under ~25 MB so the repo stays clone-friendly; use Git LFS only if you must).

- **Both the composition and the specific recording must be public domain.** In the US, sound recordings published before 1923 entered the public domain (with pre-1924+ recordings rolling in annually under the Music Modernization Act), so pre-1923 recordings of pre-1923 songs are the safe target. Good sources: the Library of Congress National Jukebox, Internet Archive's 78rpm collections, and Wikimedia Commons — prefer items explicitly marked public domain. Musopen is good for classical but prefer *songs* with clear melodies here.
- Ideal picks are early-1900s popular songs with strong singable melodies — the era barbershop actually comes from (e.g., "Sweet Adeline," "Down by the Old Mill Stream," "My Wild Irish Rose," "Shine On, Harvest Moon," "Take Me Out to the Ball Game").  Old acoustic recordings are noisy and mid-heavy; that's fine — they're a realistic stress test for the analysis pipeline, and it's part of why the hardcoded demo path also exists.
- Create `test_songs/SOURCES.md` recording, for each file: title, performer, year, source URL, and the public-domain basis. Do not commit anything whose status you can't verify — fewer verified songs beats more questionable ones.
- Wire these into the app: the upload screen should offer the bundled test songs as one-click choices alongside drag-and-drop.

## README (written for my friends, not for you)

The `README.md` is the front door for non-experts cloning a private repo. It must include:
- One-paragraph description of what barbershopify does, with a screenshot of the score view once it exists.
- **Prerequisites** with install links/commands per OS (macOS + at least one of Windows/Linux): git, Python version, Node version, ffmpeg.
- **Step-by-step setup from zero**: how to accept the GitHub invite, clone the private repo (HTTPS instructions; mention `gh repo clone` as the easy path), create the venv, install backend and frontend deps, and start the app — ideally collapsed into one command via a `Makefile` or a `run.sh`/`run.ps1` script.
- How to use it: try a bundled test song, upload your own file, playback controls, editing keys, exporting MusicXML/MIDI.
- A short troubleshooting section for the predictable failures (ffmpeg missing, port in use, Python version mismatch).
- Verify the README by following it literally in a clean directory before calling the project done.

## Quality bar & testing

- Unit-test the arrangement engine hard: given a known melody + chord progression (no audio involved), assert — range and tessitura compliance per voice; every sonority passes the chord-vocabulary validator; tenor never below lead; bass never on a chordal 7th; no doubled 3rds at points of repose; chordal 7ths resolve down by step; no parallel octaves between any pair; bass root/5th percentage above threshold; dominant-7th share of total chords within the target band; melody preserved verbatim in the lead; final chord is a root-position major triad with high ring score. Test across all spice levels — spice 1 and spice 5 should produce measurably different charts from the same input, and both must pass every legality test.
- Test the just intonation engine numerically: assert the rendered frequencies of a tuned dominant 7th hit 4:5:6:7 ratios within a cent.
- Test the text-setting engine: syllabification of known words (including OOV handling), stress–meter concordance on a known tune + lyric pair (e.g., assert "Yankee Doodle went to town" lands its stressed syllables on strong beats), melisma/split round-tripping, and valid MusicXML lyric encoding (syllabic values, hyphens, extenders).
- Test lyric-driven composition deterministically (fixed seed): an unambiguously sad poem yields a minor key, slow tempo band, and low embellishment density; an exuberant one yields major, faster, denser — plus the standard arranger legality suite on every composed output. Assert rhyme-scheme detection on known stanzas (AABB, ABAB, AABA) and the resulting form mapping.
- Include 2–3 tiny built-in demo inputs that bypass audio analysis (a hardcoded melody/progression, e.g., a public-domain tune like "My Wild Irish Rose" or "Shine On Harvest Moon" — both pre-1928) so the arranger and editor can be exercised and demoed without an upload.
- For the audio pipeline, test with a generated WAV (e.g., synthesize a sine melody over triads) and assert the extracted melody/chords are close — then run the bundled public-domain test songs through it and sanity-check the results by ear.

## Milestones (build in this order, get each working end-to-end before moving on)

0. **Repo setup**: `gh auth status` check, git init, remote wired to the existing private repo, verified test push, `.gitignore`, `SPEC.md` + `CLAUDE.md` committed, stub README.
1. **Skeleton + demo path**: backend + frontend wired; hardcoded demo melody/progression → arrangement engine v1 → MusicXML → rendered score in browser.
2. **Playback**: four-part synced playback with cursor, mute/solo.
3. **Audio pipeline + test songs**: source and verify the public-domain test recordings, then upload → melody + chord extraction → feeds the same arranger; test songs selectable in the UI.
4. **Editing**: selection, pitch/duration editing, undo/redo, exports.
5. **Lyrics — setting and detection**: text-setting engine, lyric rendering/editing in the score, ASR transcription pathway with graceful degradation, pasted-lyric substitution with fit diagnosis.
6. **Lyric-driven composition mode**: prosodic + affect analysis, affect→parameter mapping, melody generation, wired into the standard arranger.
7. **Polish + README**: embellishments (swipes first, then tags, key changes, bells per spice gating), just intonation playback mode, voice-leading refinements; finalize the friend-proof README and verify it from a clean clone.

At each milestone, run it yourself, look at/listen to the actual output, and fix what's musically or visually wrong before declaring it done. I will judge this on whether the demo arrangement actually sounds like barbershop.

## Addendum (2026-06-11): song lookup

Agreed extension beyond the original brief: uploads are fingerprinted (chromaprint →
AcoustID) to identify the song, and identified songs get real lyrics from LRCLIB instead
of speech recognition, which is skipped entirely on a hit. Free services only; the one
AcoustID application key is registered to us and committed. Melody and chords remain
audio-analysis territory — no free database for them exists. Any lookup failure degrades
to the previous behavior. Full design: docs/superpowers/specs/2026-06-11-song-lookup-design.md.

Second extension, same date: identified songs also get their chord progression
cross-checked against Chordie. Audio analysis keeps all timing; the tab corrects chord
identities only, and only when the best of 12 transpositions agrees with ≥50% of the
analyzed roots — otherwise the tab is discarded. Ultimate Guitar and the other major tab
sites Cloudflare-block plain requests (probed 2026-06-11); Chordie serves embedded
ChordPro cleanly. Full design: docs/superpowers/specs/2026-06-11-tab-chords-design.md.
