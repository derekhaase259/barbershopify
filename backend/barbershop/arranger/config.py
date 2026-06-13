"""Arranger configuration: voice ranges, cost weights, spice presets.

All soft-constraint weights live here so the optimizer's taste is tunable
in one place. The user-facing spice dial (1-5) selects scaled presets in
the harmonization stage; voicing weights are largely style-invariant.
"""
from __future__ import annotations

from dataclasses import dataclass

# Sounding-pitch ranges (MIDI), per SPEC: tenor G3-C5, lead A2-G4,
# bari A2-G4, bass E2-C4.
RANGES: dict[str, tuple[int, int]] = {
    "tenor": (55, 72),
    "lead": (45, 67),
    "bari": (45, 67),
    "bass": (40, 60),
}

# Comfortable tessitura centers used for range-comfort scoring.
COMFORT: dict[str, tuple[int, int]] = {
    "tenor": (59, 70),
    "bari": (50, 62),
    "bass": (43, 55),
}

HARD = 10_000.0  # effectively-forbidden transition/static cost


@dataclass
class ArrangerConfig:
    spice: int = 3
    duet: bool = False  # compose an independent baritone counter-melody

    # --- voicing: static weights ---
    w_ring: float = 4.0  # ring potential on sustained/cadential chords
    w_cone: float = 1.5  # cone-shaped spacing
    w_range: float = 1.0  # tessitura comfort
    w_cadence_bass_root: float = 6.0  # bass should take root at phrase ends
    w_doubled_fifth: float = 1.0  # prefer doubled root on triads
    w_incomplete_chord: float = 6.0  # a dropped fifth rings less; forced-only fallback
    w_bari_target: float = 100.0  # duet: pull toward the counter-line, but yield to HARD rules

    # --- voicing: transition weights ---
    w_motion_tenor: float = 1.2
    w_motion_bari: float = 1.0
    w_motion_bass: float = 0.5
    w_leap: float = 0.8  # extra per semitone beyond a 4th in tenor/bari
    w_awkward_interval: float = 3.0  # tritones, sevenths within a part
    w_leading_tone: float = 1.0
    w_common_tone: float = 0.6  # bonus (subtracted) per held common tone
    # frustrated resolution: lead takes the 7th's resolution tone, inner
    # voice moves to another chord tone instead (transferred resolution)
    w_frustrated_seventh: float = 1.5

    # --- texture ---
    structural_beats: float = 1.0  # structural-length threshold, in beats

    # cap on voicing candidates per slot (keeps Viterbi fast)
    max_candidates: int = 48

    # --- harmonization weights (several scale with spice) ---
    w_tier2: float = 0.5  # static penalty for tier-2 color
    w_overlap: float = 0.8  # per pitch class not shared with the input chord
    w_dom_bias: float = -0.5  # static bonus for dominant-7th-family chords
    w_circle: float = 1.5  # transition reward for descending-fifth root motion
    w_dom_resolve: float = 1.5  # extra reward when a dominant resolves down a fifth
    w_dom_hang: float = 2.5  # penalty when a dominant doesn't resolve
    max_chord_candidates: int = 24

    @property
    def w_substitution(self) -> float:
        """Cost of leaving the input chord's root; falls as spice rises."""
        return {1: 8.0, 2: 5.0, 3: 3.0, 4: 2.0, 5: 1.2}[self.spice]

    @property
    def w_same_root_upgrade(self) -> float:
        """Cost of recoloring on the same root (e.g. V -> V7)."""
        return {1: 1.2, 2: 1.0, 3: 0.8, 4: 0.5, 5: 0.3}[self.spice]

    @property
    def w_tier3(self) -> float:
        """Tier-3 spice chords are gated hard at low spice."""
        return {1: 50.0, 2: 50.0, 3: 6.0, 4: 3.0, 5: 1.5}[self.spice]
