"""Full analysis pipeline: audio file -> ArrangeInput (+ metadata).

Results are cached on disk keyed by content hash, so re-arranging at a
different spice never re-runs analysis.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from barbershop.analysis import beats as beats_mod
from barbershop.analysis import chords as chords_mod
from barbershop.analysis import key as key_mod
from barbershop.analysis import melody as melody_mod
from barbershop.analysis.decode import load_audio
from barbershop.arranger.arrange import ArrangeInput
from barbershop.lookup.identify import SongIdentity
from barbershop.score import TimeSig

CACHE_DIR = Path(__file__).resolve().parents[2] / "cache"

log = logging.getLogger(__name__)


def correct_tempo_level(
    grid: beats_mod.BeatGrid, labels: list[tuple[int, str]]
) -> beats_mod.BeatGrid:
    """Beat trackers love locking onto the eighth-note level of slow songs.
    Harmonic rhythm is the tell: this repertoire changes chords every 1-2
    measures, so a fast grid whose chords hold for 6+ "beats" is really a
    half-tempo song. (pyin segment durations are useless for this on old
    78s — voicing only catches vowel cores.) Verified against the bundled
    test recordings."""
    if grid.tempo <= 116 or len(labels) < 16:
        return grid
    import numpy as np

    runs, run = [], 1
    for a, b in zip(labels, labels[1:]):
        if a == b:
            run += 1
        else:
            runs.append(run)
            run = 1
    runs.append(run)
    if not runs or float(np.median(runs)) < 6:
        return grid
    return beats_mod.BeatGrid(
        tempo=grid.tempo / 2,
        beat_times=grid.beat_times[::2],
        downbeat_phase=0,
    )


@dataclass
class AnalysisResult:
    input: ArrangeInput
    tempo: float
    duration_seconds: float
    lyrics_source: str = "none"  # lrclib / asr / neutral / none
    lyrics_confidence: float = 0.0
    identity: SongIdentity | None = None
    chord_source: str = "audio"  # audio / tab
    chord_agreement: float | None = None
    tab_url: str | None = None


def _cache_key(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:32]


def analyze(
    path: str,
    *,
    title: str | None = None,
    use_cache: bool = True,
    lyrics: bool = True,
    lookup: bool = True,
) -> AnalysisResult:
    cache_file = CACHE_DIR / f"{_cache_key(path)}-v5.json"
    if use_cache and cache_file.exists():
        data = json.loads(cache_file.read_text())
        inp = ArrangeInput.model_validate(data["input"])
        identity = SongIdentity(**data["identity"]) if data.get("identity") else None
        if title and identity is None:
            inp.title = title  # filename titles never beat an identified title
        return AnalysisResult(
            input=inp,
            tempo=data["tempo"],
            duration_seconds=data["duration_seconds"],
            lyrics_source=data.get("lyrics_source", "none"),
            lyrics_confidence=data.get("lyrics_confidence", 0.0),
            identity=identity,
            chord_source=data.get("chord_source", "audio"),
            chord_agreement=data.get("chord_agreement"),
            tab_url=data.get("tab_url"),
        )

    y, sr = load_audio(path)
    grid = beats_mod.track(y, sr)

    # identify first: tab correction needs the identity before key
    # detection, and lyrics lookup wants it later anyway
    identity = None
    if lookup:
        try:
            from barbershop.lookup.identify import identify

            identity = identify(path, duration=len(y) / sr)
        except Exception:
            log.info("song lookup: identification crashed, continuing", exc_info=True)

    # chord labels first: their harmonic rhythm exposes a double-time
    # grid, and their changes vote for meter (3/4 vs 4/4) and downbeat
    # phase, so measures land on real harmonic arrivals
    labels = chords_mod.label_beats(y, sr, grid.beat_times)
    corrected = correct_tempo_level(grid, labels)
    if corrected is not grid:
        grid = corrected
        labels = chords_mod.label_beats(y, sr, grid.beat_times)
    meter, grid.downbeat_phase = chords_mod.best_meter_and_phase(labels)
    time = TimeSig(beats=meter, beat_type=4)

    segments = melody_mod.extract_segments(y, sr)
    melody = melody_mod.quantize(segments, grid)
    chord_spans = chords_mod.spans_from_labels(labels, grid)

    chord_source, chord_agreement, tab_url = "audio", None, None
    if identity is not None and chord_spans:
        try:
            from barbershop.lookup.align import apply_tab
            from barbershop.lookup.tabs import fetch_candidates

            for tab in fetch_candidates(identity):
                fixed = apply_tab(chord_spans, tab)
                if fixed is not None:
                    chord_spans = fixed.spans
                    chord_source = "tab"
                    chord_agreement = fixed.agreement
                    tab_url = fixed.url
                    break
        except Exception:
            log.info("song lookup: tab correction crashed, continuing", exc_info=True)

    detected_key = key_mod.key_from_chords(chord_spans)
    if detected_key is None:
        import librosa

        chroma_mean = librosa.feature.chroma_cqt(y=y, sr=sr).mean(axis=1)
        detected_key = key_mod.detect(np.asarray(chroma_mean))

    if not melody:
        raise ValueError("no melody could be extracted from this audio")
    if not chord_spans:
        raise ValueError("no chords could be estimated from this audio")

    lyrics_source, lyrics_confidence = "none", 0.0
    if lyrics:
        looked_up = None
        if identity is not None:
            try:
                from barbershop.lookup.lyrics import fetch_lyrics

                looked_up = fetch_lyrics(identity, duration=len(y) / sr)
            except Exception:
                log.info("song lookup: lyrics fetch crashed, continuing", exc_info=True)
        if looked_up is not None and looked_up.synced:
            from barbershop.textset.align import set_timed_lines

            lines = [(int(round(grid.time_to_tick(t))), txt) for t, txt in looked_up.synced]
            melody, _ = set_timed_lines(melody, lines, time)
            lyrics_source = "lrclib"
        elif looked_up is not None and looked_up.plain:
            from barbershop.textset.align import set_lyrics

            melody, _ = set_lyrics(melody, looked_up.plain, time)
            lyrics_source = "lrclib"
        else:
            from barbershop.analysis import asr

            words = asr.transcribe(path)
            if words:
                lyrics_confidence = asr.mean_confidence(words)
            if words and asr.attach_lyrics(melody, words, grid):
                lyrics_source = "asr"
            else:
                asr.neutral_lyrics(melody)  # honest fallback, never nonsense
                lyrics_source = "neutral"

    inp = ArrangeInput(
        title=(identity.title if identity else None) or title or Path(path).stem,
        key=detected_key,
        time=time,
        tempo=round(grid.tempo, 1),
        melody=melody,
        chords=chord_spans,
    )
    result = AnalysisResult(
        input=inp,
        tempo=grid.tempo,
        duration_seconds=len(y) / sr,
        lyrics_source=lyrics_source,
        lyrics_confidence=round(lyrics_confidence, 3),
        identity=identity,
        chord_source=chord_source,
        chord_agreement=chord_agreement,
        tab_url=tab_url,
    )
    if use_cache:
        CACHE_DIR.mkdir(exist_ok=True)
        cache_file.write_text(
            json.dumps(
                {
                    "input": inp.model_dump(),
                    "tempo": result.tempo,
                    "duration_seconds": result.duration_seconds,
                    "lyrics_source": result.lyrics_source,
                    "lyrics_confidence": result.lyrics_confidence,
                    "identity": asdict(identity) if identity else None,
                    "chord_source": chord_source,
                    "chord_agreement": chord_agreement,
                    "tab_url": tab_url,
                }
            )
        )
    return result
