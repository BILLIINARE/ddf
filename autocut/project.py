"""Project storage and the analyze -> plan -> render pipeline."""
from __future__ import annotations

import json
import os
import queue
import shutil
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Callable, Optional

from . import media, planner, render, transcribe

DATA_DIR = Path(os.environ.get("AUTOCUT_DATA", Path.cwd() / "data")) / "projects"


class Project:
    def __init__(self, pid: str):
        self.id = pid
        self.dir = DATA_DIR / pid
        self._lock = threading.Lock()
        self.data: dict = {}

    # ---- persistence -------------------------------------------------
    @property
    def json_path(self) -> Path:
        return self.dir / "project.json"

    def load(self) -> "Project":
        self.data = json.loads(self.json_path.read_text(encoding="utf-8"))
        return self

    def save(self) -> None:
        with self._lock:
            tmp = self.json_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.json_path)

    def update(self, **fields) -> None:
        self.data.update(fields)
        self.data["updated_at"] = time.time()
        self.save()

    def set_status(self, status: str, message: str = "", progress: float = 0.0) -> None:
        self.update(status=status, message=message, progress=round(progress, 3))

    @property
    def source(self) -> Path:
        return self.dir / self.data["source_file"]

    @property
    def preview(self) -> Path:
        proxy = self.dir / "proxy.mp4"
        return proxy if proxy.exists() else self.source

    @property
    def bgm(self) -> Optional[Path]:
        name = self.data.get("bgm_file")
        return self.dir / name if name and (self.dir / name).exists() else None

    @property
    def info(self) -> media.MediaInfo:
        i = self.data["media"]
        return media.MediaInfo(duration=i["duration"], width=i["width"], height=i["height"],
                               fps=i["fps"], rotation=0, has_audio=i["has_audio"])

    # ---- pipeline ----------------------------------------------------
    def analyze(self) -> None:
        """Probe, detect silence, transcribe, then build the automatic edit plan."""
        s = self.data["settings"]
        self.set_status("analyzing", "영상 분석 중…", 0.02)
        info = media.probe(self.source)
        self.update(media=info.to_dict())
        media.extract_thumbnail(self.source, self.dir / "thumb.jpg", at=min(1.0, info.duration / 2))
        if not (self.dir / "proxy.mp4").exists():
            self.set_status("analyzing", "미리보기용 영상 만드는 중…", 0.02)
            media.make_proxy(self.source, self.dir / "proxy.mp4", info.duration,
                             on_progress=lambda p: self.set_status("analyzing", "미리보기용 영상 만드는 중…", 0.02 + 0.06 * p))

        silences: list = []
        if info.has_audio:
            self.set_status("analyzing", "무음 구간 찾는 중…", 0.08)
            silences = media.detect_silence(self.source, info.duration, s["silence_db"], s["min_silence"])

        words: list = []
        warning = ""
        if info.has_audio and s.get("captions", True):
            self.set_status("transcribing", "음성 인식으로 자막 만드는 중…", 0.1)
            try:
                words = transcribe.transcribe(
                    str(self.source), s.get("language", "ko"), duration=info.duration,
                    on_progress=lambda p: self.set_status("transcribing", "음성 인식으로 자막 만드는 중…", 0.1 + 0.85 * p))
            except transcribe.TranscriptionUnavailable as e:
                warning = f"자막 자동 생성 실패: {e} — 자막 없이 편집합니다. 자막은 편집 화면에서 직접 추가할 수 있어요."

        self.data["analysis"] = {"silences": silences, "words": words}
        self.data.update(planner.make_plan(info.duration, info.fps, silences, words, s))
        self.data["warning"] = warning
        self.set_status("ready", "자동 편집 완료! 확인 후 내보내기를 누르세요.", 1.0)

    def replan(self, settings: dict) -> None:
        """Rebuild cuts and captions from the stored analysis with new settings."""
        old = self.data["settings"]
        self.data["settings"] = settings
        info = self.info
        analysis = self.data.get("analysis", {"silences": [], "words": []})
        if info.has_audio and (old["silence_db"] != settings["silence_db"]
                               or old["min_silence"] != settings["min_silence"]):
            analysis["silences"] = media.detect_silence(self.source, info.duration,
                                                        settings["silence_db"], settings["min_silence"])
        self.data.update(planner.make_plan(info.duration, info.fps, analysis["silences"],
                                           analysis["words"], settings))
        self.save()

    def render(self) -> None:
        self.set_status("rendering", "영상 렌더링 중…", 0.0)
        result = render.render(
            self.dir, self.source, self.info, self.data["segments"], self.data["captions"],
            self.data["settings"], bgm=self.bgm, out_name="output.mp4",
            on_progress=lambda p: self.set_status("rendering", "영상 렌더링 중…", p))
        result["rendered_at"] = time.time()
        self.update(output=result)
        self.set_status("done", "완성! 다운로드할 수 있어요.", 1.0)

    def public(self) -> dict:
        d = {k: v for k, v in self.data.items() if k != "analysis"}
        d["id"] = self.id
        return d


# ---- store ---------------------------------------------------------------
def create(filename: str, fileobj, settings: dict, auto_render: bool) -> Project:
    pid = uuid.uuid4().hex[:12]
    p = Project(pid)
    p.dir.mkdir(parents=True, exist_ok=True)
    ext = Path(filename).suffix.lower() or ".mp4"
    with open(p.dir / f"source{ext}", "wb") as f:
        shutil.copyfileobj(fileobj, f)
    p.data = {
        "name": Path(filename).stem, "source_file": f"source{ext}", "created_at": time.time(),
        "settings": settings, "auto_render": auto_render, "segments": [], "captions": [],
        "status": "queued", "message": "대기 중…", "progress": 0.0, "output": None,
    }
    p.save()
    return p


def get(pid: str) -> Optional[Project]:
    if not pid.isalnum():
        return None
    p = Project(pid)
    return p.load() if p.json_path.exists() else None


def list_all() -> list[dict]:
    if not DATA_DIR.exists():
        return []
    out = []
    for d in DATA_DIR.iterdir():
        if (d / "project.json").exists():
            try:
                out.append(get(d.name).public())
            except (json.JSONDecodeError, OSError):
                continue
    return sorted(out, key=lambda p: p.get("created_at", 0), reverse=True)


def delete(pid: str) -> None:
    p = get(pid)
    if p:
        shutil.rmtree(p.dir, ignore_errors=True)


# ---- background worker ---------------------------------------------------
_jobs: "queue.Queue[tuple[str, str]]" = queue.Queue()
_worker: Optional[threading.Thread] = None


def enqueue(pid: str, kind: str) -> None:
    """kind: 'analyze' (analyze, then render if auto_render) or 'render'."""
    global _worker
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=_work, daemon=True)
        _worker.start()
    p = get(pid)
    if p:
        p.set_status("queued", "대기 중…", 0.0)
    _jobs.put((pid, kind))


def _work() -> None:
    while True:
        pid, kind = _jobs.get()
        run_job(pid, kind)


def run_job(pid: str, kind: str, log: Callable[[str], None] = lambda _m: None) -> None:
    p = get(pid)
    if p is None:
        return
    try:
        if kind == "analyze":
            p.analyze()
            if p.data.get("auto_render"):
                p.render()
        elif kind == "render":
            p.render()
    except Exception as e:  # surface every failure to the UI
        log(traceback.format_exc())
        p.set_status("error", str(e), 0.0)
