"""Voicing engine: assign tenor/bari/bass pitches to each harmonic slot.

Candidate voicings are enumerated per slot (chord-tone coverage, voice
order, ranges = hard), scored statically (ring, cone spacing, comfort),
then stitched by Viterbi with voice-leading transition costs (parallel
octaves anywhere and bass-involved parallel fifths effectively forbidden,
chordal 7ths must resolve down by step at chord changes).
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import permutations

from barbershop.score import KeySig
from barbershop.texture import Slot
from barbershop.vocabulary import CHORDS, chord_pcs
from barbershop.arranger.config import COMFORT, HARD, RANGES, ArrangerConfig


@dataclass(frozen=True)
class Voicing:
    tenor: int
    bari: int
    bass: int

    def pitch(self, name: str) -> int:
        return getattr(self, name)


_TRIO_NAMES = ("tenor", "bari", "bass")


def _fifth_interval(quality: str) -> int:
    for interval, name in CHORDS[quality].degrees.items():
        if name == "fifth":
            return interval
    raise ValueError(f"{quality} has no fifth")


def _essential_intervals(quality: str) -> tuple[int, ...]:
    """Three chord tones the trio covers when the lead is on an NCT."""
    cdef = CHORDS[quality]
    by_name = {name: iv for iv, name in cdef.degrees.items()}
    if "seventh" in by_name:
        return (0, by_name["third"], by_name["seventh"])
    if "sixth" in by_name:
        return (0, by_name["third"], by_name["sixth"])
    return (0, by_name["third"], by_name["fifth"])


def _realization_options(slot: Slot) -> list[tuple[int, ...]]:
    """Interval-triples (bass, bari, tenor candidates pool) for the trio."""
    chord = slot.chord
    cdef = CHORDS[chord.quality]
    options: list[tuple[int, ...]] = []
    if slot.structural:
        lead_iv = (slot.melody_midi - chord.root_pc) % 12
        for realization, doubled in cdef.realizations.items():
            # never generate doubled thirds: forbidden at repose, weak elsewhere
            if doubled is not None and cdef.degrees.get(doubled) == "third":
                continue
            rest = list(realization)
            if lead_iv not in rest:
                continue
            rest.remove(lead_iv)
            options.append(tuple(rest))
    else:
        options.append(_essential_intervals(chord.quality))
    return options


def _pitches_for(pc: int, lo: int, hi: int) -> list[int]:
    return [p for p in range(lo, hi + 1) if p % 12 == pc]


def candidates(slot: Slot, cfg: ArrangerConfig, *, is_final: bool) -> list[tuple[Voicing, float]]:
    """All legal voicings for a slot with their static costs, best first."""
    chord = slot.chord
    fifth_iv = _fifth_interval(chord.quality)
    out: list[tuple[Voicing, float]] = []
    seen: set[Voicing] = set()
    for trio_ivs in _realization_options(slot):
        for bass_iv, bari_iv, tenor_iv in set(permutations(trio_ivs)):
            if bass_iv not in (0, fifth_iv):
                continue  # bass sings root or fifth, never 3rd/7th (v1)
            if is_final and bass_iv != 0:
                continue  # final chord must be root position
            bass_pc = (chord.root_pc + bass_iv) % 12
            bari_pc = (chord.root_pc + bari_iv) % 12
            tenor_pc = (chord.root_pc + tenor_iv) % 12
            for bass in _pitches_for(bass_pc, *RANGES["bass"]):
                if bass > slot.melody_min_midi:
                    continue  # the bass stays under the lead, filigree included
                for tenor in _pitches_for(tenor_pc, *RANGES["tenor"]):
                    if tenor < slot.melody_max_midi:
                        continue  # tenor stays above the lead, filigree included
                    if tenor - slot.melody_midi > 24:
                        continue  # truly hopeless spacing (cone costs
                        # handle the merely-wide; a low lead plus the
                        # tenor's range floor can legitimately force >14)
                    for bari in _pitches_for(bari_pc, *RANGES["bari"]):
                        if not (bass <= bari <= tenor):
                            continue
                        v = Voicing(tenor=tenor, bari=bari, bass=bass)
                        if v in seen:
                            continue
                        seen.add(v)
                        out.append((v, _static_cost(slot, v, bass_iv, cfg)))
    out.sort(key=lambda item: item[1])
    return out[: cfg.max_candidates]


def _static_cost(slot: Slot, v: Voicing, bass_iv: int, cfg: ArrangerConfig) -> float:
    chord = slot.chord
    lead = slot.melody_midi
    cost = 0.0

    # --- ring potential: matters most on sustained / cadential chords ---
    weight = cfg.w_ring if (slot.phrase_end or slot.duration >= 960) else cfg.w_ring * 0.3
    lead_degree = CHORDS[chord.quality].degrees.get((lead - chord.root_pc) % 12)
    ring = 0.0
    if lead_degree not in ("root", "fifth"):
        ring += 0.4
    if bass_iv != 0:
        ring += 0.4  # not root position
    if not (3 <= v.tenor - lead <= 9):
        ring += 0.2
    cost += weight * ring

    # --- cone-shaped spacing ---
    tl = v.tenor - lead
    bb = v.bari - v.bass
    if tl > 9:
        cost += cfg.w_cone * (tl - 9)
    if tl > 12:
        cost += cfg.w_cone * 3 * (tl - 12)  # >octave tenor-lead is a defect
    if bb > 12:
        cost += cfg.w_cone * (bb - 12)
    if bb < 4 and v.bari <= 52:
        cost += cfg.w_cone * (4 - bb)  # muddy low cluster

    # --- range comfort ---
    for name in _TRIO_NAMES:
        lo, hi = COMFORT[name]
        p = v.pitch(name)
        cost += cfg.w_range * max(0, lo - p, p - hi) * 0.5
    if slot.duration >= 960 and v.tenor < 58:
        cost += cfg.w_range * 2.0  # tenor is light: no sustained low notes

    # --- cadential bass root ---
    if slot.phrase_end and bass_iv != 0:
        cost += cfg.w_cadence_bass_root

    # --- prefer doubled root over doubled fifth on triads ---
    pcs = sorted(((v.tenor - chord.root_pc) % 12, (lead - chord.root_pc) % 12,
                  (v.bari - chord.root_pc) % 12, (v.bass - chord.root_pc) % 12))
    if slot.structural and pcs.count(_fifth_interval(chord.quality)) > 1:
        cost += cfg.w_doubled_fifth

    # --- completeness: a structural chord wants all its tones to ring ---
    # (non-structural slots cover only the essential trio by design)
    if slot.structural:
        chord_ivs = {iv % 12 for iv in CHORDS[chord.quality].intervals}
        if chord_ivs - set(pcs):  # a chord tone is missing (e.g. dropped fifth)
            cost += cfg.w_incomplete_chord

    return cost


def _movement_cost(name: str, delta: int, cfg: ArrangerConfig) -> float:
    d = abs(delta)
    if d == 0:
        return -cfg.w_common_tone  # reward held common tones
    if name == "bass":
        # root motion by 4th/5th/octave is the bass's native language
        if d in (5, 7, 12):
            return cfg.w_motion_bass * 1.5
        return cfg.w_motion_bass * d
    w = cfg.w_motion_tenor if name == "tenor" else cfg.w_motion_bari
    cost = w * d
    if d > 5:
        cost += cfg.w_leap * (d - 5)
    if d in (6, 10, 11):  # tritone / seventh leaps are unsingable inner lines
        cost += cfg.w_awkward_interval
    return cost


def transition_cost(
    prev_slot: Slot,
    prev: Voicing,
    slot: Slot,
    cur: Voicing,
    cfg: ArrangerConfig,
) -> float:
    cost = 0.0
    contiguous = prev_slot.end >= slot.onset  # no rest between
    chord_changed = (
        prev_slot.chord.root_pc != slot.chord.root_pc
        or prev_slot.chord.quality != slot.chord.quality
    )

    # the lead's pitch just before the boundary is the LAST note it sang
    # during the previous slot (filigree included), not the slot's start
    before = {"tenor": prev.tenor, "lead": prev_slot.melody_last_midi, "bari": prev.bari, "bass": prev.bass}
    after = {"tenor": cur.tenor, "lead": slot.melody_midi, "bari": cur.bari, "bass": cur.bass}

    for name in _TRIO_NAMES:
        cost += _movement_cost(name, after[name] - before[name], cfg)

    if not contiguous:
        return cost  # across a rest, only smoothness matters

    # --- forbidden parallels (same-direction motion only) ---
    names = list(before)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            da, db = after[a] - before[a], after[b] - before[b]
            if da == 0 or db == 0 or (da > 0) != (db > 0):
                continue  # oblique or contrary motion is not parallel
            iv1, iv2 = abs(before[a] - before[b]), abs(after[a] - after[b])
            if iv1 % 12 == 0 and iv2 % 12 == 0:
                cost += HARD  # parallel octaves/unisons, any pair
            elif "bass" in (a, b) and iv1 % 12 == 7 and iv2 % 12 == 7:
                cost += HARD  # parallel fifths against the bass

    # --- tendency tones at chord changes ---
    if chord_changed:
        prev_chord = prev_slot.chord
        seventh_pc = next(
            ((prev_chord.root_pc + iv) % 12
             for iv, deg in CHORDS[prev_chord.quality].degrees.items() if deg == "seventh"),
            None,
        )
        third_pc = (prev_chord.root_pc + next(
            iv for iv, deg in CHORDS[prev_chord.quality].degrees.items() if deg == "third"
        )) % 12
        next_pcs = chord_pcs(slot.chord.root_pc, slot.chord.quality)
        for name in _TRIO_NAMES:
            delta = after[name] - before[name]
            # dim7 is fully symmetric: its labeled "7th" is a notational
            # artifact, and its tones move by step or hold (movement costs
            # already prefer that). The resolution rule applies to chords
            # with a functional 7th.
            if (
                prev_chord.quality != "dim7"
                and seventh_pc is not None
                and before[name] % 12 == seventh_pc
            ):
                if not (-2 <= delta <= -1):
                    # chordal 7ths resolve down by step — except transferred
                    # resolution (the lead takes the resolution tone) and the
                    # common-tone hold: a predominant 7th (min7/halfdim7)
                    # whose 7th is a tone of the next chord may simply hold.
                    targets = {(seventh_pc - 1) % 12, (seventh_pc - 2) % 12} & next_pcs
                    lead_took = slot.melody_midi % 12 in targets
                    common_hold = (
                        delta == 0
                        and prev_chord.quality in ("min7", "halfdim7")
                        and seventh_pc in next_pcs
                    )
                    if common_hold:
                        pass
                    elif lead_took and after[name] % 12 in next_pcs:
                        cost += cfg.w_frustrated_seventh + (0.5 if delta > 0 else 0.0)
                    else:
                        cost += HARD
            if prev_chord.quality in ("dom7", "dom9", "aug7", "dom7b5") and before[name] % 12 == third_pc:
                # leading tone rises... or drops to the 5th (idiomatic inner-voice exception)
                if delta not in (1, -4, 0):
                    cost += cfg.w_leading_tone

    return cost


def voice_slots(slots: list[Slot], key: KeySig, cfg: ArrangerConfig) -> list[Voicing]:
    """Viterbi over per-slot voicing candidates."""
    if not slots:
        return []
    columns: list[list[tuple[Voicing, float]]] = []
    for i, slot in enumerate(slots):
        col = candidates(slot, cfg, is_final=(i == len(slots) - 1))
        if not col:
            raise ValueError(
                f"no legal voicing for slot at tick {slot.onset} "
                f"(chord root={slot.chord.root_pc} {slot.chord.quality}, melody={slot.melody_midi})"
            )
        columns.append(col)

    # cost[i][j] = best path cost ending at candidate j of slot i
    best = [c for _, c in columns[0]]
    back: list[list[int]] = [[-1] * len(columns[0])]
    for i in range(1, len(columns)):
        cur_costs: list[float] = []
        cur_back: list[int] = []
        for v, static in columns[i]:
            best_prev, best_cost = 0, float("inf")
            for j, (pv, _) in enumerate(columns[i - 1]):
                c = best[j] + transition_cost(slots[i - 1], pv, slots[i], v, cfg)
                if c < best_cost:
                    best_cost, best_prev = c, j
            cur_costs.append(best_cost + static)
            cur_back.append(best_prev)
        best = cur_costs
        back.append(cur_back)

    # backtrack
    idx = min(range(len(best)), key=lambda j: best[j])
    path = [idx]
    for i in range(len(columns) - 1, 0, -1):
        idx = back[i][idx]
        path.append(idx)
    path.reverse()
    return [columns[i][j][0] for i, j in enumerate(path)]
