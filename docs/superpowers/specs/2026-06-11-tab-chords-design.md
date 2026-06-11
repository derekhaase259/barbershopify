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
| Source | Ultimate Guitar only, scraped from the embedded `js-store` JSON. One source; the feature fail-softs, so a second scraper isn't worth its fragile surface. |
| Capo/tab-key metadata | Deliberately ignored — the 12-transposition search subsumes capo, transposed tabs, and off-pitch transfers. |
| Disagreement | Melody stays sacrosanct: corrected chords are still only the harmonizer's prior; melody-containment remains the hard constraint. |

## Components (all in `backend/barbershop/lookup/`)

- **`tabs.py`** — `fetch_chords(identity) -> TabChords | None`. Two requests, plain
  `requests` with a browser User-Agent: (1) UG search
  (`search.php?search_type=title&value={artist} {title}`), read `js-store` JSON, pick the
  best Chords-type result by rating × votes; (2) the tab page, extract chord tokens in
  order from `[ch]...[/ch]` markers in `wiki_tab.content`.
  `TabChords(chords: list[str], url: str, votes: int)`. Never raises.
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

## Known risk (and Task 0)

Ultimate Guitar's tolerance of plain `requests` is the least stable dependency in the
project — scrapers work today, but Cloudflare policies change. **Implementation Task 0 is
a live probe** (one search page + one tab page) before anything is built on the
assumption. If UG blocks: try header adjustments; failing that, swap the source site
behind the same `TabChords` interface — and report back before proceeding. Whatever
breaks later, the runtime failure mode is always "chords come from audio analysis."

## Out of scope

- Other tab sites (Chordie, E-CHORDS, AZchords) — same interface, add only if UG dies.
- Using tab lyrics as an LRCLIB fallback.
- Splitting/merging span boundaries from tab structure (section markers, repeats).
- Manual title/artist override UI.
