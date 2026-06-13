"""Composer for the duet baritone counter-line."""
from barbershop.score import ChordSpan
from barbershop.texture import Slot
from barbershop.arranger.config import RANGES, ArrangerConfig
from barbershop.arranger.countermelody import compose_countermelody
from barbershop.vocabulary import chord_pcs

Q = 480


def _slot(onset, lead, root_pc, quality, *, swipe=False, phrase_end=False, structural=True):
    return Slot(
        onset=onset, duration=Q, melody_midi=lead, melody_max_midi=lead,
        melody_min_midi=lead, melody_last_midi=lead, melody_attack=not swipe,
        chord=ChordSpan(onset=onset, duration=Q, root_pc=root_pc, quality=quality),
        structural=structural, phrase_end=phrase_end, swipe=swipe,
    )


def test_targets_only_on_apt_slots():
    # a plain structural slot (no swipe, not a phrase end) gets no counter-line
    slots = [_slot(0, 64, 0, "maj")]
    assert compose_countermelody(slots, ArrangerConfig(duet=True)) == [None]


def test_target_is_a_chord_tone_below_the_lead_in_bari_range():
    slots = [_slot(0, 67, 0, "maj", swipe=True)]  # G4 lead over C major
    (t,) = compose_countermelody(slots, ArrangerConfig(duet=True))
    lo, hi = RANGES["bari"]
    assert t is not None
    assert t % 12 in chord_pcs(0, "maj")
    assert lo <= t <= hi
    assert t < 67


def test_contrary_motion_against_a_rising_lead():
    # lead climbs C5..E5 over swipe slots; the bari line should not climb with it
    leads = [60, 64, 67]
    slots = [_slot(i * Q, m, 0, "maj", swipe=True) for i, m in enumerate(leads)]
    targets = compose_countermelody(slots, ArrangerConfig(duet=True))
    assert all(t is not None for t in targets)
    assert targets[-1] <= targets[0]  # bari trends down (or holds) as the lead rises
