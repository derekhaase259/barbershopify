"""Top-level arranger pipeline: ArrangeInput -> four-part Score.

transpose -> segment into slots -> harmonize (chord per slot) ->
voice (tenor/bari/bass per slot) -> assemble Score. The lead carries the
melody verbatim (transposed); the trio re-attacks on structural melody
notes, holds through filigree, and merges common tones across chord
changes under a held lead.
"""
from __future__ import annotations

from dataclasses import replace

from pydantic import BaseModel, Field

from barbershop.score import ChordSpan, KeySig, Note, Score, TimeSig, VoiceName
from barbershop.texture import segment
from barbershop.arranger.config import RANGES, ArrangerConfig
from barbershop.arranger.harmonize import harmonize
from barbershop.arranger.transpose import choose_transposition
from barbershop.arranger.voicing import voice_slots


class ArrangeInput(BaseModel):
    title: str
    key: KeySig
    time: TimeSig
    tempo: float = Field(gt=0)
    melody: list[Note]
    chords: list[ChordSpan]


def _extend_for_tag(melody: list[Note], chords: list[ChordSpan], time: TimeSig) -> None:
    """The tag: the lead posts on its final note for two extra measures
    while the trio walks beneath it (the walk itself emerges from the
    harmonizer over the swipe slots created in _add_swipes)."""
    tpm = time.ticks_per_measure
    final = melody[-1]
    final.duration += 2 * tpm
    if final.lyric is not None:
        final.lyric.extend = True
    last_chord = chords[-1]
    if last_chord.end < final.end:
        last_chord.duration = final.end - last_chord.onset


def _swipe_min_duration(cfg: ArrangerConfig) -> int | None:
    """Sustained-note threshold for swipes, by spice (None = no swipes)."""
    return {1: None, 2: 1920, 3: 1440, 4: 960, 5: 960}[cfg.spice]


def _add_swipes(slots: list, time: TimeSig, cfg: ArrangerConfig) -> list:
    """Split sustained slots so harmony can move while the lead holds."""
    min_dur = _swipe_min_duration(cfg)
    tpm = time.ticks_per_measure
    out = []
    for i, slot in enumerate(slots):
        is_final = i == len(slots) - 1
        if is_final and cfg.spice >= 3 and slot.duration >= 2 * tpm + 960:
            # the tag post: walk in half-measure steps, settle on a stable
            # final measure
            walk_end = slot.end - tpm
            t = slot.onset
            while t < walk_end:
                step = min(tpm // 2, walk_end - t)
                out.append(
                    replace(slot, onset=t, duration=step, melody_attack=(t == slot.onset),
                            swipe=True, phrase_end=False)
                )
                t += step
            out.append(replace(slot, onset=walk_end, duration=slot.end - walk_end,
                               melody_attack=False, phrase_end=True))
            continue
        if (
            not is_final
            and min_dur is not None
            and slot.structural
            and slot.melody_attack
            and slot.duration >= min_dur
        ):
            split = slot.duration - 480  # the swipe lands on the last beat
            out.append(replace(slot, duration=split))
            out.append(
                replace(slot, onset=slot.onset + split, duration=slot.duration - split,
                        melody_attack=False, swipe=True)
            )
            continue
        out.append(slot)
    return out


def arrange(inp: ArrangeInput, cfg: ArrangerConfig) -> Score:
    if not inp.melody:
        raise ValueError("cannot arrange an empty melody")

    shift, fifths = choose_transposition([n.midi for n in inp.melody], inp.key.fifths)
    key = KeySig(fifths=fifths, mode=inp.key.mode)
    melody = [n.model_copy(update={"midi": n.midi + shift}) for n in inp.melody]
    # extraction outliers that no global transposition can reach are
    # folded into range by octaves (artifact correction — see DESIGN.md;
    # pitch classes, hence harmony, are preserved)
    lead_lo, lead_hi = RANGES["lead"]
    for n in melody:
        while n.midi > lead_hi:
            n.midi -= 12
        while n.midi < lead_lo:
            n.midi += 12
    chords = [
        c.model_copy(update={"root_pc": (c.root_pc + shift) % 12}) for c in inp.chords
    ]

    if cfg.spice >= 3:
        _extend_for_tag(melody, chords, inp.time)

    threshold = int(cfg.structural_beats * inp.time.ticks_per_beat)
    slots = segment(melody, chords, threshold)
    slots = _add_swipes(slots, inp.time, cfg)
    chosen = harmonize(slots, key, cfg)
    slots = [replace(slot, chord=chord) for slot, chord in zip(slots, chosen)]
    bari_targets = None
    if cfg.duet:
        from barbershop.arranger.countermelody import compose_countermelody

        bari_targets = compose_countermelody(slots, cfg)
    voicings = voice_slots(slots, key, cfg, bari_targets)

    voices: dict[VoiceName, list[Note]] = {VoiceName.lead: melody}
    for name in (VoiceName.tenor, VoiceName.bari, VoiceName.bass):
        notes: list[Note] = []
        for slot, v in zip(slots, voicings):
            pitch = v.pitch(name.value)
            held = (
                notes
                and not slot.melody_attack
                and notes[-1].end == slot.onset
                and notes[-1].midi == pitch
            )
            if held:
                notes[-1].duration += slot.duration
            else:
                notes.append(Note(onset=slot.onset, duration=slot.duration, midi=pitch))
        voices[name] = notes

    merged: list[ChordSpan] = []
    for c in chosen:
        if (
            merged
            and merged[-1].end == c.onset
            and merged[-1].root_pc == c.root_pc
            and merged[-1].quality == c.quality
        ):
            merged[-1].duration += c.duration
        else:
            merged.append(c.model_copy())

    return Score(
        title=inp.title,
        key=key,
        time=inp.time,
        tempo=inp.tempo,
        voices=voices,
        chords=merged,
    )
