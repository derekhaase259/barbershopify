"""Vocal isolation via Demucs — DORMANT. No longer on the melody path (RMVPE
on the raw mix replaced it; see rmvpe.py). Kept for future duet diarization.

NOT pinned anymore: `torch`/`torchaudio`/`demucs` were dropped from
requirements.txt once RMVPE made them unnecessary (~1 GB lighter install).
This module is lazy-import + fail-soft, so with those packages absent
``isolate_vocal`` simply returns ``None``. To reactivate it, install demucs:
``pip install demucs`` (pulls the Torch CPU stack). Everything below is a
function-local import; any problem — package missing, model download failed,
decode error — returns ``None``, mirroring the song-lookup rule that an
optional booster may never break analysis.
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
