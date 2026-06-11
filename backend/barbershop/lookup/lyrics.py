"""Real lyrics for an identified song, from LRCLIB (no key, no rate limit).

Strict fail-soft: fetch_lyrics() never raises.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import requests

from barbershop.lookup.identify import SongIdentity

LRCLIB_URL = "https://lrclib.net/api"
USER_AGENT = "barbershopify/1.0 (https://github.com/derekhaase259/barbershopify)"
TIMEOUT = (3.05, 10)
MAX_DURATION_GAP = 10.0  # seconds; a search hit further off is a different cut

log = logging.getLogger(__name__)

_LRC_TIME = re.compile(r"\[(\d+):(\d{2}(?:\.\d+)?)\]")


@dataclass(frozen=True)
class LookedUpLyrics:
    synced: list[tuple[float, str]] | None  # (seconds, line), sorted
    plain: str | None


def fetch_lyrics(identity: SongIdentity, duration: float) -> LookedUpLyrics | None:
    """LRCLIB lyrics for the identified song, or None. Never raises."""
    try:
        record = _get_exact(identity, duration) or _search(identity, duration)
        if not record or record.get("instrumental"):
            return None
        synced = parse_lrc(record.get("syncedLyrics") or "")
        plain = (record.get("plainLyrics") or "").strip() or None
        if not synced and not plain:
            return None
        return LookedUpLyrics(synced=synced or None, plain=plain)
    except Exception:
        log.info("song lookup: lyrics fetch failed, skipping", exc_info=True)
        return None


def _get_exact(identity: SongIdentity, duration: float) -> dict | None:
    resp = requests.get(
        f"{LRCLIB_URL}/get",
        params={
            "artist_name": identity.artist,
            "track_name": identity.title,
            "duration": int(round(duration)),
        },
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def _search(identity: SongIdentity, duration: float) -> dict | None:
    resp = requests.get(
        f"{LRCLIB_URL}/search",
        params={"q": f"{identity.artist} {identity.title}"},
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    hits = [h for h in resp.json() or [] if h.get("syncedLyrics") or h.get("plainLyrics")]
    if not hits:
        return None
    best = min(hits, key=lambda h: abs(h.get("duration", 0) - duration))
    if abs(best.get("duration", 0) - duration) > MAX_DURATION_GAP:
        return None
    return best


def parse_lrc(text: str) -> list[tuple[float, str]]:
    """LRC text -> sorted (seconds, line). Multiple stamps repeat the line;
    metadata tags like [ar:...] don't match the digit pattern and drop out."""
    out: list[tuple[float, str]] = []
    for raw in text.splitlines():
        stamps = _LRC_TIME.findall(raw)
        words = _LRC_TIME.sub("", raw).strip()
        if not stamps or not words:
            continue
        for minutes, seconds in stamps:
            out.append((int(minutes) * 60 + float(seconds), words))
    out.sort(key=lambda x: x[0])
    return out
