"""Vocal isolation via Demucs, so the melody tracker sees a (near-)solo
voice instead of a full mix.

``librosa.pyin`` is monophonic: on a dense modern production it locks onto
whichever source is loudest frame to frame, jumping between voice, bass and
strings. Separating the vocal stem first is what makes melody extraction
usable on that material (the bundled acoustic 78s are sparse enough not to
need it, and separation leaves them unharmed).

Demucs drags in a Torch stack, so everything here is a lazy import and the
whole module is fail-soft: any problem — package missing, model download
failed, decode error — returns ``None`` and the pipeline falls back to
extracting the melody from the full mix. This mirrors the song-lookup rule:
an optional booster may never break analysis.
"""
from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger(__name__)

MODEL_NAME = "htdemucs"  # Hybrid Transformer Demucs — the current default bag
_model = None  # cached across calls; loading + the weight download are costly


def _load_model():
    global _model
    if _model is None:
        from demucs.pretrained import get_model

        _model = get_model(MODEL_NAME)
        _model.cpu()
        _model.eval()
    return _model


def isolate_vocal(path: str, sr: int) -> np.ndarray | None:
    """Return the isolated vocal as mono float32 resampled to ``sr``, or
    ``None`` if separation is unavailable for any reason."""
    try:
        import librosa
        import torch
        from demucs.apply import apply_model
        from demucs.audio import AudioFile, convert_audio

        model = _load_model()
        wav = AudioFile(path).read(
            streams=0, samplerate=model.samplerate, channels=model.audio_channels
        )
        # Demucs expects per-track standardized input (see its separate.py)
        ref = wav.mean(0)
        wav = (wav - ref.mean()) / (ref.std() + 1e-8)
        with torch.no_grad():
            sources = apply_model(
                model, wav[None], device="cpu", progress=False, num_workers=0
            )[0]
        sources = sources * ref.std() + ref.mean()

        vocal = sources[model.sources.index("vocals")]  # (channels, samples)
        mono = vocal.mean(0).cpu().numpy().astype(np.float32)
        if model.samplerate != sr:
            mono = librosa.resample(mono, orig_sr=model.samplerate, target_sr=sr)
        if not np.any(np.abs(mono) > 1e-4):
            log.info("vocal separation produced a silent stem; using the mix")
            return None
        return mono
    except Exception:
        log.info("vocal separation unavailable; using the mix", exc_info=True)
        return None
