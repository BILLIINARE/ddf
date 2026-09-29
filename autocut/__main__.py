"""Command line entry point.

    python -m autocut serve [--port 8000]         # web editor (CapCut-style UI)
    python -m autocut edit input.mp4 [-o out.mp4] # fully automatic, no UI
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autocut")
    sub = parser.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="웹 편집기 실행")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)

    e = sub.add_parser("edit", help="영상 하나를 자동 편집")
    e.add_argument("input")
    e.add_argument("-o", "--output")
    e.add_argument("-p", "--preset", default="shorts", help="shorts | dynamic | vlog | interview")
    e.add_argument("--title", default="")
    e.add_argument("--bgm", help="배경음악 파일")
    e.add_argument("--aspect", help="original | 9:16-crop | 9:16-blur | 1:1")
    e.add_argument("--no-captions", action="store_true")
    e.add_argument("--language", default="ko", help="ko, en, ja … 또는 auto")
    args = parser.parse_args(argv)

    from . import media
    media.require_ffmpeg()

    if args.cmd == "serve":
        import uvicorn
        print(f"AutoCut 편집기: http://{args.host}:{args.port}")
        uvicorn.run("autocut.server:app", host=args.host, port=args.port)
        return 0

    from . import presets, project
    src = Path(args.input)
    overrides = {"title": args.title, "language": args.language}
    if args.aspect:
        overrides["aspect"] = args.aspect
    if args.no_captions:
        overrides["captions"] = False
    with open(src, "rb") as f:
        p = project.create(src.name, f, presets.resolve(args.preset, overrides), auto_render=True)
    if args.bgm:
        bgm = Path(args.bgm)
        shutil.copy(bgm, p.dir / f"bgm{bgm.suffix.lower()}")
        p.update(bgm_file=f"bgm{bgm.suffix.lower()}", bgm_name=bgm.name)

    last = [""]
    import json

    def show() -> None:
        d = json.loads(p.json_path.read_text(encoding="utf-8"))
        line = f"\r[{d['status']:>12}] {d['progress'] * 100:5.1f}%  {d['message']}"
        if line != last[0]:
            print(line.ljust(80), end="", flush=True)
            last[0] = line

    import threading
    t = threading.Thread(target=project.run_job, args=(p.id, "analyze", lambda m: print(m, file=sys.stderr)))
    t.start()
    while t.is_alive():
        show()
        t.join(0.5)
    show()
    print()
    p.load()
    if p.data["status"] != "done":
        print(f"실패: {p.data['message']}", file=sys.stderr)
        return 1
    if p.data.get("warning"):
        print(p.data["warning"])
    out = Path(args.output or src.with_name(src.stem + "_autocut.mp4"))
    shutil.copy(p.dir / "output.mp4", out)
    print(f"완성: {out}  ({p.data['output']['duration']:.1f}초, 원본 {p.data['media']['duration']:.1f}초)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
