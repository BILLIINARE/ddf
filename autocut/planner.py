"""Turns analysis results (silences, words) into an edit plan.

Everything here is pure Python so it can be unit tested without ffmpeg.
All times in a plan are *source* times; :class:`TimeMap` converts them to the
edited (output) timeline.
"""
from __future__ import annotations

import bisect
import re
from dataclasses import dataclass

Range = tuple[float, float]

# Hesitation sounds that are cut out of the video and hidden from captions.
FILLERS = {
    "음", "으음", "음음", "엄", "어", "어어", "으", "흠", "아아", "그으", "저어",
    "um", "umm", "uh", "uhh", "erm", "hmm", "mm", "ah",
}
_PUNCT = re.compile(r"[\s.,…~!?·\-\"'“”‘’]+")


def is_filler(text: str) -> bool:
    return _PUNCT.sub("", text).lower() in FILLERS


def snap(t: float, fps: float) -> float:
    """Round a time onto the frame grid so audio and video cut at the same instant."""
    return round(round(t * fps) / fps, 6)


def merge_ranges(ranges: list[Range], gap: float = 0.0) -> list[Range]:
    out: list[list[float]] = []
    for s, e in sorted(ranges):
        if e <= s:
            continue
        if out and s <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def subtract_ranges(keeps: list[Range], cuts: list[Range]) -> list[Range]:
    result: list[Range] = []
    cuts = merge_ranges(cuts)
    for s, e in keeps:
        cur = s
        for cs, ce in cuts:
            if ce <= cur or cs >= e:
                continue
            if cs > cur:
                result.append((cur, cs))
            cur = max(cur, ce)
        if cur < e:
            result.append((cur, e))
    return result


def keep_ranges(duration: float, silences: list[Range], padding: float = 0.12) -> list[Range]:
    """Complement of the silences, leaving ``padding`` seconds of breathing room around speech."""
    cuts = []
    for s, e in silences:
        s2 = s + padding if s > 0 else 0.0
        e2 = e - padding if e < duration else duration
        if e2 - s2 > 0.05:
            cuts.append((s2, e2))
    return subtract_ranges([(0.0, duration)], cuts)


def build_segments(duration: float, fps: float, keeps: list[Range], min_keep: float = 0.25,
                   punch_zoom: float = 1.0) -> list[dict]:
    """Segments covering the whole source, each marked keep/cut.

    Kept segments that directly follow a cut alternate between normal framing
    and a punch-in zoom, the classic jump-cut look.
    """
    snapped = [(snap(s, fps), snap(e, fps)) for s, e in merge_ranges(keeps, gap=0.05)]
    snapped = [(s, e) for s, e in snapped if e - s >= min_keep]
    end = snap(duration, fps)

    segments: list[dict] = []
    cursor = 0.0
    zoom_next = False
    for s, e in snapped:
        if s > cursor + 1e-6:
            segments.append({"start": cursor, "end": s, "keep": False, "zoom": 1.0})
            zoom_next = not zoom_next
        segments.append({"start": s, "end": e, "keep": True,
                         "zoom": punch_zoom if (zoom_next and punch_zoom > 1.0) else 1.0})
        cursor = e
    if end > cursor + 1e-6:
        segments.append({"start": cursor, "end": end, "keep": False, "zoom": 1.0})
    for i, seg in enumerate(segments):
        seg["id"] = f"s{i}"
    return segments


def build_captions(words: list[dict], max_chars: int = 14, max_gap: float = 0.5,
                   drop_fillers: bool = True) -> list[dict]:
    """Group words into short one-line captions (short-form style)."""
    captions: list[dict] = []
    cur: list[dict] = []

    def flush() -> None:
        if cur:
            text = "".join(w["text"] for w in cur).strip()
            captions.append({"id": f"c{len(captions)}", "start": cur[0]["start"], "end": cur[-1]["end"],
                             "text": text, "words": [dict(w) for w in cur]})
            cur.clear()

    for w in words:
        if drop_fillers and is_filler(w["text"]):
            continue
        if cur:
            text_len = len("".join(x["text"] for x in cur).strip()) + len(w["text"])
            if text_len > max_chars or w["start"] - cur[-1]["end"] > max_gap:
                flush()
        cur.append(w)
        if w["text"].rstrip().endswith((".", "?", "!", "。")):
            flush()
    flush()
    for c in captions:
        c["text"] = _tidy(c["text"])
    return captions


def _tidy(text: str) -> str:
    """Short-form captions drop trailing commas/periods but keep ? and !."""
    return re.sub(r"[.,。、]+$", "", text.strip())


@dataclass
class TimeMap:
    """Maps source time to output time given the kept segments."""

    keeps: list[Range]

    @classmethod
    def from_segments(cls, segments: list[dict]) -> "TimeMap":
        return cls([(s["start"], s["end"]) for s in segments if s["keep"]])

    def __post_init__(self) -> None:
        self._starts = [s for s, _ in self.keeps]
        self._offsets = []
        acc = 0.0
        for s, e in self.keeps:
            self._offsets.append(acc)
            acc += e - s
        self.duration = acc

    def to_output(self, t: float) -> float:
        """Output time of source time ``t``; times inside a cut snap to the next kept frame."""
        i = bisect.bisect_right(self._starts, t) - 1
        if i >= 0:
            s, e = self.keeps[i]
            if t < e:
                return self._offsets[i] + (t - s)
            return self._offsets[i] + (e - s)
        return 0.0

    def is_kept(self, t: float) -> bool:
        i = bisect.bisect_right(self._starts, t) - 1
        return i >= 0 and t < self.keeps[i][1]

    def span(self, start: float, end: float) -> Range | None:
        a, b = self.to_output(start), self.to_output(end)
        return (a, b) if b - a > 0.05 else None


def make_plan(duration: float, fps: float, silences: list[Range], words: list[dict], settings: dict) -> dict:
    keeps = [(0.0, duration)]
    if settings.get("cut_silence", True):
        keeps = keep_ranges(duration, silences, settings.get("padding", 0.12))
    if settings.get("remove_fillers", True):
        filler_cuts = [(w["start"], w["end"]) for w in words
                       if is_filler(w["text"]) and w["end"] - w["start"] < 1.5]
        keeps = subtract_ranges(keeps, filler_cuts)
    if sum(e - s for s, e in keeps) < 0.5:  # nothing detected as speech: keep everything
        keeps = [(0.0, duration)]
    zoom = settings.get("zoom_amount", 1.12) if settings.get("zoom_punch", True) else 1.0
    segments = build_segments(duration, fps, keeps, punch_zoom=zoom)
    captions = build_captions(words, max_chars=int(settings.get("max_chars", 14)),
                              drop_fillers=settings.get("remove_fillers", True))
    return {"segments": segments, "captions": captions}
