"""Audio analysis pipeline, tested against a synthesized ground truth:
a sine melody with a click track and low triad pads at 120 BPM in C major.
Signal-processing assertions are tolerant by design."""
import numpy as np
import pytest
import soundfile as sf

from barbershop.analysis.decode import load_audio
from barbershop.analysis.pipeline import analyze

SR = 22050
BPM = 120
BEAT = 60 / BPM  # 0.5s

# one melody note per beat (midi), chord root/quality per measure underneath
MELODY = [60, 62, 64, 65, 67, 64, 60, 62, 64, 65, 67, 69, 67, 64, 62, 60]
CHORDS = [(0, "maj"), (5, "maj"), (7, "dom7"), (0, "maj")]
CHORD_PCS = {"maj": (0, 4, 7), "dom7": (0, 4, 7, 10)}


def _tone(freq: float, dur: float, amp: float) -> np.ndarray:
    t = np.linspace(0, dur, int(SR * dur), endpoint=False)
    env = np.minimum(1, np.minimum(t / 0.01, (dur - t) / 0.02))
    # a couple of harmonics make pyin and chroma behave like real signals
    return amp * env * (
        np.sin(2 * np.pi * freq * t)
        + 0.3 * np.sin(4 * np.pi * freq * t)
        + 0.1 * np.sin(6 * np.pi * freq * t)
    )


def _midi_hz(m: float) -> float:
    return 440 * 2 ** ((m - 69) / 12)


@pytest.fixture(scope="module")
def test_wav(tmp_path_factory):
    total = len(MELODY) * BEAT
    y = np.zeros(int(SR * total), dtype=np.float64)
    for i, midi in enumerate(MELODY):
        s = int(i * BEAT * SR)
        seg = _tone(_midi_hz(midi), BEAT, 0.5)
        y[s : s + len(seg)] += seg
    for m, (root, quality) in enumerate(CHORDS):
        s = int(m * 4 * BEAT * SR)
        for pc in CHORD_PCS[quality]:
            seg = _tone(_midi_hz(48 + ((root + pc) % 12)), 4 * BEAT, 0.22)
            y[s : s + len(seg)] += seg
        # bass root an octave down, like a real accompaniment
        seg = _tone(_midi_hz(36 + root), 4 * BEAT, 0.3)
        y[s : s + len(seg)] += seg
    # click track for the beat tracker
    rng = np.random.default_rng(7)
    for i in range(len(MELODY)):
        s = int(i * BEAT * SR)
        click = rng.uniform(-1, 1, int(0.012 * SR)) * np.linspace(1, 0, int(0.012 * SR)) * 0.4
        y[s : s + len(click)] += click
    y /= np.abs(y).max() * 1.1
    path = tmp_path_factory.mktemp("audio") / "synth.wav"
    sf.write(path, y.astype(np.float32), SR)
    return path


@pytest.fixture(scope="module")
def analysis(test_wav):
    return analyze(str(test_wav), use_cache=False)


def test_decode_loads_mono_float(test_wav):
    y, sr = load_audio(str(test_wav))
    assert sr == 22050
    assert y.ndim == 1
    assert abs(len(y) / sr - len(MELODY) * BEAT) < 0.1


def test_full_analysis(analysis):
    inp = analysis.input

    # tempo within 5% (or a metrical-level alias)
    assert any(abs(inp.tempo - BPM * f) < 6 for f in (1, 0.5, 2)), inp.tempo

    # key: C major
    assert inp.key.fifths == 0 and inp.key.mode == "major"

    # melody: sample the extracted notes at beat centers, compare pitch classes
    got = []
    for i in range(len(MELODY)):
        tick = i * 480 + 240
        note = next((n for n in inp.melody if n.onset <= tick < n.onset + n.duration), None)
        got.append(note.midi % 12 if note else None)
    want = [m % 12 for m in MELODY]
    agreement = sum(1 for g, w in zip(got, want) if g == w) / len(want)
    assert agreement >= 0.7, f"melody agreement {agreement}: {got} vs {want}"

    # chords: at least 60% of beats carry the right root
    ok = total = 0
    for m, (root, _) in enumerate(CHORDS):
        for b in range(4):
            tick = (m * 4 + b) * 480 + 240
            span = next((c for c in inp.chords if c.onset <= tick < c.onset + c.duration), None)
            total += 1
            if span is not None and span.root_pc == root:
                ok += 1
    assert ok / total >= 0.6, f"chord root accuracy {ok}/{total}"


def test_analysis_feeds_the_arranger(analysis):
    from barbershop.arranger.arrange import arrange
    from barbershop.arranger.config import ArrangerConfig
    from barbershop.arranger.validate import validate

    score = arrange(analysis.input, ArrangerConfig(spice=3))
    assert validate(score) == []


from barbershop.lookup.identify import SongIdentity
from barbershop.lookup.lyrics import LookedUpLyrics

IDENT = SongIdentity(
    title="Synth Song", artist="Test Quartet", year=1999,
    recording_mbid="mbid-x", match_score=0.9,
)


def test_melody_comes_from_rmvpe_when_available(test_wav, monkeypatch):
    # a steady B natural (pc 11) the C-major mix melody never sounds; in key,
    # so snap_to_key leaves it — its dominance proves RMVPE drove the melody.
    # ~8 s of frames at RMVPE's 10 ms hop, matching the test_wav duration.
    n = 800
    times = np.arange(n) * 0.01
    freq = np.full(n, 493.88)  # B4
    conf = np.full(n, 0.9)
    monkeypatch.setattr("barbershop.analysis.rmvpe.rmvpe_f0", lambda y, sr: (times, freq, conf))
    result = analyze(str(test_wav), use_cache=False)
    pcs = [m % 12 for m in (note.midi for note in result.input.melody)]
    assert pcs and sum(p == 11 for p in pcs) / len(pcs) > 0.5
    assert result.input.key.fifths == 0  # key still from the mix


def test_melody_falls_back_to_pyin_when_rmvpe_absent(test_wav, monkeypatch):
    monkeypatch.setattr("barbershop.analysis.rmvpe.rmvpe_f0", lambda y, sr: None)
    result = analyze(str(test_wav), use_cache=False)
    pcs = [note.midi % 12 for note in result.input.melody]
    assert pcs and sum(p == 11 for p in pcs) / len(pcs) < 0.2  # no B-natural takeover


def test_lookup_hit_uses_real_lyrics_and_skips_asr(test_wav, monkeypatch):
    monkeypatch.setattr("barbershop.lookup.identify.identify", lambda *a, **k: IDENT)
    monkeypatch.setattr(
        "barbershop.lookup.lyrics.fetch_lyrics",
        lambda *a, **k: LookedUpLyrics(
            synced=[(0.0, "shine on me"), (4.0, "harvest moon")], plain=None
        ),
    )
    monkeypatch.setattr(
        "barbershop.analysis.asr.transcribe",
        lambda path: pytest.fail("ASR must not run when lookup provides lyrics"),
    )
    result = analyze(str(test_wav), use_cache=False)
    assert result.identity == IDENT
    assert result.lyrics_source == "lrclib"
    assert result.input.title == "Synth Song"  # identified title beats filename
    words = " ".join(n.lyric.text for n in result.input.melody if n.lyric)
    assert "shine" in words


def test_lookup_plain_lyrics_fall_back_to_positional_setting(test_wav, monkeypatch):
    monkeypatch.setattr("barbershop.lookup.identify.identify", lambda *a, **k: IDENT)
    monkeypatch.setattr(
        "barbershop.lookup.lyrics.fetch_lyrics",
        lambda *a, **k: LookedUpLyrics(synced=None, plain="shine on me\nharvest moon"),
    )
    result = analyze(str(test_wav), use_cache=False)
    assert result.lyrics_source == "lrclib"
    assert any(n.lyric for n in result.input.melody)


def test_lookup_crash_changes_nothing(test_wav, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("lookup exploded")

    monkeypatch.setattr("barbershop.lookup.identify.identify", boom)
    monkeypatch.setattr("barbershop.analysis.asr.transcribe", lambda path: None)
    result = analyze(str(test_wav), use_cache=False)
    assert result.identity is None
    assert result.lyrics_source == "neutral"  # exactly today's fallback path


def test_cache_roundtrips_identity_and_title_rule(test_wav, monkeypatch, tmp_path):
    monkeypatch.setattr("barbershop.analysis.pipeline.CACHE_DIR", tmp_path)
    monkeypatch.setattr("barbershop.lookup.identify.identify", lambda *a, **k: IDENT)
    monkeypatch.setattr(
        "barbershop.lookup.lyrics.fetch_lyrics",
        lambda *a, **k: LookedUpLyrics(synced=None, plain="shine on me"),
    )
    first = analyze(str(test_wav), use_cache=True)
    assert first.identity == IDENT

    # cache hit: lookup must not run again; identified title survives the
    # filename-derived title parameter
    monkeypatch.setattr(
        "barbershop.lookup.identify.identify",
        lambda *a, **k: pytest.fail("cache hit must not re-identify"),
    )
    second = analyze(str(test_wav), use_cache=True, title="synth")
    assert second.identity == IDENT
    assert second.input.title == "Synth Song"
    assert second.lyrics_source == "lrclib"


def test_tab_correction_applies(test_wav, monkeypatch):
    from barbershop.lookup.align import TabAlignment
    from barbershop.lookup.tabs import TabChords

    monkeypatch.setattr("barbershop.lookup.identify.identify", lambda *a, **k: IDENT)
    monkeypatch.setattr(
        "barbershop.lookup.tabs.fetch_candidates",
        lambda *a, **k: [TabChords(chords=["C", "F", "G7"], url="http://tab", artist="x", title="y")],
    )

    def fake_apply(spans, tab):
        fixed = [s.model_copy(update={"quality": "min7"}) for s in spans]
        return TabAlignment(spans=fixed, agreement=0.83, transposition=0, url=tab.url)

    monkeypatch.setattr("barbershop.lookup.align.apply_tab", fake_apply)
    monkeypatch.setattr("barbershop.analysis.asr.transcribe", lambda path: None)
    result = analyze(str(test_wav), use_cache=False)
    assert result.chord_source == "tab"
    assert result.chord_agreement == 0.83
    assert result.tab_url == "http://tab"
    assert all(c.quality == "min7" for c in result.input.chords)


def test_tab_rejection_keeps_audio_chords(test_wav, monkeypatch):
    from barbershop.lookup.tabs import TabChords

    monkeypatch.setattr("barbershop.lookup.identify.identify", lambda *a, **k: IDENT)
    monkeypatch.setattr(
        "barbershop.lookup.tabs.fetch_candidates",
        lambda *a, **k: [TabChords(chords=["Eb", "Bbm", "F#", "B"], url="http://tab", artist="x", title="y")],
    )
    monkeypatch.setattr("barbershop.lookup.align.apply_tab", lambda spans, tab: None)
    monkeypatch.setattr("barbershop.analysis.asr.transcribe", lambda path: None)
    result = analyze(str(test_wav), use_cache=False)
    assert result.chord_source == "audio"
    assert result.chord_agreement is None


def test_tab_crash_changes_nothing(test_wav, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("tab fetch exploded")

    monkeypatch.setattr("barbershop.lookup.identify.identify", lambda *a, **k: IDENT)
    monkeypatch.setattr("barbershop.lookup.tabs.fetch_candidates", boom)
    monkeypatch.setattr("barbershop.analysis.asr.transcribe", lambda path: None)
    result = analyze(str(test_wav), use_cache=False)
    assert result.chord_source == "audio"


def test_cache_roundtrips_chord_fields(test_wav, monkeypatch, tmp_path):
    from barbershop.lookup.align import TabAlignment
    from barbershop.lookup.tabs import TabChords

    monkeypatch.setattr("barbershop.analysis.pipeline.CACHE_DIR", tmp_path)
    monkeypatch.setattr("barbershop.lookup.identify.identify", lambda *a, **k: IDENT)
    monkeypatch.setattr(
        "barbershop.lookup.tabs.fetch_candidates",
        lambda *a, **k: [TabChords(chords=["C", "F", "G7", "C"], url="http://tab", artist="x", title="y")],
    )
    monkeypatch.setattr(
        "barbershop.lookup.align.apply_tab",
        lambda spans, tab: TabAlignment(spans=list(spans), agreement=0.9, transposition=2, url=tab.url),
    )
    monkeypatch.setattr("barbershop.analysis.asr.transcribe", lambda path: None)
    analyze(str(test_wav), use_cache=True)
    monkeypatch.setattr(
        "barbershop.lookup.tabs.fetch_candidates",
        lambda *a, **k: pytest.fail("cache hit must not refetch the tab"),
    )
    second = analyze(str(test_wav), use_cache=True)
    assert second.chord_source == "tab"
    assert second.chord_agreement == 0.9
    assert second.tab_url == "http://tab"


def test_second_candidate_passes_gate(test_wav, monkeypatch):
    """A same-artist sheet that fails the gate falls through to a cover."""
    from barbershop.lookup.align import TabAlignment
    from barbershop.lookup.tabs import TabChords

    monkeypatch.setattr("barbershop.lookup.identify.identify", lambda *a, **k: IDENT)
    bad = TabChords(chords=["Eb", "Bbm", "F#", "B"], url="http://bad", artist="orig", title="t")
    cover = TabChords(chords=["C", "F", "G7", "C"], url="http://cover", artist="cover band", title="t")
    monkeypatch.setattr(
        "barbershop.lookup.tabs.fetch_candidates", lambda *a, **k: [bad, cover]
    )

    def gate(spans, tab):
        if tab.url == "http://bad":
            return None
        return TabAlignment(spans=list(spans), agreement=0.7, transposition=0, url=tab.url)

    monkeypatch.setattr("barbershop.lookup.align.apply_tab", gate)
    monkeypatch.setattr("barbershop.analysis.asr.transcribe", lambda path: None)
    result = analyze(str(test_wav), use_cache=False)
    assert result.chord_source == "tab"
    assert result.tab_url == "http://cover"
