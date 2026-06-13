"""Web API: stateless transforms over the score model."""
import xml.etree.ElementTree as ET

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_list_demos():
    r = client.get("/api/demos")
    assert r.status_code == 200
    demos = r.json()
    ids = {d["id"] for d in demos}
    assert "yankee-doodle" in ids
    assert all("title" in d for d in demos)


def test_arrange_demo_returns_score_and_musicxml():
    r = client.post("/api/demos/yankee-doodle/arrange", json={"spice": 3})
    assert isinstance(r.json()["metrics"].get("input_adherence"), float)
    assert r.status_code == 200
    body = r.json()
    assert set(body["score"]["voices"]) == {"tenor", "lead", "bari", "bass"}
    root = ET.fromstring(body["musicxml"])
    assert root.tag == "score-partwise"
    assert body["violations"] == []


def test_arrange_unknown_demo_404s():
    r = client.post("/api/demos/nope/arrange", json={"spice": 3})
    assert r.status_code == 404


def test_render_roundtrip():
    score = client.post("/api/demos/yankee-doodle/arrange", json={"spice": 1}).json()["score"]
    r = client.post("/api/render", json={"score": score})
    assert r.status_code == 200
    assert ET.fromstring(r.json()["musicxml"]).tag == "score-partwise"


def test_spice_is_validated():
    r = client.post("/api/demos/yankee-doodle/arrange", json={"spice": 9})
    assert r.status_code == 422


def test_arrange_response_includes_reusable_input():
    body = client.post("/api/demos/yankee-doodle/arrange", json={"spice": 2}).json()
    r = client.post("/api/arrange", json={"input": body["input"], "spice": 4})
    assert r.status_code == 200


def test_list_test_songs():
    r = client.get("/api/test-songs")
    assert r.status_code == 200
    ids = {s["id"] for s in r.json()}
    assert "sweet-adeline" in ids


def test_unknown_test_song_404s():
    r = client.post("/api/test-songs/not-a-song/arrange", json={"spice": 3})
    assert r.status_code == 404


def test_set_lyrics_returns_fit_diagnosis():
    body = client.post("/api/demos/yankee-doodle/arrange", json={"spice": 1}).json()
    r = client.post(
        "/api/lyrics/set",
        json={"input": body["input"], "text": "my homemade rap verse goes here tonight", "spice": 1},
    )
    assert r.status_code == 200
    out = r.json()
    assert out["fit"] and all(f["status"] in ("green", "yellow", "red") for f in out["fit"])
    # the lead now carries the new words
    lead = out["score"]["voices"]["lead"]
    texts = [n["lyric"]["text"] for n in lead if n.get("lyric")]
    assert "rap" in texts


def test_compose_endpoint_returns_chart_and_meta():
    r = client.post(
        "/api/compose",
        json={"text": "The morning sun is bright with joy\nwe sing a happy song", "spice": 3, "seed": 5},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["composition"]["mode"] in ("major", "minor")
    assert body["composition"]["seed"] == 5
    assert body["score"]["voices"]["lead"]


def test_upload_rejects_unknown_extension():
    r = client.post("/api/upload", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert r.status_code == 422


def _make_test_wav(tmp_path):
    """A tiny analyzable WAV: sine melody with clicks, like the happy path uses."""
    import numpy as np
    import soundfile as sf

    sr, beat = 22050, 0.5
    melody = [60, 62, 64, 65, 67, 64, 60, 62]
    y = np.zeros(int(sr * beat * len(melody)))
    for i, m in enumerate(melody):
        t = np.linspace(0, beat, int(sr * beat), endpoint=False)
        f = 440 * 2 ** ((m - 69) / 12)
        seg = 0.5 * np.sin(2 * np.pi * f * t) * np.minimum(1, np.minimum(t / 0.01, (beat - t) / 0.02))
        y[int(i * beat * sr) : int(i * beat * sr) + len(seg)] += seg
        y[int(i * beat * sr) : int(i * beat * sr) + 200] += np.linspace(0.4, 0, 200)  # click
    path = tmp_path / "tiny.wav"
    sf.write(path, y.astype(np.float32), sr)
    return path


def test_upload_happy_path(monkeypatch, tmp_path):
    """A real WAV through the real multipart endpoint (ASR stubbed out so
    the test never loads the whisper model — exercising the doo/dah path)."""
    from barbershop.analysis import asr

    monkeypatch.setattr(asr, "transcribe", lambda path: None)
    monkeypatch.setattr("barbershop.analysis.pipeline.CACHE_DIR", tmp_path)

    with open(_make_test_wav(tmp_path), "rb") as f:
        r = client.post(
            "/api/upload?spice=2", files={"file": ("tiny.wav", f, "audio/wav")}
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["score"]["voices"]["lead"]
    assert body["lyrics"]["source"] == "neutral"  # graceful doo/dah fallback
    assert body["identity"] is None  # conftest stubs lookup to a miss
    assert body["chords"] == {"source": "audio", "agreement": None, "tab_url": None}


def test_upload_reports_identity(monkeypatch, tmp_path):
    from barbershop.analysis import asr
    from barbershop.lookup.identify import SongIdentity

    monkeypatch.setattr(asr, "transcribe", lambda path: None)
    monkeypatch.setattr("barbershop.analysis.pipeline.CACHE_DIR", tmp_path)
    monkeypatch.setattr(
        "barbershop.lookup.identify.identify",
        lambda *a, **k: SongIdentity(
            title="Synth Song", artist="Test Quartet", year=1999,
            recording_mbid="mbid-x", match_score=0.9,
        ),
    )

    with open(_make_test_wav(tmp_path), "rb") as f:
        r = client.post(
            "/api/upload?spice=2", files={"file": ("synth.wav", f, "audio/wav")}
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["identity"]["title"] == "Synth Song"
    assert body["identity"]["year"] == 1999
    assert body["score"]["title"] == "Synth Song"


def test_arrange_endpoint_accepts_duet_flag():
    demo = client.get("/api/demos").json()[0]["id"]
    r = client.post(f"/api/demos/{demo}/arrange", json={"spice": 4, "duet": True})
    assert r.status_code == 200, r.text
    assert r.json()["score"]["voices"]["bari"]


def test_upload_threads_duet_to_arranger(monkeypatch, tmp_path):
    from barbershop.analysis import asr
    import app.main as main

    monkeypatch.setattr(asr, "transcribe", lambda path: None)
    monkeypatch.setattr("barbershop.analysis.pipeline.CACHE_DIR", tmp_path)
    seen = {}
    real = main.arrange  # _arrangement_response calls the module global `arrange`

    def spy(inp, cfg):
        seen["duet"] = cfg.duet
        return real(inp, cfg)

    monkeypatch.setattr(main, "arrange", spy)
    with open(_make_test_wav(tmp_path), "rb") as f:
        r = client.post("/api/upload?spice=2&duet=true",
                        files={"file": ("tiny.wav", f, "audio/wav")})
    assert r.status_code == 200, r.text
    assert seen["duet"] is True
