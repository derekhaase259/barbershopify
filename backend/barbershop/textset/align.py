"""Prosodic text setting: map syllables to notes, phrase by phrase.

A DP per phrase chooses, for each note, how many syllables it carries:
0 = melisma (the previous syllable extends through this note),
1 = plain assignment, 2+ = the note splits into equal sub-notes.
Costs reward stressed syllables on metrically strong / agogically long
notes, prefer melismas on open vowels, and price splits by how short
the resulting sub-notes get. Rhythm may change (splits); pitch never.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

from barbershop.score import Lyric, Note, Syllabic, TimeSig
from barbershop.textset.phrases import split_phrases
from barbershop.textset.syllabify import Syllable, syllabify_line

_MAX_SPLIT = 4
_HUGE = 1e9


@dataclass
class FitReport:
    phrase_index: int
    status: str  # green / yellow / red
    syllables: int
    notes: int
    melismas: int
    splits: int
    detail: str


def _strength(time: TimeSig, onset: int) -> float:
    beat = time.beat_of(onset)
    if beat == 0.0:
        return 3.0
    if time.beats % 2 == 0 and beat == time.beats / 2:
        return 2.0
    if beat == int(beat):
        return 1.0
    return 0.0


def _stress_cost(syll: Syllable, time: TimeSig, note: Note) -> float:
    strength = _strength(time, note.onset)
    long_note = note.duration >= time.ticks_per_beat
    cost = 0.0
    if syll.stress == 1:
        cost += max(0.0, 2.0 - strength)
        if not long_note and strength < 2:
            cost += 0.5
    elif syll.stress == 0:
        if strength >= 3.0:
            cost += 0.75
        if note.duration >= 2 * time.ticks_per_beat:
            cost += 1.0  # unstressed syllable on a long climactic note
    return cost


def _melisma_cost(prev_syll: Syllable | None) -> float:
    if prev_syll is None:
        return _HUGE  # nothing to extend
    return 0.5 if prev_syll.open_vowel else 1.5


def _split_cost(k: int, note: Note, time: TimeSig) -> float:
    sub = note.duration / k
    cost = 1.0 * (k - 1)
    if sub < 240:  # sub-eighth syllables get pattery
        cost += 1.5 * (k - 1)
    if sub < 120:
        cost += 3.0 * (k - 1)
    return cost


def _align_phrase(
    notes: list[Note], sylls: list[Syllable], time: TimeSig
) -> tuple[list[Note], int, int] | None:
    """DP: how many syllables does each note carry? Returns rebuilt notes."""
    n, m = len(notes), len(sylls)
    # cost[j][i]: first j notes consumed, first i syllables placed
    cost = [[_HUGE] * (m + 1) for _ in range(n + 1)]
    choice: list[list[int]] = [[-1] * (m + 1) for _ in range(n + 1)]
    cost[0][0] = 0.0
    for j in range(n):
        note = notes[j]
        for i in range(m + 1):
            if cost[j][i] >= _HUGE:
                continue
            # melisma: this note extends the previous syllable
            c = cost[j][i] + _melisma_cost(sylls[i - 1] if i > 0 else None)
            if c < cost[j + 1][i]:
                cost[j + 1][i], choice[j + 1][i] = c, 0
            # k syllables on this note (k=1 plain, k>1 split)
            for k in range(1, _MAX_SPLIT + 1):
                if i + k > m:
                    break
                c = cost[j][i] + _stress_cost(sylls[i], time, note)
                if k > 1:
                    c += _split_cost(k, note, time)
                if c < cost[j + 1][i + k]:
                    cost[j + 1][i + k], choice[j + 1][i + k] = c, k
    if cost[n][m] >= _HUGE:
        return None

    # backtrack: syllable count per note
    counts: list[int] = []
    i = m
    for j in range(n, 0, -1):
        k = choice[j][i]
        counts.append(k)
        i -= k
    counts.reverse()

    # rebuild notes with lyrics, splits, melisma extenders
    out: list[Note] = []
    syll_iter = iter(sylls)
    melismas = splits = 0
    last_lyric_note: Note | None = None
    for note, k in zip(notes, counts):
        if k == 0:
            melismas += 1
            if last_lyric_note is not None and last_lyric_note.lyric is not None:
                last_lyric_note.lyric.extend = True
            out.append(note.model_copy(update={"lyric": None}))
            continue
        if k == 1:
            sub_notes = [note.model_copy()]
        else:
            splits += 1
            sub = note.duration // k
            sub_notes = []
            for x in range(k):
                dur = note.duration - sub * (k - 1) if x == k - 1 else sub
                sub_notes.append(
                    Note(onset=note.onset + sub * x, duration=dur, midi=note.midi)
                )
        for sn in sub_notes:
            syll = next(syll_iter)
            if syll.word_begin and syll.word_end:
                syllabic = Syllabic.single
            elif syll.word_begin:
                syllabic = Syllabic.begin
            elif syll.word_end:
                syllabic = Syllabic.end
            else:
                syllabic = Syllabic.middle
            sn.lyric = Lyric(text=syll.text, syllabic=syllabic, extend=False)
            out.append(sn)
            last_lyric_note = sn
    return out, melismas, splits


def set_lyrics(
    melody: list[Note], text: str, time: TimeSig, *, elasticity: float = 0.4
) -> tuple[list[Note], list[FitReport]]:
    """Set pasted text under a melody. Returns (new melody, per-phrase fit)."""
    phrases = split_phrases(melody)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    # align lines to phrases 1:1; spill leftovers into the final slot
    line_sylls = [syllabify_line(ln) for ln in lines]
    per_phrase: list[list[Syllable]] = []
    for p in range(len(phrases)):
        if p < len(line_sylls):
            per_phrase.append(line_sylls[p])
        else:
            per_phrase.append([])
    for extra in line_sylls[len(phrases):]:
        per_phrase[-1].extend(extra)

    return _set_per_phrase(phrases, per_phrase, time, elasticity=elasticity)


def set_timed_lines(
    melody: list[Note],
    lines: list[tuple[int, str]],
    time: TimeSig,
    *,
    elasticity: float = 0.4,
) -> tuple[list[Note], list[FitReport]]:
    """Set time-anchored lyric lines (onset_tick, text) under a melody.
    Each line lands in the phrase containing its onset, so instrumental
    intros and interludes correctly receive no words. The caller converts
    wall-clock stamps to ticks; textset stays ignorant of beat grids."""
    GRACE = 240  # singers and LRC stamps run up to a half-beat early
    phrases = split_phrases(melody)
    starts = [phrase[0].onset for phrase in phrases]
    per_phrase: list[list[Syllable]] = [[] for _ in phrases]
    for tick, text in sorted(lines, key=lambda x: x[0]):
        idx = max(0, bisect_right(starts, tick + GRACE) - 1)
        per_phrase[idx].extend(syllabify_line(text))
    return _set_per_phrase(phrases, per_phrase, time, elasticity=elasticity)


def _set_per_phrase(
    phrases: list[list[Note]],
    per_phrase: list[list[Syllable]],
    time: TimeSig,
    *,
    elasticity: float,
) -> tuple[list[Note], list[FitReport]]:
    out_notes: list[Note] = []
    reports: list[FitReport] = []
    for idx, (phrase, sylls) in enumerate(zip(phrases, per_phrase)):
        n, m = len(phrase), len(sylls)
        if m == 0:
            out_notes.extend(nn.model_copy(update={"lyric": None}) for nn in phrase)
            reports.append(FitReport(idx, "yellow", 0, n, 0, 0, "no text for this phrase"))
            continue
        ratio = abs(m - n) / max(1, n)
        aligned = _align_phrase(phrase, sylls, time)
        if aligned is None:
            # beyond what splits/melismas can absorb: hard-truncate
            usable = sylls[: n * _MAX_SPLIT]
            aligned = _align_phrase(phrase, usable, time)
            assert aligned is not None
            ratio = max(ratio, 1.0)
        notes, melismas, splits = aligned
        out_notes.extend(notes)
        if ratio > elasticity:
            status = "red"
            detail = f"{m} syllables vs {n} notes — severe mismatch"
        elif melismas or splits:
            status = "yellow"
            detail = f"{melismas} melismas, {splits} split notes"
        else:
            status = "green"
            detail = "natural fit"
        reports.append(FitReport(idx, status, m, n, melismas, splits, detail))
    return out_notes, reports
