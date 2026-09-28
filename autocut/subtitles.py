"""ASS subtitle generation: short-form style captions with word highlighting."""
from __future__ import annotations

import re
import shutil
import subprocess

from .planner import TimeMap

# Tried in order; the first one installed on the machine is used.
FONT_CANDIDATES = [
    "Pretendard", "Noto Sans CJK KR", "Noto Sans KR", "NanumSquare", "NanumGothic",
    "Apple SD Gothic Neo", "Malgun Gothic", "Arial",
]


def pick_font(preferred: str | None = None) -> str:
    if preferred:
        return preferred
    if shutil.which("fc-list"):
        try:
            installed = subprocess.run(["fc-list", ":", "family"], capture_output=True, text=True).stdout
        except OSError:
            installed = ""
        for name in FONT_CANDIDATES:
            if name in installed:
                return name
    return FONT_CANDIDATES[1]


def ass_color(hex_color: str, alpha: int = 0) -> str:
    """'#RRGGBB' -> ASS '&HAABBGGRR'."""
    h = hex_color.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def ass_time(t: float) -> str:
    cs = max(0, int(round(t * 100)))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def escape(text: str) -> str:
    return text.replace("\\", "＼").replace("{", "｛").replace("}", "｝").replace("\n", "\\N")


def build_ass(captions: list[dict], tmap: TimeMap, width: int, height: int, settings: dict) -> str:
    style = settings.get("caption_style", "pop")
    font = pick_font(settings.get("font") or None)
    vertical = height > width
    scale = float(settings.get("caption_size", 1.0))
    size = round(min(width, height) * (0.085 if vertical else 0.068) * scale)
    outline = max(2, round(size * 0.1))
    margin_v = round(height * (0.26 if vertical else 0.08))
    margin_h = round(width * 0.06)
    highlight = settings.get("highlight_color", "#FFE14D")
    primary = ass_color(settings.get("caption_color", "#FFFFFF"))

    if style == "box":
        # BorderStyle 3 (opaque box): libass fills the box with OutlineColour.
        cap_style = (f"Style: Caption,{font},{size},{primary},{primary},&H55000000,&H55000000,"
                     f"-1,0,0,0,100,100,0,0,3,{max(4, outline)},0,2,{margin_h},{margin_h},{margin_v},1")
    elif style == "minimal":
        cap_style = (f"Style: Caption,{font},{round(size * 0.8)},{primary},{primary},&H00000000,&H80000000,"
                     f"-1,0,0,0,100,100,0,0,1,{max(1, outline // 3)},2,2,{margin_h},{margin_h},{margin_v},1")
    else:  # pop
        cap_style = (f"Style: Caption,{font},{size},{primary},{primary},&H00000000,&H64000000,"
                     f"-1,0,0,0,100,100,0,0,1,{outline},{max(2, outline // 2)},2,{margin_h},{margin_h},{margin_v},1")

    title_size = round(size * 1.05)
    title_bg = ass_color(settings.get("title_color", "#FF3B5C"))
    title_style = (f"Style: Title,{font},{title_size},&H00FFFFFF,&H00FFFFFF,{title_bg},{title_bg},"
                   f"-1,0,0,0,100,100,0,0,3,"
                   f"{round(title_size * 0.35)},0,8,{margin_h},{margin_h},{round(height * 0.09)},1")

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        cap_style,
        title_style,
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    title = (settings.get("title") or "").strip()
    if title and tmap.duration > 0:
        end = tmap.duration if settings.get("title_always", True) else min(tmap.duration, 3.0)
        lines.append(f"Dialogue: 1,{ass_time(0)},{ass_time(end)},Title,,0,0,0,,"
                     f"{{\\fad(150,0)}}{escape(title)}")

    if settings.get("captions", True):
        for cap in captions:
            lines.extend(_caption_events(cap, tmap, style, highlight, primary))
    return "\n".join(lines) + "\n"


def _caption_events(cap: dict, tmap: TimeMap, style: str, highlight: str, primary: str) -> list[str]:
    span = tmap.span(cap["start"], cap["end"])
    if span is None:
        return []
    start, end = span
    pop = "{\\fscx85\\fscy85\\t(0,90,\\fscx100\\fscy100)}" if style == "pop" else "{\\fad(80,0)}"

    words = [w for w in cap.get("words") or [] if tmap.is_kept((w["start"] + w["end"]) / 2)]
    joined = "".join(w["text"] for w in cap.get("words") or []).strip()
    edited = _norm(joined) != _norm(cap["text"])
    if style == "minimal" or edited or len(words) < 2:
        return [f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Caption,,0,0,0,,{pop}{escape(cap['text'])}"]

    # One event per word, the currently spoken word in the highlight colour.
    hl = ass_color(highlight)
    events = []
    times = [max(start, tmap.to_output(w["start"])) for w in words]
    times[0] = start
    for i, _w in enumerate(words):
        t0 = times[i]
        t1 = times[i + 1] if i + 1 < len(words) else end
        if t1 - t0 < 0.01:
            continue
        parts = []
        for j, w in enumerate(words):
            txt = w["text"].strip()
            if j == len(words) - 1:
                txt = re.sub(r"[.,。、]+$", "", txt)
            txt = escape(txt)
            sep = " " if j and w["text"].startswith(" ") else ""
            parts.append(f"{sep}{{\\c{hl}}}{txt}{{\\c{primary}}}" if j == i else f"{sep}{txt}")
        prefix = pop if i == 0 else ""
        events.append(f"Dialogue: 0,{ass_time(t0)},{ass_time(t1)},Caption,,0,0,0,,{prefix}{''.join(parts)}")
    return events


def _norm(text: str) -> str:
    return "".join(ch for ch in text if ch.isalnum())
