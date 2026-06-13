"""Voicing engine: per-slot four-part voicings via cost-optimized search."""
from barbershop.score import ChordSpan, KeySig
from barbershop.texture import Slot
from barbershop.vocabulary import chord_degree, classify
from barbershop.arranger.config import ArrangerConfig, RANGES
from barbershop.arranger.voicing import voice_slots

Q = 480
KEY_C = KeySig(fifths=0, mode="major")


def make_slot(onset, melody_midi, root_pc, quality, *, attack=True, structural=True,
              phrase_end=False, duration=Q, melody_max=None):
    return Slot(
        onset=onset,
        duration=duration,
        melody_midi=melody_midi,
        melody_max_midi=melody_max if melody_max is not None else melody_midi,
        melody_min_midi=melody_midi,
        melody_last_midi=melody_midi,
        melody_attack=attack,
        chord=ChordSpan(onset=onset, duration=duration, root_pc=root_pc, quality=quality),
        structural=structural,
        phrase_end=phrase_end,
    )


def pcs_of(voicing, lead_midi):
    return tuple(p % 12 for p in (voicing.tenor, lead_midi, voicing.bari, voicing.bass))


def test_major_triad_slot_is_legal_and_ordered():
    slots = [make_slot(0, 64, 0, "maj")]  # melody E4 over C major
    (v,) = voice_slots(slots, KEY_C, ArrangerConfig())
    assert classify(pcs_of(v, 64)) != []
    assert v.tenor >= 64 >= v.bass and v.bari <= v.tenor and v.bass <= v.bari
    assert chord_degree(0, "maj", v.bass % 12) in ("root", "fifth")
    lo, hi = RANGES["tenor"]
    assert lo <= v.tenor <= hi


def test_dom7_has_all_four_tones_and_seventh_in_inner_voice():
    slots = [make_slot(0, 59, 7, "dom7")]  # melody B3 (3rd of G7)
    (v,) = voice_slots(slots, KEY_C, ArrangerConfig())
    sounding = {v.tenor % 12, 59 % 12, v.bari % 12, v.bass % 12}
    assert sounding == {7, 11, 2, 5}  # complete, no doubling/omission
    assert chord_degree(7, "dom7", v.bass % 12) in ("root", "fifth")
    seventh_voices = [name for name, p in (("tenor", v.tenor), ("bari", v.bari)) if p % 12 == 5]
    assert seventh_voices  # 7th lands in tenor or bari, never bass


def test_chordal_seventh_resolves_down_by_step():
    slots = [
        make_slot(0, 59, 7, "dom7"),          # G7, melody on B
        make_slot(Q, 60, 0, "maj", phrase_end=True),  # C, melody on C
    ]
    v1, v2 = voice_slots(slots, KEY_C, ArrangerConfig())
    pairs = [("tenor", v1.tenor, v2.tenor), ("bari", v1.bari, v2.bari), ("bass", v1.bass, v2.bass)]
    for _, before, after in pairs:
        if before % 12 == 5:  # had the 7th (F)
            assert 1 <= before - after <= 2


def test_no_parallel_octaves_or_bass_fifths():
    slots = [
        make_slot(0, 60, 0, "maj"),
        make_slot(Q, 62, 2, "min"),  # root motion up a step: parallel-prone
    ]
    v1, v2 = voice_slots(slots, KEY_C, ArrangerConfig())
    before = {"tenor": v1.tenor, "lead": 60, "bari": v1.bari, "bass": v1.bass}
    after = {"tenor": v2.tenor, "lead": 62, "bari": v2.bari, "bass": v2.bass}
    names = list(before)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            iv1 = abs(before[a] - before[b])
            iv2 = abs(after[a] - after[b])
            moved = before[a] != after[a] and before[b] != after[b]
            if moved and iv1 % 12 == 0 and iv2 % 12 == 0:
                raise AssertionError(f"parallel octaves between {a} and {b}")
            if moved and "bass" in (a, b) and iv1 % 12 == 7 and iv2 % 12 == 7:
                raise AssertionError(f"parallel fifths between {a} and {b}")


def test_tenor_clears_filigree_peak():
    slots = [make_slot(0, 60, 0, "maj", melody_max=67, duration=2 * Q)]
    (v,) = voice_slots(slots, KEY_C, ArrangerConfig())
    assert v.tenor >= 67


def test_final_slot_bass_takes_root():
    slots = [
        make_slot(0, 62, 7, "dom7"),  # melody on D, the fifth of G7
        make_slot(Q, 64, 0, "maj", phrase_end=True),
    ]
    voicings = voice_slots(slots, KEY_C, ArrangerConfig())
    assert voicings[-1].bass % 12 == 0


def test_final_seventh_chord_with_lead_on_root_drops_fifth():
    # a song ending on a dom7 with the lead on the root: the final chord
    # must be root position (bass on root), but the lead already holds the
    # only root of a complete voicing. Theory's answer: omit the fifth and
    # double the root (root-root-third-seventh) — not a crash.
    slots = [make_slot(0, 55, 7, "dom7", phrase_end=True)]  # G7, lead on G3 (root)
    (v,) = voice_slots(slots, KEY_C, ArrangerConfig())
    assert v.bass % 12 == 7  # bass takes the root (final chord = root position)
    sounding = sorted({v.tenor % 12, 55 % 12, v.bari % 12, v.bass % 12})
    assert sounding == [5, 7, 11]  # root, third, seventh present; fifth (2) dropped
    assert classify(pcs_of(v, 55)) != []  # a legal vocabulary sonority


def test_structural_seventh_chord_stays_complete_when_it_fits():
    # the drop-fifth voicing is a forced fallback only: when a complete
    # voicing is available (lead not on the root), keep all four tones
    slots = [make_slot(0, 59, 7, "dom7")]  # G7, lead on B3 (the third)
    (v,) = voice_slots(slots, KEY_C, ArrangerConfig())
    sounding = {v.tenor % 12, 59 % 12, v.bari % 12, v.bass % 12}
    assert sounding == {7, 11, 2, 5}  # complete, fifth not dropped


def test_nonstructural_slot_trio_covers_essential_tones():
    # chord change lands mid-run: lead pitch (D) is an NCT over C major
    slots = [
        make_slot(0, 60, 0, "maj"),
        make_slot(Q, 62, 0, "maj", attack=False, structural=False),
    ]
    _, v2 = voice_slots(slots, KEY_C, ArrangerConfig())
    trio_pcs = {v2.tenor % 12, v2.bari % 12, v2.bass % 12}
    assert {0, 4}.issubset(trio_pcs)  # root and third always present
