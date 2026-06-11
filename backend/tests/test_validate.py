"""The legality validator: the chart-quality battery from SPEC.md.

Two duties: every arranged demo must come back clean at every spice
level, and the validator must actually catch planted violations (a
validator that can't fail is no validator).
"""
import pytest

from barbershop.arranger.arrange import arrange
from barbershop.arranger.config import ArrangerConfig
from barbershop.arranger.validate import metrics, validate
from barbershop.demos import DEMOS
from barbershop.score import ChordSpan, KeySig, Note, Score, TimeSig, VoiceName


@pytest.mark.parametrize("spice", [1, 2, 3, 4, 5])
@pytest.mark.parametrize("name", list(DEMOS))
def test_arranged_demos_are_clean_at_every_spice(name, spice):
    score = arrange(DEMOS[name], ArrangerConfig(spice=spice))
    violations = validate(score)
    assert violations == [], "\n".join(map(str, violations))


@pytest.mark.parametrize("name", list(DEMOS))
def test_chart_metrics_in_barbershop_bands(name):
    score = arrange(DEMOS[name], ArrangerConfig(spice=3))
    m = metrics(score)
    assert 0.30 <= m["dom7_family_share"] <= 0.60
    assert m["bass_root_fifth_share"] >= 0.90
    assert m["final_chord_ring"] is True


def test_spice_extremes_differ_but_both_stay_legal():
    demo = DEMOS["yankee-doodle"]
    mild = arrange(demo, ArrangerConfig(spice=1))
    wild = arrange(demo, ArrangerConfig(spice=5))
    assert validate(mild) == [] and validate(wild) == []
    assert mild.chords != wild.chords


def _tiny_score(tenor, lead, bari, bass, *, quality="maj", root=0):
    """One-chord score with explicit pitches for planting violations."""
    return Score(
        title="bad",
        key=KeySig(fifths=0, mode="major"),
        time=TimeSig(beats=4, beat_type=4),
        tempo=100,
        voices={
            VoiceName.tenor: [Note(onset=0, duration=1920, midi=tenor)],
            VoiceName.lead: [Note(onset=0, duration=1920, midi=lead)],
            VoiceName.bari: [Note(onset=0, duration=1920, midi=bari)],
            VoiceName.bass: [Note(onset=0, duration=1920, midi=bass)],
        },
        chords=[ChordSpan(onset=0, duration=1920, root_pc=root, quality=quality)],
    )


def test_catches_major_seventh_sonority():
    bad = _tiny_score(71, 64, 55, 48)  # C E G B
    kinds = {v.kind for v in validate(bad)}
    assert "vocabulary" in kinds


def test_catches_tenor_below_lead():
    bad = _tiny_score(60, 64, 55, 48)  # tenor C4 under lead E4
    kinds = {v.kind for v in validate(bad)}
    assert "crossing" in kinds


def test_catches_bass_on_chordal_seventh():
    bad = _tiny_score(67, 64, 60, 53, quality="dom7", root=7)  # bass F2+12=F3 = 7th of G7
    kinds = {v.kind for v in validate(bad)}
    assert "bass-seventh" in kinds


def test_catches_out_of_range():
    bad = _tiny_score(74, 64, 55, 48)  # tenor D5 above C5 ceiling
    kinds = {v.kind for v in validate(bad)}
    assert "range" in kinds


def test_catches_doubled_third_at_repose():
    bad = _tiny_score(64, 64, 55, 48)  # two E's on the final chord
    kinds = {v.kind for v in validate(bad)}
    assert "doubled-third" in kinds


def test_violation_reports_measure_and_beat():
    bad = _tiny_score(71, 64, 55, 48)
    v = validate(bad)[0]
    assert v.measure == 1 and v.beat == 1.0


def test_catches_parallel_octaves():
    score = Score(
        title="bad",
        key=KeySig(fifths=0, mode="major"),
        time=TimeSig(beats=4, beat_type=4),
        tempo=100,
        voices={
            VoiceName.tenor: [Note(onset=0, duration=960, midi=67), Note(onset=960, duration=960, midi=69)],
            VoiceName.lead: [Note(onset=0, duration=960, midi=64), Note(onset=960, duration=960, midi=65)],
            VoiceName.bari: [Note(onset=0, duration=960, midi=55), Note(onset=960, duration=960, midi=57)],
            VoiceName.bass: [Note(onset=0, duration=960, midi=48), Note(onset=960, duration=960, midi=53)],
        },
        chords=[
            ChordSpan(onset=0, duration=960, root_pc=0, quality="maj"),
            ChordSpan(onset=960, duration=960, root_pc=5, quality="maj"),
        ],
    )
    # bari A3(57) and tenor A4(69) move from G3/G4 in parallel octaves
    kinds = {v.kind for v in validate(score)}
    assert "parallels" in kinds


def test_catches_unresolved_seventh():
    score = Score(
        title="bad",
        key=KeySig(fifths=0, mode="major"),
        time=TimeSig(beats=4, beat_type=4),
        tempo=100,
        voices={
            VoiceName.tenor: [Note(onset=0, duration=960, midi=65), Note(onset=960, duration=960, midi=67)],
            VoiceName.lead: [Note(onset=0, duration=960, midi=59), Note(onset=960, duration=960, midi=60)],
            VoiceName.bari: [Note(onset=0, duration=960, midi=55), Note(onset=960, duration=960, midi=52)],
            VoiceName.bass: [Note(onset=0, duration=960, midi=43), Note(onset=960, duration=960, midi=48)],
        },
        chords=[
            ChordSpan(onset=0, duration=960, root_pc=7, quality="dom7"),
            ChordSpan(onset=960, duration=960, root_pc=0, quality="maj"),
        ],
    )
    # tenor has F (7th of G7) and leaps UP to G instead of resolving to E
    kinds = {v.kind for v in validate(score)}
    assert "seventh-resolution" in kinds


# --- progression adherence: the chart must still be the song ---

from barbershop.arranger.validate import progression_adherence

C_MAJOR = KeySig(fifths=0, mode="major")


def _vertical_score(verticals, *, key=C_MAJOR):
    """A score from explicit (tenor, lead, bari, bass) midi quadruples,
    one half-note vertical each."""
    voices = {v: [] for v in VoiceName}
    for i, (tenor, lead, bari, bass) in enumerate(verticals):
        for v, midi in zip(
            (VoiceName.tenor, VoiceName.lead, VoiceName.bari, VoiceName.bass),
            (tenor, lead, bari, bass),
        ):
            voices[v].append(Note(onset=i * 960, duration=960, midi=midi))
    return Score(
        title="t", key=key, time=TimeSig(beats=4, beat_type=4), tempo=100,
        voices=voices,
        chords=[ChordSpan(onset=0, duration=960 * len(verticals), root_pc=0, quality="maj")],
    )


def test_adherence_full_when_parts_realize_the_input():
    # C major then G7, sung exactly
    score = _vertical_score([
        (76, 72, 67, 48),  # E5 C5 G4 C3 -> C major
        (74, 71, 65, 43),  # D5 B4 F4 G2 -> G7
    ])
    inp = [
        ChordSpan(onset=0, duration=960, root_pc=0, quality="maj"),
        ChordSpan(onset=960, duration=960, root_pc=7, quality="dom7"),
    ]
    assert progression_adherence(score, inp, C_MAJOR) == 1.0


def test_adherence_allows_secondary_dominant_toward_next_chord():
    # during the C span the quartet sings D7 (V7 of the coming G7); the lead's
    # C is both the root of the input chord and the 7th of D7
    score = _vertical_score([
        (78, 72, 62, 45),  # F#5 C5 D4 A2 -> D7
        (74, 71, 65, 43),  # G7
    ])
    inp = [
        ChordSpan(onset=0, duration=960, root_pc=0, quality="maj"),
        ChordSpan(onset=960, duration=960, root_pc=7, quality="dom7"),
    ]
    assert progression_adherence(score, inp, C_MAJOR) == 1.0


def test_adherence_allows_passing_diminished():
    score = _vertical_score([
        (75, 72, 66, 45),  # Eb5 C5 F#4 A2 -> dim7 family
        (74, 71, 65, 43),  # G7
    ])
    inp = [
        ChordSpan(onset=0, duration=960, root_pc=0, quality="maj"),
        ChordSpan(onset=960, duration=960, root_pc=7, quality="dom7"),
    ]
    assert progression_adherence(score, inp, C_MAJOR) == 1.0


def test_adherence_skips_forced_substitutions():
    # lead sings F# over an input C chord: the input chord is unrealizable
    # under the sacrosanct melody, so the vertical leaves the denominator
    score = _vertical_score([
        (78, 66, 62, 50),  # F#5 F#4 D4 D3 -> D-rooted sonority, lead off-chord
        (74, 71, 65, 43),  # G7 sung over the G7 span
    ])
    inp = [
        ChordSpan(onset=0, duration=960, root_pc=0, quality="maj"),
        ChordSpan(onset=960, duration=960, root_pc=7, quality="dom7"),
    ]
    assert progression_adherence(score, inp, C_MAJOR) == 1.0


def test_wandering_chart_trips_the_progression_floor():
    # Ab7 sung against a C-major input (not a dominant of anything coming)
    score = _vertical_score([
        (75, 72, 68, 44),  # Eb5 C5 Ab4 Gb2 -> Ab7, lead C is a chord tone of C
    ])
    inp = [ChordSpan(onset=0, duration=960, root_pc=0, quality="maj")]
    assert progression_adherence(score, inp, C_MAJOR) == 0.0
    violations = validate(score, input_chords=inp, input_key=C_MAJOR)
    assert any(v.kind == "progression" for v in violations)


@pytest.mark.parametrize("spice", [1, 2, 3, 4, 5])
@pytest.mark.parametrize("name", list(DEMOS))
def test_arranged_demos_adhere_to_their_progressions(name, spice):
    inp = DEMOS[name]
    score = arrange(inp, ArrangerConfig(spice=spice))
    a = progression_adherence(score, inp.chords, inp.key)
    assert a is not None and a >= 0.6, f"adherence {a}"
    if spice <= 2:
        assert a == 1.0  # below the substitution spice, the chart IS the song
    violations = validate(score, input_chords=inp.chords, input_key=inp.key)
    assert not any(v.kind == "progression" for v in violations)


def test_metrics_report_input_adherence():
    inp = DEMOS["yankee-doodle"]
    score = arrange(inp, ArrangerConfig(spice=1))
    m = metrics(score, input_chords=inp.chords, input_key=inp.key)
    assert m["input_adherence"] == 1.0
    assert "input_adherence" not in metrics(score)  # absent without the input
