"""Render smoke tests on generated media (need ffmpeg)."""
import shutil
import subprocess

import pytest

from autocut import media, planner, presets, render

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _make(path, size="640x360", audio=True, dur=6):
    args = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30:duration={dur}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=f=300:d={dur},volume='if(lt(mod(t,3),1.8),1,0)':eval=frame", "-shortest"]
    subprocess.run(args + ["-pix_fmt", "yuv420p", str(path)], check=True)


WORDS = [{"start": 0.1, "end": 0.8, "text": " 테스트"}, {"start": 0.9, "end": 1.5, "text": " 자막입니다"},
         {"start": 3.1, "end": 3.9, "text": " 두번째"}]


@pytest.mark.parametrize("preset,size,audio,bgm", [
    ("shorts", "640x360", True, False),
    ("dynamic", "360x640", True, True),
    ("vlog", "640x360", False, True),
    ("interview", "640x360", False, False),
])
def test_render(tmp_path, preset, size, audio, bgm):
    src = tmp_path / "src.mp4"
    _make(src, size, audio)
    bgm_path = None
    if bgm:
        bgm_path = tmp_path / "bgm.wav"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=500:d=2", str(bgm_path)], check=True)
    info = media.probe(src)
    settings = presets.resolve(preset, {"title": "제목", "preset": "ultrafast"})
    silences = media.detect_silence(src, info.duration) if audio else []
    plan = planner.make_plan(info.duration, info.fps, silences, WORDS if audio else [], settings)
    res = render.render(tmp_path, src, info, plan["segments"], plan["captions"], settings, bgm=bgm_path)
    out = media.probe(tmp_path / res["file"])
    assert abs(out.duration - res["duration"]) < 0.15
    assert out.has_audio == (audio or bgm)
    if audio:
        assert res["duration"] < info.duration - 1  # silences were cut
