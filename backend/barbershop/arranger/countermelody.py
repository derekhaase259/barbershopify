"""Compose an independent baritone counter-melody for duet mode.

Where the lead sustains (a swipe slot) or cadences (a phrase end), the
baritone leaves its harmony role and sings its own line: a chord tone
below the lead, moving in contrary motion and stepwise where it can. The
voicing engine pins the bari to these targets and solves the other voices
around them, falling back to a free bari when a target cannot be voiced.
"""
from __future__ import annotations

from barbershop.texture import Slot
from barbershop.vocabulary import chord_pcs
from barbershop.arranger.config import RANGES, ArrangerConfig


def _apt(slot: Slot) -> bool:
    """A held lead (swipe) or a cadence is where a counter-line belongs."""
    return slot.swipe or slot.phrase_end


def _cost(p: int, lead: int, prev: int | None, lead_dir: int) -> float:
    gap = lead - p
    cost = 0.0
    if gap < 3:            # sit a third to a sixth under the lead
        cost += (3 - gap) * 2.0
    elif gap > 9:
        cost += (gap - 9) * 1.0
    if prev is not None:
        bari_dir = p - prev
        # contrary motion: penalize moving the same way the lead just moved
        if (lead_dir > 0 and bari_dir > 0) or (lead_dir < 0 and bari_dir < 0):
            cost += 4.0
        # under a held lead, reward the bari for actually moving (the line)
        if lead_dir == 0 and bari_dir == 0:
            cost += 2.0
        cost += 0.3 * abs(bari_dir)  # mild smoothness
    return cost


def compose_countermelody(slots: list[Slot], cfg: ArrangerConfig) -> list[int | None]:
    """One optional baritone target MIDI per slot (None = voicer chooses)."""
    targets: list[int | None] = [None] * len(slots)
    if not cfg.duet:
        return targets
    lo, hi = RANGES["bari"]
    prev: int | None = None
    prev_lead: int | None = None
    for i, slot in enumerate(slots):
        lead = slot.melody_midi
        if not _apt(slot):
            prev_lead = lead
            continue
        pcs = chord_pcs(slot.chord.root_pc, slot.chord.quality)
        cands = [p for p in range(lo, hi + 1) if p % 12 in pcs and p < lead]
        if not cands:
            prev_lead = lead
            continue
        lead_dir = 0 if prev_lead is None else lead - prev_lead
        # tie-break toward the higher tone (closer under the lead)
        target = min(cands, key=lambda p: (_cost(p, lead, prev, lead_dir), -p))
        targets[i] = target
        prev, prev_lead = target, lead
    return targets
