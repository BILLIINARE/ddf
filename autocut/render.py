"""Builds the ffmpeg filter graph for a plan and renders the final video."""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from .media import MediaInfo, run_ffmpeg
from .planner import TimeMap
from .subtitles import build_ass

COLOR_FILTERS = {
    "none": "",
    "vivid": "eq=contrast=1.05:saturation=1.3",
    "warm": "eq=contrast=1.03:saturation=1.12,colorbalance=rm=0.06:gm=0.01:bm=-0.06",
    "cool": "eq=saturation=1.05,colorbalance=rm=-0.04:bm=0.06",
    "cinematic": "eq=contrast=1.12:saturation=0.85,vignette=PI/5",
    "bw": "hue=s=0,eq=contrast=1.15",
}


def output_size(info: MediaInfo, aspect: str) -> tuple[int, int]:
    if aspect in ("9:16-crop", "9:16-blur"):
        return 1080, 1920
    if aspect == "1:1":
        return 1080, 1080
    w, h = info.display_size
    scale = min(1.0, 1920 / max(w, h))
    return int(w * scale) // 2 * 2, int(h * scale) // 2 * 2


def _aspect_chain(aspect: str, w: int, h: int, src: str, dst: str) -> str:
    if aspect == "9:16-blur":
        return (f"[{src}]split=2[bgsrc][fgsrc];"
                f"[bgsrc]scale={w // 4}:{h // 4}:force_original_aspect_ratio=increase,crop={w // 4}:{h // 4},"
                f"boxblur=10:2,scale={w}:{h},eq=brightness=-0.08[bg];"
                f"[fgsrc]scale={w}:{h}:force_original_aspect_ratio=decrease[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[{dst}];")
    if aspect in ("9:16-crop", "1:1"):
        return f"[{src}]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1[{dst}];"
    return f"[{src}]scale={w}:{h},setsar=1[{dst}];"


def _frame_ranges(segments: list[dict], tmap: TimeMap, fps: float, want) -> list[tuple[int, int]]:
    out = []
    for seg in segments:
        if seg["keep"] and want(seg):
            a = round(tmap.to_output(seg["start"]) * fps)
            b = round((tmap.to_output(seg["start"]) + seg["end"] - seg["start"]) * fps)
            if b > a:
                out.append((a, b))
    return out


def build_graph(info: MediaInfo, segments: list[dict], settings: dict, fps: float,
                has_bgm: bool, ass_file: Optional[str]) -> tuple[str, float, tuple[int, int]]:
    """Return (filter_complex script, output duration, output size)."""
    tmap = TimeMap.from_segments(segments)
    keeps = [s for s in segments if s["keep"]]
    w, h = output_size(info, settings.get("aspect", "original"))
    duration = tmap.duration

    vsel = "+".join(f"between(n,{round(s['start'] * fps)},{round(s['end'] * fps) - 1})" for s in keeps)
    graph = f"[0:v]fps={fps},select='{vsel}',setpts=N/{fps}/TB[cut];"
    graph += _aspect_chain(settings.get("aspect", "original"), w, h, "cut", "framed")

    post: list[str] = []
    zoomed = _frame_ranges(segments, tmap, fps, lambda s: s.get("zoom", 1.0) > 1.0)
    if zoomed:
        z = max(1.01, float(settings.get("zoom_amount", 1.12)))
        cond = "+".join(f"between(in,{a},{b - 1})" for a, b in zoomed)
        post.append(f"zoompan=z='if({cond},{z},1)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                    f":d=1:s={w}x{h}:fps={fps}")
    color = COLOR_FILTERS.get(settings.get("color", "vivid"), "")
    if color:
        post.append(color)
    if ass_file:
        post.append(f"ass={ass_file}")
    if settings.get("flash", False) and len(keeps) > 1:
        cuts = [a for a, _ in _frame_ranges(segments, tmap, fps, lambda s: True)][1:]
        cond = "+".join(f"between(n,{a},{a + 1})" for a in cuts)
        post.append(f"drawbox=x=0:y=0:w=iw:h=ih:color=white@0.45:t=fill:enable='{cond}'")
    post.append("format=yuv420p")
    graph += f"[framed]{','.join(post)}[vout]"

    if info.has_audio:
        asel = "+".join(f"between(t,{s['start']:.6f},{s['end']:.6f})" for s in keeps)
        graph += (f";[0:a]aresample=48000,aformat=channel_layouts=stereo,asetnsamples=n=48:p=0,"
                  f"aselect='{asel}',asetpts=N/SR/TB")
        if settings.get("normalize_audio", True):
            graph += ",loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000"
        graph += "[voice]"
    if has_bgm:
        vol = float(settings.get("bgm_volume", 0.18))
        fade_st = max(0.0, duration - 2.0)
        graph += (f";[1:a]aresample=48000,aformat=channel_layouts=stereo,volume={vol},"
                  f"atrim=duration={duration:.3f},afade=t=in:d=0.5,afade=t=out:st={fade_st:.3f}:d=2[music]")
        if info.has_audio:
            graph += (";[voice]asplit=2[v1][v2];"
                      "[music][v2]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=400[ducked];"
                      "[v1][ducked]amix=inputs=2:duration=first:normalize=0[aout]")
        else:
            graph += ";[music]anull[aout]"
    elif info.has_audio:
        graph += ";[voice]anull[aout]"
    return graph, duration, (w, h)


def render(workdir: Path, source: Path, info: MediaInfo, segments: list[dict], captions: list[dict],
           settings: dict, bgm: Optional[Path] = None, out_name: str = "output.mp4",
           on_progress: Optional[Callable[[float], None]] = None) -> dict:
    fps = min(60.0, round(info.fps, 3)) or 30.0
    tmap = TimeMap.from_segments(segments)
    w, h = output_size(info, settings.get("aspect", "original"))

    ass_file = None
    if (settings.get("captions", True) and captions) or (settings.get("title") or "").strip():
        (workdir / "captions.ass").write_text(build_ass(captions, tmap, w, h, settings), encoding="utf-8")
        ass_file = "captions.ass"

    graph, duration, size = build_graph(info, segments, settings, fps, bgm is not None, ass_file)
    (workdir / "graph.txt").write_text(graph, encoding="utf-8")

    args = ["-i", str(source.resolve())]
    if bgm is not None:
        args += ["-stream_loop", "-1", "-i", str(bgm.resolve())]
    args += ["-filter_complex_script", "graph.txt", "-map", "[vout]"]
    if info.has_audio or bgm is not None:
        args += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
    args += ["-c:v", "libx264", "-preset", settings.get("preset", "veryfast"), "-crf", str(settings.get("crf", 20)),
             "-r", str(fps), "-t", f"{duration:.3f}", "-movflags", "+faststart", out_name]
    run_ffmpeg(args, duration, cwd=workdir, on_progress=on_progress)
    return {"file": out_name, "duration": duration, "width": size[0], "height": size[1]}
