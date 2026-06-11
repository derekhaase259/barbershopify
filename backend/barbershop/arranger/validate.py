"""Chart legality validator + quality metrics (SPEC.md quality bar).

Walks every vertical sonority of a finished score and names violations
by measure and beat: vocabulary legality, voice crossing, ranges, bass
discipline, doubled thirds at repose, unresolved chordal 7ths, parallel
octaves/fifths. NCT verticals (lead filigree under a held trio) are
exempt from full classification but the trio must stay on chord tones.

Also measures progression adherence: the fraction of structural
verticals whose actually-sung pitches realize the *input* progression,
classified polyphonically — never by trusting the score's chord labels.
"""
from __future__ import annotations

from dataclasses import dataclass

from barbershop.score import ChordSpan, KeySig, Note, Score, VoiceName
from barbershop.vocabulary import CHORDS, chord_degree, chord_pcs, classify
from barbershop.arranger.config import RANGES

_DOM_FAMILY = ("dom7", "dom9", "aug7", "dom7b5")
_TRIO = (VoiceName.tenor, VoiceName.bari, VoiceName.bass)


@dataclass(frozen=True)
class Violation:
    measure: int  # 1-indexed
    beat: float  # 1-indexed
    kind: str
    detail: str

    def __str__(self) -> str:
        return f"m{self.measure} b{self.beat:g}: [{self.kind}] {self.detail}"


def _sounding(notes: list[Note], tick: int) -> Note | None:
    for n in notes:
        if n.onset <= tick < n.end:
            return n
    return None


def _chord_at(chords: list[ChordSpan], tick: int) -> ChordSpan | None:
    for c in chords:
        if c.onset <= tick < c.end:
            return c
    return None


def _seventh_pc(chord: ChordSpan) -> int | None:
    for interval, name in CHORDS[chord.quality].degrees.items():
        if name == "seventh":
            return (chord.root_pc + interval) % 12
    return None


def _event_ticks(score: Score) -> list[int]:
    return sorted({n.onset for notes in score.voices.values() for n in notes})


def _phrase_final_lead_notes(lead: list[Note], threshold: int) -> set[int]:
    """Onsets of lead notes that end a contiguous sung span AND are of
    structural length — repose implies sustain; a short trailing ornament
    is not a point of repose."""
    out = set()
    for i, n in enumerate(lead):
        if (i + 1 == len(lead) or lead[i + 1].onset > n.end) and n.duration >= threshold:
            out.add(n.onset)
    return out


def validate(
    score: Score,
    threshold: int | None = None,
    *,
    input_chords: list[ChordSpan] | None = None,
    input_key: KeySig | None = None,
) -> list[Violation]:
    if threshold is None:
        threshold = score.time.ticks_per_beat
    out: list[Violation] = []

    def report(tick: int, kind: str, detail: str) -> None:
        out.append(
            Violation(
                measure=score.time.measure_of(tick) + 1,
                beat=score.time.beat_of(tick) + 1,
                kind=kind,
                detail=detail,
            )
        )

    # --- ranges (per note, any voice) ---
    for voice, notes in score.voices.items():
        lo, hi = RANGES[voice.value]
        for n in notes:
            if not (lo <= n.midi <= hi):
                report(n.onset, "range", f"{voice.value} sings midi {n.midi}, range is {lo}..{hi}")

    lead_notes = score.voices[VoiceName.lead]
    repose_onsets = _phrase_final_lead_notes(lead_notes, threshold)
    ticks = _event_ticks(score)

    verticals: list[tuple[int, dict[VoiceName, Note]]] = []
    for t in ticks:
        sounding = {v: n for v in VoiceName if (n := _sounding(score.voices[v], t))}
        # --- crossing rules apply whenever the voices in question sound ---
        if VoiceName.tenor in sounding and VoiceName.lead in sounding:
            if sounding[VoiceName.tenor].midi < sounding[VoiceName.lead].midi:
                report(t, "crossing", "tenor below lead")
        if VoiceName.tenor in sounding and VoiceName.bari in sounding:
            if sounding[VoiceName.bari].midi > sounding[VoiceName.tenor].midi:
                report(t, "crossing", "bari above tenor")
        if VoiceName.bass in sounding:
            for upper in (VoiceName.tenor, VoiceName.lead, VoiceName.bari):
                if upper in sounding and sounding[upper].midi < sounding[VoiceName.bass].midi:
                    report(t, "crossing", f"bass above {upper.value}")
        if len(sounding) == 4:
            verticals.append((t, sounding))

    for t, sounding in verticals:
        chord = _chord_at(score.chords, t)
        lead_note = sounding[VoiceName.lead]
        structural = lead_note.duration >= threshold
        pcs = tuple(sounding[v].midi % 12 for v in VoiceName)

        # --- vocabulary legality ---
        if structural:
            if not classify(pcs):
                report(t, "vocabulary", f"unclassifiable sonority pcs={sorted(set(pcs))}")
        elif chord is not None:
            trio_pcs = {sounding[v].midi % 12 for v in _TRIO}
            if not trio_pcs <= chord_pcs(chord.root_pc, chord.quality):
                report(t, "vocabulary", "trio off chord tones under lead filigree")

        if chord is None:
            continue

        # --- bass discipline ---
        bass_pc = sounding[VoiceName.bass].midi % 12
        seventh = _seventh_pc(chord)
        if seventh is not None and bass_pc == seventh:
            report(t, "bass-seventh", f"bass on the 7th of {chord.quality}")

        # --- doubled third at repose ---
        if lead_note.onset in repose_onsets:
            third_pc = (chord.root_pc + next(
                iv for iv, deg in CHORDS[chord.quality].degrees.items() if deg == "third"
            )) % 12
            if sum(1 for p in pcs if p == third_pc) > 1:
                report(t, "doubled-third", "doubled third at point of repose")

    # --- parallel perfect intervals between consecutive verticals ---
    for (t1, s1), (t2, s2) in zip(verticals, verticals[1:]):
        names = list(VoiceName)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                if s1[a].end < t2 or s1[b].end < t2:
                    continue  # a rest broke the linear connection
                p1a, p1b = s1[a].midi, s1[b].midi
                p2a, p2b = s2[a].midi, s2[b].midi
                if p1a == p2a or p1b == p2b:
                    continue  # a voice held or repeated: not parallel motion
                if (p2a > p1a) != (p2b > p1b):
                    continue  # contrary motion is not parallel
                iv1, iv2 = abs(p1a - p1b), abs(p2a - p2b)
                if iv1 % 12 == 0 and iv2 % 12 == 0:
                    report(t2, "parallels", f"parallel octaves/unisons {a.value}-{b.value}")
                elif VoiceName.bass in (a, b) and iv1 % 12 == 7 and iv2 % 12 == 7:
                    report(t2, "parallels", f"parallel fifths {a.value}-{b.value}")

    # --- chordal 7ths resolve down by step at chord changes ---
    for c1, c2 in zip(score.chords, score.chords[1:]):
        if c1.end != c2.onset:
            continue  # phrase gap
        if (c1.root_pc, c1.quality) == (c2.root_pc, c2.quality):
            continue
        seventh = _seventh_pc(c1)
        if seventh is None or c1.quality == "dim7":
            continue  # symmetric dim7: the labeled 7th is notational, not functional
        next_pcs = chord_pcs(c2.root_pc, c2.quality)
        lead_after = _sounding(score.voices[VoiceName.lead], c2.onset)
        targets = {(seventh - 1) % 12, (seventh - 2) % 12} & next_pcs
        lead_took = lead_after is not None and lead_after.midi % 12 in targets
        for voice in _TRIO:  # the lead's melody is sacrosanct, exempt
            before = _sounding(score.voices[voice], c1.end - 1)
            after = _sounding(score.voices[voice], c2.onset)
            if before is None or after is None:
                continue
            if before.midi % 12 != seventh or 1 <= before.midi - after.midi <= 2:
                continue
            # transferred resolution: the lead sounds the resolution tone,
            # so the inner voice may take another chord tone instead
            if lead_took and after.midi % 12 in next_pcs:
                continue
            # common-tone hold: a predominant 7th may hold its 7th when it
            # is a tone of the next chord (e.g. ii-half-dim-7 -> i in minor)
            if (
                after.midi == before.midi
                and c1.quality in ("min7", "halfdim7")
                and seventh in next_pcs
            ):
                continue
            report(
                c2.onset,
                "seventh-resolution",
                f"{voice.value} leaves the 7th of {c1.quality} by "
                f"{after.midi - before.midi:+d} (must resolve down by step)",
            )

    # --- final chord: root-position major triad (minor keys may end minor) ---
    if verticals and score.chords:
        t, sounding = verticals[-1]
        final = score.chords[-1]
        final_ok = ("maj",) if score.key.mode == "major" else ("maj", "min")
        if final.quality not in final_ok:
            report(t, "final-chord", f"final chord is {final.quality}, not a stable triad")
        if sounding[VoiceName.bass].midi % 12 != final.root_pc:
            report(t, "final-chord", "final chord not in root position")

    # --- the chart must still be the song: progression adherence floor ---
    if input_chords is not None and input_key is not None:
        adherence = progression_adherence(score, input_chords, input_key, threshold)
        if adherence is not None and adherence < ADHERENCE_FLOOR:
            report(
                0,
                "progression",
                f"chart realizes only {adherence:.0%} of the input progression "
                f"(floor {ADHERENCE_FLOOR:.0%})",
            )

    return out


def _tonic_pc(key: KeySig) -> int:
    pc = (key.fifths * 7) % 12
    if key.mode == "minor":
        pc = (pc + 9) % 12  # fifths name the relative major
    return pc


def _adheres(
    readings: list,
    inp: ChordSpan,
    prev: ChordSpan | None,
    nxt: ChordSpan | None,
    tick: int,
    beat: int,
) -> bool:
    """Does any polyphonic reading of the sonority serve the input chord —
    directly, or via the standard theory exceptions?"""
    for r in readings:
        if r.root_pc == inp.root_pc:
            return True  # the chord itself, or a same-root recoloring (C -> C7)
        if r.quality in _DOM_FAMILY and nxt is not None and r.root_pc == (nxt.root_pc + 7) % 12:
            return True  # secondary dominant leading into the next chord
        if r.quality == "dim7":
            return True  # passing diminished (symmetric; its root is notation)
        if prev is not None and tick - inp.onset < beat and r.root_pc == prev.root_pc:
            return True  # suspension held across the change
        if nxt is not None and inp.end - tick <= beat and r.root_pc == nxt.root_pc:
            return True  # anticipation of the coming change
    return False


def progression_adherence(
    score: Score,
    input_chords: list[ChordSpan],
    input_key: KeySig,
    threshold: int | None = None,
) -> float | None:
    """Fraction of structural verticals whose sung pitches realize the input
    progression (transposed into the score's key). Lead-filigree verticals
    and material beyond the input (the tag) are out of the denominator.
    None = nothing measurable."""
    if not input_chords:
        return None
    if threshold is None:
        threshold = score.time.ticks_per_beat
    shift = (_tonic_pc(score.key) - _tonic_pc(input_key)) % 12
    spans = sorted(
        (c.model_copy(update={"root_pc": (c.root_pc + shift) % 12}) for c in input_chords),
        key=lambda c: c.onset,
    )
    total = adhering = 0
    for t in _event_ticks(score):
        sounding = {v: n for v in VoiceName if (n := _sounding(score.voices[v], t))}
        if len(sounding) < 4:
            continue
        if sounding[VoiceName.lead].duration < threshold:
            continue  # passing tones / filigree: the NCT exemption
        inp = _chord_at(spans, t)
        if inp is None:
            continue  # tag or other invented material beyond the input
        if sounding[VoiceName.lead].midi % 12 not in chord_pcs(inp.root_pc, inp.quality):
            continue  # the sacrosanct melody forces a substitution here:
            # realizing the input chord is impossible by construction
        i = spans.index(inp)
        prev = spans[i - 1] if i > 0 else None
        nxt = spans[i + 1] if i + 1 < len(spans) else None
        total += 1
        pcs = tuple(sounding[v].midi % 12 for v in VoiceName)
        if _adheres(classify(pcs), inp, prev, nxt, t, threshold):
            adhering += 1
    return adhering / total if total else None


ADHERENCE_FLOOR = 0.5


def metrics(
    score: Score,
    *,
    input_chords: list[ChordSpan] | None = None,
    input_key: KeySig | None = None,
) -> dict:
    """Quality metrics: not violations, but the bands a good chart hits."""
    spans = score.chords
    dom = sum(1 for c in spans if c.quality in _DOM_FAMILY)

    bass_ok = bass_total = 0
    for n in score.voices[VoiceName.bass]:
        chord = _chord_at(spans, n.onset)
        if chord is None:
            continue
        bass_total += 1
        if chord_degree(chord.root_pc, chord.quality, n.midi % 12) in ("root", "fifth"):
            bass_ok += 1

    ring = False
    if spans:
        final = spans[-1]
        last_lead = score.voices[VoiceName.lead][-1]
        last_bass = score.voices[VoiceName.bass][-1]
        last_tenor = score.voices[VoiceName.tenor][-1]
        ring = (
            final.quality == "maj"
            and last_bass.midi % 12 == final.root_pc
            and chord_degree(final.root_pc, final.quality, last_lead.midi % 12) in ("root", "fifth")
            and last_tenor.midi - last_lead.midi <= 9
        )

    out = {
        "dom7_family_share": dom / len(spans) if spans else 0.0,
        "bass_root_fifth_share": bass_ok / bass_total if bass_total else 0.0,
        "final_chord_ring": ring,
    }
    if input_chords is not None and input_key is not None:
        adherence = progression_adherence(score, input_chords, input_key)
        out["input_adherence"] = round(adherence, 3) if adherence is not None else None
    return out
