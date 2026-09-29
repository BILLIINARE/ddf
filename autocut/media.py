"""ffmpeg / ffprobe helpers: probing, silence detection, running renders."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


class FFmpegError(RuntimeError):
    pass


def require_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise FFmpegError(f"'{tool}' 를 찾을 수 없습니다. ffmpeg 를 설치하고 PATH 에 추가하세요.")


@dataclass
class MediaInfo:
    duration: float
    width: int
    height: int
    fps: float
    rotation: int
    has_audio: bool

    @property
    def display_size(self) -> tuple[int, int]:
        """Width/height as the viewer sees them (phone videos are often rotated)."""
        if self.rotation % 180 == 90:
            return self.height, self.width
        return self.width, self.height

    def to_dict(self) -> dict:
        w, h = self.display_size
        return {"duration": self.duration, "width": w, "height": h, "fps": self.fps,
                "has_audio": self.has_audio}


def _parse_rate(rate: str) -> float:
    if not rate or rate == "0/0":
        return 0.0
    if "/" in rate:
        num, den = rate.split("/")
        return float(num) / float(den) if float(den) else 0.0
    return float(rate)


def probe(path: str | Path) -> MediaInfo:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise FFmpegError(f"영상 정보를 읽을 수 없습니다: {out.stderr.strip()}")
    data = json.loads(out.stdout)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise FFmpegError("비디오 스트림이 없는 파일입니다.")
    has_audio = any(s.get("codec_type") == "audio" for s in streams)

    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
    fps = _parse_rate(video.get("avg_frame_rate", "")) or _parse_rate(video.get("r_frame_rate", "")) or 30.0

    rotation = 0
    tags = video.get("tags", {}) or {}
    if "rotate" in tags:
        rotation = int(float(tags["rotate"]))
    for side in video.get("side_data_list", []) or []:
        if "rotation" in side:
            rotation = int(float(side["rotation"]))
    return MediaInfo(duration=duration, width=int(video["width"]), height=int(video["height"]),
                     fps=fps, rotation=abs(rotation) % 360, has_audio=has_audio)


_SIL_START = re.compile(r"silence_start: (-?[\d.]+)")
_SIL_END = re.compile(r"silence_end: (-?[\d.]+)")


def detect_silence(path: str | Path, duration: float, noise_db: float = -35.0,
                   min_silence: float = 0.45) -> list[tuple[float, float]]:
    """Return (start, end) intervals where audio stays below ``noise_db``."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn",
         "-af", f"silencedetect=noise={noise_db}dB:d={min_silence}", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise FFmpegError(f"무음 구간 분석 실패: {proc.stderr[-500:]}")
    silences: list[tuple[float, float]] = []
    start: Optional[float] = None
    for line in proc.stderr.splitlines():
        if (m := _SIL_START.search(line)):
            start = max(0.0, float(m.group(1)))
        elif (m := _SIL_END.search(line)) and start is not None:
            silences.append((start, min(duration, float(m.group(1)))))
            start = None
    if start is not None:  # silence runs to the end of the file
        silences.append((start, duration))
    return silences


def run_ffmpeg(args: list[str], total_seconds: float, cwd: Optional[Path] = None,
               on_progress: Optional[Callable[[float], None]] = None) -> None:
    """Run ffmpeg, reporting progress in [0, 1] from its -progress output."""
    cmd = ["ffmpeg", "-hide_banner", "-y", "-nostats", "-progress", "pipe:1", *args]
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stderr_tail: list[str] = []

    def drain_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_tail.append(line)
            del stderr_tail[:-40]

    t = threading.Thread(target=drain_stderr, daemon=True)
    t.start()
    assert proc.stdout is not None
    for line in proc.stdout:
        key, _, value = line.strip().partition("=")
        if key == "out_time_us" and on_progress and total_seconds > 0:
            try:
                on_progress(min(1.0, int(value) / 1e6 / total_seconds))
            except ValueError:
                pass
    proc.wait()
    t.join(timeout=5)
    if proc.returncode != 0:
        raise FFmpegError("ffmpeg 렌더링 실패:\n" + "".join(stderr_tail[-15:]))


def make_proxy(path: str | Path, out: str | Path, duration: float,
               on_progress: Optional[Callable[[float], None]] = None) -> None:
    """Small H.264 copy for smooth in-browser preview (HEVC/4K sources often won't play)."""
    run_ffmpeg(["-i", str(Path(path).resolve()), "-vf", "scale='if(gt(iw,ih),-2,540)':'if(gt(iw,ih),540,-2)'",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-g", "15", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(Path(out).resolve())],
               duration, on_progress=on_progress)


def extract_thumbnail(path: str | Path, out: str | Path, at: float = 0.5) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-y", "-ss", str(at), "-i", str(path), "-frames:v", "1",
                    "-vf", "scale=480:-2", str(out)], capture_output=True)
