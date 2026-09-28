"""Speech-to-text with word timestamps (faster-whisper, runs locally)."""
from __future__ import annotations

import os
import threading
from typing import Callable, Optional

_model = None
_model_lock = threading.Lock()


class TranscriptionUnavailable(RuntimeError):
    pass


def _load_model():
    global _model
    with _model_lock:
        if _model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as e:  # pragma: no cover - depends on environment
                raise TranscriptionUnavailable("faster-whisper 가 설치되어 있지 않습니다 (pip install faster-whisper).") from e
            size = os.environ.get("AUTOCUT_WHISPER_MODEL", "small")
            device = os.environ.get("AUTOCUT_WHISPER_DEVICE", "auto")
            compute = os.environ.get("AUTOCUT_WHISPER_COMPUTE", "int8")
            try:
                _model = WhisperModel(size, device=device, compute_type=compute)
            except Exception as e:  # network errors, missing CUDA, ...
                raise TranscriptionUnavailable(f"Whisper 모델 '{size}' 을(를) 불러오지 못했습니다: {e}") from e
        return _model


def transcribe(path: str, language: Optional[str] = "ko",
               on_progress: Optional[Callable[[float], None]] = None,
               duration: float = 0.0) -> list[dict]:
    """Return a flat list of words: ``[{"start", "end", "text"}]`` in source time.

    ``text`` keeps Whisper's leading space so words can be re-joined verbatim.
    """
    model = _load_model()
    segments, _info = model.transcribe(
        path,
        language=None if language in (None, "", "auto") else language,
        word_timestamps=True,
        vad_filter=True,
        condition_on_previous_text=False,
    )
    words: list[dict] = []
    for seg in segments:
        for w in seg.words or []:
            if not w.word.strip():
                continue
            words.append({"start": round(float(w.start), 3), "end": round(float(w.end), 3), "text": w.word})
        if on_progress and duration > 0:
            on_progress(min(1.0, seg.end / duration))
    return words
