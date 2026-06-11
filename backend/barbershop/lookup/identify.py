"""Song identification: chromaprint fingerprint -> AcoustID -> title/artist/year.

Strict fail-soft: identify() never raises — any failure (no fpcalc, no
network, malformed response) returns None and the caller proceeds as if
the feature didn't exist.
"""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass

import requests

ACOUSTID_APP_KEY = "crzNhTAC7w"  # registered app "Barbershopify"; an app
# identifier by design, not a secret — committing it is AcoustID's intended
# model (Picard and beets do the same), so cloners never need their own key
ACOUSTID_URL = "https://api.acoustid.org/v2/lookup"
MIN_MATCH_SCORE = 0.5
TIMEOUT = (3.05, 10)  # connect, read — offline must cost seconds, not minutes

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SongIdentity:
    title: str
    artist: str
    year: int | None
    recording_mbid: str
    match_score: float


def identify(path: str, duration: float) -> SongIdentity | None:
    """Best AcoustID match for the audio file, or None. Never raises."""
    try:
        proc = subprocess.run(["fpcalc", "-json", path], capture_output=True, timeout=60)
        if proc.returncode != 0:
            log.info("song lookup: fpcalc failed: %s", proc.stderr[:200])
            return None
        fp = json.loads(proc.stdout)
        resp = requests.get(
            ACOUSTID_URL,
            params={
                "client": ACOUSTID_APP_KEY,
                "fingerprint": fp["fingerprint"],
                "duration": int(round(fp.get("duration", duration))),
                "meta": "recordings releases",
            },
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return _best_match(resp.json())
    except FileNotFoundError:
        log.info("song lookup: fpcalc not installed, skipping identification")
        return None
    except Exception:
        log.info("song lookup: identification failed, skipping", exc_info=True)
        return None


def _best_match(data: dict) -> SongIdentity | None:
    if data.get("status") != "ok":
        return None
    results = [r for r in data.get("results", []) if r.get("score", 0) >= MIN_MATCH_SCORE]
    if not results:
        return None
    best = max(results, key=lambda r: r["score"])
    for rec in best.get("recordings", []):
        title = rec.get("title")
        artists = [a.get("name", "") for a in rec.get("artists") or [] if a.get("name")]
        if not title or not artists:
            continue
        years = [
            rel["date"]["year"]
            for rel in rec.get("releases", [])
            if rel.get("date", {}).get("year")
        ]
        return SongIdentity(
            title=title,
            artist="; ".join(artists),
            year=min(years) if years else None,
            recording_mbid=rec.get("id", ""),
            match_score=best["score"],
        )
    return None
