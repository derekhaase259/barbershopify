"""Tab chords: Chordie scraping, chord-name parsing, and span alignment."""
import pytest

from barbershop.lookup.chordnames import parse_chord


@pytest.mark.parametrize(
    "name,expected",
    [
        ("C", (0, "maj")),
        ("F#m7", (6, "min7")),
        ("Bb7", (10, "dom7")),
        ("Cmaj7", (0, "maj6")),   # barbershop never voices maj7
        ("Am7b5", (9, "halfdim7")),
        ("Ddim", (2, "dim7")),
        ("Ebdim7", (3, "dim7")),
        ("G/B", (7, "maj")),      # slash bass is the arranger's business
        ("Esus4", (4, "maj")),
        ("A7sus4", (9, "dom7")),
        ("Cadd9", (0, "maj")),
        ("C6", (0, "maj6")),
        ("Am6", (9, "min6")),
        ("Caug", (0, "aug")),
        ("C9", (0, "dom9")),
        ("C13", (0, "dom9")),
        ("Amin", (9, "min")),
        ("Cmaj", (0, "maj")),
        ("B7", (11, "dom7")),
    ],
)
def test_parse_chord(name, expected):
    assert parse_chord(name) == expected


def test_parse_chord_rejects_garbage():
    assert parse_chord("N.C.") is None
    assert parse_chord("x2") is None
    assert parse_chord("") is None


def test_parse_chord_unknown_decoration_falls_back_to_major():
    assert parse_chord("C7b9") == (0, "dom7")  # startswith family
    assert parse_chord("Gweird") == (7, "maj")
