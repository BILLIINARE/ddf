"""FastAPI web server: upload, edit, render, download."""
from __future__ import annotations

import json
import shutil
import traceback
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import presets, project
from .planner import TimeMap

WEB_DIR = Path(__file__).parent / "web"
BUSY = {"queued", "analyzing", "transcribing", "rendering"}
BGM_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}

app = FastAPI(title="AutoCut")


def _get(pid: str) -> project.Project:
    p = project.get(pid)
    if p is None:
        raise HTTPException(404, "프로젝트를 찾을 수 없습니다.")
    return p


def _idle(p: project.Project) -> project.Project:
    if p.data.get("status") in BUSY:
        raise HTTPException(409, "작업이 진행 중입니다. 끝난 뒤 다시 시도하세요.")
    return p


@app.get("/api/presets")
def get_presets() -> dict:
    return {"defaults": presets.DEFAULTS,
            "presets": {k: {"label": v["label"], "settings": presets.resolve(k)} for k, v in presets.PRESETS.items()}}


@app.get("/api/projects")
def list_projects() -> list[dict]:
    return project.list_all()


@app.post("/api/projects")
def create_project(file: UploadFile = File(...), preset: str = Form("shorts"), settings: str = Form("{}"),
                   auto_render: bool = Form(True), bgm: Optional[UploadFile] = File(None)) -> dict:
    try:
        overrides = json.loads(settings or "{}")
    except json.JSONDecodeError:
        raise HTTPException(400, "settings 는 JSON 이어야 합니다.")
    p = project.create(file.filename or "video.mp4", file.file, presets.resolve(preset, overrides), auto_render)
    if bgm is not None and bgm.filename:
        _save_bgm(p, bgm)
    project.enqueue(p.id, "analyze")
    return p.public()


@app.get("/api/projects/{pid}")
def get_project(pid: str) -> dict:
    return _get(pid).public()


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    segments: Optional[list[dict]] = None
    captions: Optional[list[dict]] = None
    settings: Optional[dict] = None


@app.put("/api/projects/{pid}")
def update_project(pid: str, body: ProjectUpdate) -> dict:
    p = _idle(_get(pid))
    if body.name is not None:
        p.data["name"] = body.name
    if body.segments is not None:
        p.data["segments"] = _clean_segments(body.segments)
    if body.captions is not None:
        p.data["captions"] = _clean_captions(body.captions)
    if body.settings is not None:
        p.data["settings"] = presets.resolve(None, {**p.data["settings"], **body.settings})
    if p.data.get("status") == "done":
        p.data.update(status="ready", message="수정됨 — 다시 내보내기를 눌러주세요.")
    p.save()
    return p.public()


@app.post("/api/projects/{pid}/replan")
def replan(pid: str, body: dict) -> dict:
    """Re-run automatic cutting/captioning with new settings (discards manual edits)."""
    p = _idle(_get(pid))
    p.replan(presets.resolve(None, {**p.data["settings"], **body.get("settings", {})}))
    p.data.update(status="ready", message="새 설정으로 다시 자동 편집했어요.")
    p.save()
    return p.public()


@app.post("/api/projects/{pid}/render")
def render_project(pid: str) -> dict:
    p = _idle(_get(pid))
    if not any(s["keep"] for s in p.data.get("segments", [])):
        raise HTTPException(400, "남겨둔 구간이 없습니다.")
    project.enqueue(pid, "render")
    return _get(pid).public()


@app.post("/api/projects/{pid}/analyze")
def reanalyze(pid: str) -> dict:
    p = _idle(_get(pid))
    p.update(auto_render=False)
    project.enqueue(pid, "analyze")
    return _get(pid).public()


@app.post("/api/projects/{pid}/bgm")
def upload_bgm(pid: str, file: UploadFile = File(...)) -> dict:
    p = _idle(_get(pid))
    _save_bgm(p, file)
    return p.public()


@app.delete("/api/projects/{pid}/bgm")
def delete_bgm(pid: str) -> dict:
    p = _idle(_get(pid))
    if p.bgm:
        p.bgm.unlink()
    p.update(bgm_file=None, bgm_name=None)
    return p.public()


@app.delete("/api/projects/{pid}")
def delete_project(pid: str) -> dict:
    _idle(_get(pid))
    project.delete(pid)
    return {"ok": True}


@app.get("/api/projects/{pid}/source")
def source(pid: str):
    return FileResponse(_get(pid).preview)


@app.get("/api/projects/{pid}/thumb")
def thumb(pid: str):
    path = _get(pid).dir / "thumb.jpg"
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path)


@app.get("/api/projects/{pid}/output")
def output(pid: str, download: bool = False):
    p = _get(pid)
    path = p.dir / "output.mp4"
    if not path.exists():
        raise HTTPException(404, "아직 렌더링된 영상이 없습니다.")
    name = f"{p.data.get('name', 'autocut')}_edit.mp4"
    return FileResponse(path, media_type="video/mp4", filename=name if download else None)


@app.get("/api/projects/{pid}/captions.srt")
def captions_srt(pid: str):
    """Captions on the edited timeline, for uploading to YouTube etc."""
    p = _get(pid)
    tmap = TimeMap.from_segments(p.data["segments"])
    lines, n = [], 0
    for c in p.data["captions"]:
        span = tmap.span(c["start"], c["end"])
        if span:
            n += 1
            lines += [str(n), f"{_srt_time(span[0])} --> {_srt_time(span[1])}", c["text"], ""]
    return PlainTextResponse("\n".join(lines), headers={
        "Content-Disposition": f'attachment; filename="{pid}.srt"'})


def _srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _save_bgm(p: project.Project, upload: UploadFile) -> None:
    ext = Path(upload.filename or "").suffix.lower()
    if ext not in BGM_EXTS:
        raise HTTPException(400, f"지원하지 않는 음악 형식입니다: {ext}")
    if p.bgm:
        p.bgm.unlink()
    with open(p.dir / f"bgm{ext}", "wb") as f:
        shutil.copyfileobj(upload.file, f)
    p.update(bgm_file=f"bgm{ext}", bgm_name=upload.filename)


def _clean_segments(segments: list[dict]) -> list[dict]:
    out = []
    for i, s in enumerate(sorted(segments, key=lambda s: float(s["start"]))):
        start, end = float(s["start"]), float(s["end"])
        if end > start:
            out.append({"id": s.get("id") or f"s{i}", "start": start, "end": end,
                        "keep": bool(s.get("keep", True)), "zoom": float(s.get("zoom", 1.0))})
    return out


def _clean_captions(captions: list[dict]) -> list[dict]:
    out = []
    for i, c in enumerate(sorted(captions, key=lambda c: float(c["start"]))):
        text = str(c.get("text", "")).strip()
        start, end = float(c["start"]), float(c["end"])
        if text and end > start:
            out.append({"id": c.get("id") or f"c{i}", "start": start, "end": end, "text": text,
                        "words": c.get("words") or []})
    return out


@app.exception_handler(Exception)
async def _unhandled(_request, exc: Exception):  # pragma: no cover
    traceback.print_exc()
    return PlainTextResponse(str(exc), status_code=500)


app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
