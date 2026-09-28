"""Editing presets. Every key here is a render/plan setting; see DEFAULTS for the full list."""
from __future__ import annotations

DEFAULTS: dict = {
    # cutting
    "cut_silence": True,
    "silence_db": -35.0,
    "min_silence": 0.45,
    "padding": 0.12,
    "remove_fillers": True,
    # captions
    "captions": True,
    "language": "ko",
    "caption_style": "pop",        # pop | box | minimal
    "caption_size": 1.0,
    "caption_color": "#FFFFFF",
    "highlight_color": "#FFE14D",
    "max_chars": 14,
    "font": "",
    "title": "",
    "title_color": "#FF3B5C",
    "title_always": True,
    # effects
    "aspect": "9:16-blur",         # original | 9:16-crop | 9:16-blur | 1:1
    "zoom_punch": True,
    "zoom_amount": 1.12,
    "flash": False,
    "color": "vivid",              # none | vivid | warm | cool | cinematic | bw
    # audio
    "normalize_audio": True,
    "bgm_volume": 0.18,
    # encoding
    "preset": "veryfast",
    "crf": 20,
}

PRESETS: dict[str, dict] = {
    "shorts": {
        "label": "숏폼 (쇼츠/릴스)",
        "aspect": "9:16-blur", "caption_style": "pop", "zoom_punch": True, "zoom_amount": 1.12,
        "flash": False, "color": "vivid", "min_silence": 0.4, "padding": 0.1,
    },
    "dynamic": {
        "label": "다이나믹 (빠른 컷 + 플래시)",
        "aspect": "9:16-crop", "caption_style": "pop", "zoom_punch": True, "zoom_amount": 1.2,
        "flash": True, "color": "vivid", "min_silence": 0.3, "padding": 0.08, "max_chars": 10,
        "highlight_color": "#7CFF4D",
    },
    "vlog": {
        "label": "감성 브이로그",
        "aspect": "original", "caption_style": "minimal", "zoom_punch": False,
        "flash": False, "color": "warm", "min_silence": 0.8, "padding": 0.2, "max_chars": 20,
    },
    "interview": {
        "label": "깔끔 인터뷰/강의",
        "aspect": "original", "caption_style": "box", "zoom_punch": True, "zoom_amount": 1.08,
        "flash": False, "color": "none", "min_silence": 0.6, "padding": 0.15, "max_chars": 22,
    },
}


def resolve(preset: str | None = None, overrides: dict | None = None) -> dict:
    settings = dict(DEFAULTS)
    if preset and preset in PRESETS:
        settings.update({k: v for k, v in PRESETS[preset].items() if k != "label"})
    for k, v in (overrides or {}).items():
        if k in DEFAULTS:
            settings[k] = type(DEFAULTS[k])(v) if isinstance(DEFAULTS[k], (int, float)) and not isinstance(DEFAULTS[k], bool) else v
    return settings
