# Song lookup: identification + real lyrics

**Date:** 2026-06-11 · **Status:** approved · **Owner:** Derek + Claude

## Why

The analysis pipeline reconstructs everything from the waveform. For melody and chords that
is unavoidable — no free database serves "the melody of song X"; even Shazam only matches
fingerprints to a name. But for *identity* and *lyrics*, lookup beats analysis: crowdsourced
databases already hold the answer, and our weakest link is Whisper guessing words through
78-rpm surface noise. This feature identifies the uploaded song and fetches its real lyrics,
keeping audio analysis as the backbone for everything musical.

Constraint honored: every service is free and needs no key from a user who clones the repo.
The one key involved (AcoustID's application key) is registered once by us and committed.

## Decisions (made 2026-06-11)

| Decision | Choice |
|---|---|
| Scope | Identity + lyrics. No chord/melody lookup (no such database); no MIDI hunting (out of scope, see below). |
| Apply UX | Auto-apply looked-up lyrics; badge shows identification + lyric source; existing editing/undo covers rejection. |
| Architecture | Integrated pipeline stage inside `analyze()`, not a post-hoc endpoint. Skips ASR when real lyrics land. |

## Services

| Service | Gives us | Auth | Notes |
|---|---|---|---|
| `fpcalc` (chromaprint CLI) | audio fingerprint | none | optional system dep: `libchromaprint-tools` (apt) / `chromaprint` (brew) |
| AcoustID `/v2/lookup` | recording match + title/artist/year (`meta=recordings+releasegroups`) | app key, committed in repo | free for non-commercial; we register once |
| LRCLIB (`/api/get`, `/api/search`) | plain + time-synced (LRC) lyrics | none, no rate limit | built for FOSS players; ~3M lyrics |

No separate MusicBrainz call: AcoustID's `meta=` parameter returns the metadata we need.

## Components

New pure module `backend/barbershop/lookup/` — like the arranger, it imports nothing from
FastAPI and is testable offline with mocked HTTP. Uses the already-pinned `requests`; no new
Python dependencies.

- **`lookup/identify.py`** — `identify(path, duration) -> SongIdentity | None`. Shells out to
  `fpcalc -json`, then one GET to AcoustID. `SongIdentity` (frozen dataclass): `title`,
  `artist`, `year`, `recording_mbid`, `match_score`. Returns `None` when fpcalc is missing,
  the network fails, or the best match scores below 0.5. The AcoustID application key lives
  here as a constant.
- **`lookup/lyrics.py`** — `fetch_lyrics(identity, duration) -> LookedUpLyrics | None`.
  Tries LRCLIB `/api/get` (artist + track + duration), falls back to `/api/search?q=` with a
  duration-closeness tiebreak. Returns parsed `synced` lines (`list[(seconds, text)]`) and/or
  `plain` text.
- **`textset/align.py` + `set_timed_lines(melody, lines, time)`** — sets synced lyrics;
  `lines` is `list[(onset_tick, text)]` (the pipeline converts LRC seconds to ticks via the
  beat grid, so `textset` keeps zero dependency on the analysis module). Each line is
  assigned to the melody phrase containing its onset, then the existing per-phrase
  `_align_phrase` DP runs unchanged. Instrumental intros and
  interludes correctly receive no words — an improvement over the paste path's positional
  1:1 line-to-phrase mapping, which stays as-is for plain text.

## Data flow

In `pipeline.analyze()`, after melody and chord extraction, before the ASR block:

1. `identify(path, duration)` (~1–3 s). On a hit, the identified title becomes the chart
   title (beating the filename-derived one); artist/year/mbid ride along as a new
   `AnalysisResult.identity` field.
2. If `lyrics=True` and identified: `fetch_lyrics(...)`. Synced hit → `set_timed_lines`;
   plain-only → existing `set_lyrics`. Either way `lyrics_source = "lrclib"` and Whisper
   never runs — skipping the 15–60 s ASR step and the first-run 150 MB model download.
3. Any miss at any stage → exactly today's path: ASR, then doo/dah fallback.
4. Cache: key bumps `-v3` → `-v4`; cache stores `identity` and lyric source. Title rule on
   cache hits: an identified title wins over the filename-derived `title=` parameter
   (today the parameter always clobbers the cached title).
5. API: `/api/upload` and `/api/test-songs/{id}/arrange` responses gain
   `"identity": {...} | null` beside the existing `"lyrics"` field. No new endpoints.

## Error handling

One rule: **any lookup failure degrades to exactly today's behavior.**

- `identify()` and `fetch_lyrics()` catch everything internally and return `None`; the
  pipeline wraps the calls in a second `try/except` so even a lookup-module bug cannot
  break analysis.
- HTTP timeouts `(3.05 s connect, 10 s read)` on every call. Offline adds seconds at worst,
  typically nothing (instant connection refusal).
- Bad-fitting LRCLIB lyrics are not a failure: the existing `FitReport` machinery grades
  each phrase green/yellow/red and everything stays editable.
- One `logging.info` line per skipped stage; never a user-facing error.

## UI

The store keeps `identity` from the response. When present, the score header shows
"♪ Identified: *Title* — *Artist* (*year*)" and the lyric-source indicator gains an
`lrclib` state. No badge when unidentified.

## Setup & docs

- README install table gains `fpcalc` as *optional — enables song identification*; a
  troubleshooting row covers "song wasn't identified / wrong lyrics"; a privacy note says
  fingerprints (not audio) go to acoustid.org and title/artist to lrclib.net.
- `SPEC.md` addendum and `DESIGN.md` rationale (including "no free melody/chord database
  exists; analysis remains the backbone") land in the same commit as the feature.

## Testing (all network mocked; pytest stays offline)

- `identify()`: parses an AcoustID fixture; `None` on missing fpcalc, timeout, garbage
  JSON, low score.
- `fetch_lyrics()`: `/api/get` hit; `/api/search` fallback with duration tiebreak; LRC
  parsing edge cases.
- `set_timed_lines()`: lines land in the right phrases by time; instrumental phrases get
  no words; out-of-range timestamps clamp.
- Pipeline: lookup monkeypatched to raise → output identical to today's (fail-soft as a
  test); lookup hit → ASR verifiably never called; cache v4 round-trips `identity`.
- Endpoints: `identity` present on hit, `null` on miss.
- Standing verification: run against the real Victrola 78s and at least one modern upload,
  and listen.

## Out of scope (considered, rejected for now)

- **MIDI hunting** (archive.org MIDI search for the identified song): perfect
  melody/chords when it hits, but spotty coverage, wildly varying quality, and
  melody-channel detection is its own analysis problem. Revisit if lookup proves valuable.
- **Chord/melody lookup**: no free, no-key source exists.
- **Key/tempo priors from metadata**: AcousticBrainz is defunct; nothing reliable remains.

## One human step

Register a free AcoustID application key at acoustid.org (needs a MusicBrainz or Google
login; ~2 minutes), then commit it as the constant in `lookup/identify.py`. Everything
else works from a fresh clone with no keys.
