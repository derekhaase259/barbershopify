"""FastAPI layer: a thin, stateless web wrapper around the music package."""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

from barbershop.arranger.arrange import ArrangeInput, arrange
from barbershop.arranger.config import ArrangerConfig
from barbershop.arranger.validate import metrics, validate
from barbershop.demos import DEMOS
from barbershop.midi import to_midi
from barbershop.musicxml import to_musicxml
from barbershop.score import Score

TEST_SONGS_DIR = Path(__file__).resolve().parents[2] / "test_songs"
ALLOWED_SUFFIXES = {".mp3", ".m4a", ".wav"}

app = FastAPI(title="barbershopify")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ArrangeOptions(BaseModel):
    spice: int = Field(default=3, ge=1, le=5)
    duet: bool = False


class ArrangeRequest(BaseModel):
    input: ArrangeInput
    spice: int = Field(default=3, ge=1, le=5)
    duet: bool = False


class RenderRequest(BaseModel):
    score: Score


def _arrangement_response(inp: ArrangeInput, spice: int, duet: bool = False) -> dict:
    score = arrange(inp, ArrangerConfig(spice=spice, duet=duet))
    return {
        "input": inp.model_dump(),  # cached client-side so re-arrange skips analysis
        "score": score.model_dump(),
        "musicxml": to_musicxml(score),
        "violations": [
            str(v) for v in validate(score, input_chords=inp.chords, input_key=inp.key)
        ],
        "metrics": metrics(score, input_chords=inp.chords, input_key=inp.key),
    }


def _song_title(stem: str) -> str:
    return re.sub(r"[-_]+", " ", stem).title()


@app.get("/api/demos")
def list_demos() -> list[dict]:
    return [{"id": demo_id, "title": inp.title} for demo_id, inp in DEMOS.items()]


@app.post("/api/demos/{demo_id}/arrange")
def arrange_demo(demo_id: str, options: ArrangeOptions) -> dict:
    if demo_id not in DEMOS:
        raise HTTPException(status_code=404, detail=f"unknown demo {demo_id!r}")
    return _arrangement_response(DEMOS[demo_id], options.spice, options.duet)


@app.post("/api/arrange")
def arrange_input(req: ArrangeRequest) -> dict:
    return _arrangement_response(req.input, req.spice, req.duet)


@app.post("/api/render")
def render(req: RenderRequest) -> dict:
    """Re-render an (edited) score; legality is re-checked so the UI can
    tell the user honestly what their edit did to the chart."""
    return {
        "musicxml": to_musicxml(req.score),
        "violations": [str(v) for v in validate(req.score)],
        "metrics": metrics(req.score),
    }


class ComposeRequest(BaseModel):
    text: str
    spice: int = Field(default=3, ge=1, le=5)
    seed: int = 0
    title: str = "From a Poem"


@app.post("/api/compose")
def compose_endpoint(req: ComposeRequest) -> dict:
    from barbershop.composer.compose import compose

    try:
        result = compose(req.text, seed=req.seed, title=req.title)
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    response = _arrangement_response(result.input, req.spice)
    response["composition"] = result.meta | {"seed": req.seed}
    return response


class SetLyricsRequest(BaseModel):
    input: ArrangeInput
    text: str
    spice: int = Field(default=3, ge=1, le=5)


@app.post("/api/lyrics/set")
def set_lyrics_endpoint(req: SetLyricsRequest) -> dict:
    from barbershop.textset.align import set_lyrics

    melody, reports = set_lyrics(req.input.melody, req.text, req.input.time)
    inp = req.input.model_copy(update={"melody": melody})
    response = _arrangement_response(inp, req.spice)
    response["fit"] = [
        {
            "phrase": r.phrase_index + 1,
            "status": r.status,
            "syllables": r.syllables,
            "notes": r.notes,
            "detail": r.detail,
        }
        for r in reports
    ]
    return response


@app.post("/api/export/midi")
def export_midi(req: RenderRequest) -> Response:
    return Response(
        content=to_midi(req.score),
        media_type="audio/midi",
        headers={"Content-Disposition": 'attachment; filename="arrangement.mid"'},
    )


@app.get("/api/test-songs")
def list_test_songs() -> list[dict]:
    if not TEST_SONGS_DIR.is_dir():
        return []
    return [
        {"id": p.stem, "title": _song_title(p.stem)}
        for p in sorted(TEST_SONGS_DIR.glob("*.mp3"))
    ]


@app.post("/api/test-songs/{song_id}/arrange")
def arrange_test_song(song_id: str, options: ArrangeOptions) -> dict:
    path = TEST_SONGS_DIR / f"{song_id}.mp3"
    if not re.fullmatch(r"[a-z0-9-]+", song_id) or not path.exists():
        raise HTTPException(status_code=404, detail=f"unknown test song {song_id!r}")
    from barbershop.analysis.pipeline import analyze  # heavy import, deferred

    result = analyze(str(path), title=_song_title(song_id))
    response = _arrangement_response(result.input, options.spice, options.duet)
    response["lyrics"] = {"source": result.lyrics_source, "confidence": result.lyrics_confidence}
    response["identity"] = asdict(result.identity) if result.identity else None
    response["chords"] = {
        "source": result.chord_source,
        "agreement": result.chord_agreement,
        "tab_url": result.tab_url,
    }
    return response


@app.post("/api/upload")
def upload_and_arrange(file: UploadFile, spice: int = 3, duet: bool = False) -> dict:
    """Synchronous on purpose: FastAPI runs it in a worker thread, so the
    minutes-long analysis (and the first-run model downloads) can't freeze
    the event loop for every other request."""
    suffix = Path(file.filename or "upload").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=422,
            detail=f"unsupported file type {suffix!r}; use .mp3, .m4a or .wav",
        )
    if not 1 <= spice <= 5:
        raise HTTPException(status_code=422, detail="spice must be 1-5")
    from barbershop.analysis.pipeline import analyze  # heavy import, deferred

    # delete=False + explicit unlink: on Windows an open NamedTemporaryFile
    # cannot be reopened by name, so ffmpeg could never read it
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        shutil.copyfileobj(file.file, tmp)
        tmp.close()
        try:
            result = analyze(
                tmp.name,
                title=Path(file.filename or "Upload").stem,
            )
        except ValueError as err:
            raise HTTPException(status_code=422, detail=str(err)) from err
    finally:
        tmp.close()
        os.unlink(tmp.name)
    response = _arrangement_response(result.input, spice, duet)
    response["lyrics"] = {"source": result.lyrics_source, "confidence": result.lyrics_confidence}
    response["identity"] = asdict(result.identity) if result.identity else None
    response["chords"] = {
        "source": result.chord_source,
        "agreement": result.chord_agreement,
        "tab_url": result.tab_url,
    }
    return response
