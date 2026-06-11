# Tab chords: correct the analyzed progression from guitar tab sites

**Date:** 2026-06-11 · **Status:** approved · **Owner:** Derek + Claude
**Builds on:** `2026-06-11-song-lookup-design.md` (identification + lyrics, shipped as PR #4)

## Why

Chroma-based chord recognition gets identities wrong in predictable ways (false minors,
wrong roots, missed sevenths) even when its timing is right. Guitar tab sites hold
crowdsourced chord progressions for millions of songs — untimed and possibly transposed,
but identity-rich. This feature fetches the song's tab (using the AcoustID identity we
already have) and uses it to *correct chord identities* while audio analysis keeps
owning all timing. The harmonizer treats the input progression as its prior and key
detection votes from chord labels, so better chords directly produce better arrangements.

## Decisions (made 2026-06-11)

| Decision | Choice |
|---|---|
| Trigger | Identified songs only — piggybacks on AcoustID, like lyrics. No manual title/artist field. |
| Integration | **Align + relabel**: audio keeps span timing; tab corrects roots/qualities via 12-transposition global alignment; ≥50% root-agreement gate or the tab is discarded. Not a Viterbi prior (no clean reject gate, reopens tuning); not full tab reconstruction (too fragile). |
| Source | ~~Ultimate Guitar~~ **Chordie** (decided by the Task 0 probe, 2026-06-11: UG, e-chords, UkuTabs, and Cifra Club song pages all serve Cloudflare challenges to plain `requests`; Chordie serves 200s and embeds clean ChordPro source). One source; the feature fail-softs, so a second scraper isn't worth its fragile surface. |
| Capo/tab-key metadata | Deliberately ignored — the 12-transposition search subsumes capo, transposed tabs, and off-pitch transfers. |
| Disagreement | Melody stays sacrosanct: corrected chords are still only the harmonizer's prior; melody-containment remains the hard constraint. |

## Components (all in `backend/barbershop/lookup/`)

- **`tabs.py`** — `fetch_candidates(identity) -> list[TabChords]`. Plain `requests` with a
  browser User-Agent against Chordie: (1) search
  (`https://www.chordie.com/results.php?q={title}` — title only; appending the artist
  makes Chordie return that artist's *other* songs, found live 2026-06-11), collect the
  song links (`/chord.pere/...`), deduplicated, first 5 candidates; (2) fetch candidates
  in order and keep those whose embedded ChordPro source (`<textarea id="chordproContent">`)
  matches the identity. **Amended 2026-06-11 (live verification finding):** title match
  is mandatory (majority of the wanted title's tokens — any-overlap let "Jude the
  Obscene" impersonate "Hey Jude"), but artist match only *ranks*: same-artist sheets
  are tried first, then same-title covers, because covers keep the harmony and the ≥50%
  alignment gate against the actual audio is the real arbiter (strict artist matching
  cost us every Hallelujah, which exists on Chordie only as covers). `fetch_candidates`
  returns the ordered list; the pipeline tries each through `apply_tab` until one passes
  the gate. Chord tokens come from the ChordPro body in order: strip `{sot}…{eot}`
  tablature blocks and `#` comment lines, then collect inline `[C]`/`[F#m7]` brackets.
  Require ≥ 4 tokens. `TabChords(chords: list[str], url: str, artist: str, title: str)`.
  Never raises.
- **`chordnames.py`** — `parse_chord(name) -> tuple[int, str] | None` mapping guitar
  names into the arranger vocabulary: `m→min`, `7→dom7`, `m7→min7`, `maj7→maj6` (the
  barbershop substitute; maj7 is never voiced), `m7b5→halfdim7`, `dim/dim7→dim7`,
  `9→dom9`, `6→maj6`, `m6→min6`, `aug→aug`, `sus2/sus4/add*/5→maj`. Slash chords take the
  pre-slash chord (the bass voice is the arranger's business). Unparseable tokens drop out.
- **`align.py`** — pure algorithm, no network. `apply_tab(spans, tab) -> TabAlignment | None`:
  compress consecutive duplicate tab chords; for each transposition 0–11, global
  Needleman–Wunsch against the analyzed span sequence (substitution: 0 same root+quality,
  small penalty same root/different quality, larger by root distance; gap costs both
  directions); keep the best. Gate: root-agreement on aligned spans ≥ 0.5, else `None`.
  On success: aligned spans take the tab's (transposed) root/quality, gap spans keep
  analyzed values. `TabAlignment(spans, agreement: float, transposition: int, url: str)`.

## Data flow (`pipeline.analyze()`)

Identification **moves earlier** — right after beat tracking (it only needs the path) —
because chord correction must precede key detection. New order:

1. beats → chord labels → tempo-level correction → meter/phase *(pure audio, unchanged —
   tabs have no timing)*
2. spans → **tab correction** (`identity` present → `fetch_chords` → `apply_tab`, both
   fail-soft) → **key detection on corrected spans**
3. melody → guards → lyrics *(unchanged from the lyrics feature)*

Cache bumps `-v4` → `-v5`, storing `chord_source` (`"audio"` | `"tab"`), `chord_agreement`,
`tab_url`. API: `/api/upload` and `/api/test-songs/{id}/arrange` responses gain
`"chords": {"source": ..., "agreement": ..., "tab_url": ...}` beside `lyrics`/`identity`.

## Error handling

- `fetch_chords()` catches everything (HTTP errors, Cloudflare challenge pages, missing
  `js-store`, bad JSON, zero chord markers) → `None` + one `log.info` line. Timeouts
  `(3.05, 10)` per request, two requests max.
- Pipeline call site wrapped in belt-and-suspenders `try/except`.
- **Quality gate:** a fetched-but-wrong tab (cover, simplification, wrong song) must agree
  with ≥50% of the analyzed roots before it changes anything. No path exists where a tab
  degrades the chords without first agreeing with half of them. Below the gate:
  progression untouched, `chord_source: "audio"`.

## UI

One sidebar fine-print row, only when a tab was applied: **"Chords — matched tab (NN%
agreement)"**. No row = pure audio analysis. No new controls.

## Testing (network mocked; conftest gains a `fetch_chords -> None` suite-wide stub)

- `chordnames`: table-driven — naturals/sharps/flats, m7b5, maj7→maj6, slash, garbage.
- `tabs`: trimmed real `js-store` fixtures for search + tab pages; `None` on missing div,
  bad JSON, no markers.
- `align`: transposition recovery (+3-semitone tab → t=3); false-minor correction (audio
  Am, tab C → corrected); gaps absorbed; shuffled-garbage tab scores below gate → `None`.
- Pipeline: applied tab → corrected spans reach key detection, `chord_source == "tab"`;
  rejected tab → output identical to today; raising tab path → caught.
- Endpoint: `chords` field present on both analyze endpoints.
- Standing verification: a real identified song end-to-end, plus the offline probe.

## Known risk (and Task 0 — resolved 2026-06-11)

The Task 0 live probe ran during planning. Findings: Ultimate Guitar (search **and** tab
pages, even with full browser headers), e-chords, UkuTabs, and Cifra Club song pages all
return 403 Cloudflare challenges to plain `requests`. **Chordie** serves 200s: its search
works and every song page embeds the full ChordPro source in
`<textarea id="chordproContent">` — cleaner to parse than UG's `[ch]` markup. The source
swap above is the fallback the original risk section prescribed. Chordie aggregates
third-party tab sites (e.g. guitaretab.com), so coverage is smaller than UG's and search
relevance is looser — hence the artist/title verification step and the alignment gate.
Scraping remains the least stable dependency in the project; whatever breaks later, the
runtime failure mode is always "chords come from audio analysis."

## Out of scope

- Other tab sites (Chordie, E-CHORDS, AZchords) — same interface, add only if UG dies.
- Using tab lyrics as an LRCLIB fallback.
- Splitting/merging span boundaries from tab structure (section markers, repeats).
- Manual title/artist override UI.
