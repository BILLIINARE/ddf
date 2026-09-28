from autocut import planner, presets
from autocut.planner import TimeMap
from autocut.subtitles import ass_color, ass_time, build_ass


def test_keep_ranges_pads_around_speech():
    keeps = planner.keep_ranges(10.0, [(2.0, 4.0), (8.0, 10.0)], padding=0.1)
    assert keeps == [(0.0, 2.1), (3.9, 8.1)]


def test_leading_silence_is_cut_without_padding_at_zero():
    keeps = planner.keep_ranges(5.0, [(0.0, 1.0)], padding=0.1)
    assert keeps == [(0.9, 5.0)]


def test_subtract_ranges():
    assert planner.subtract_ranges([(0, 10)], [(2, 3), (5, 6)]) == [(0, 2), (3, 5), (6, 10)]


def test_segments_cover_source_and_alternate_zoom():
    segs = planner.build_segments(10.0, 30, [(0, 2), (3, 5), (6, 8)], punch_zoom=1.2)
    assert segs[0]["start"] == 0 and segs[-1]["end"] == 10
    for a, b in zip(segs, segs[1:]):
        assert a["end"] == b["start"]
    kept = [s for s in segs if s["keep"]]
    assert [s["zoom"] for s in kept] == [1.0, 1.2, 1.0]


def test_segments_snap_to_frames_and_drop_tiny_keeps():
    segs = planner.build_segments(5.0, 30, [(0.013, 1.0), (2.0, 2.1)])
    kept = [s for s in segs if s["keep"]]
    assert len(kept) == 1
    assert abs(kept[0]["start"] * 30 - round(kept[0]["start"] * 30)) < 1e-6


def test_timemap():
    tm = TimeMap([(1.0, 3.0), (5.0, 6.0)])
    assert tm.duration == 3.0
    assert tm.to_output(2.0) == 1.0
    assert tm.to_output(4.0) == 2.0  # inside a cut -> start of next kept part
    assert tm.to_output(5.5) == 2.5
    assert tm.is_kept(1.5) and not tm.is_kept(4.0)
    assert tm.span(3.2, 4.8) is None


def test_fillers():
    assert planner.is_filler(" 음...")
    assert planner.is_filler("Um,")
    assert not planner.is_filler(" 음식")


def test_captions_chunking():
    words = [{"start": i * 0.4, "end": i * 0.4 + 0.3, "text": f" 단어{i}"} for i in range(6)]
    words.insert(2, {"start": 0.75, "end": 0.78, "text": " 음"})
    caps = planner.build_captions(words, max_chars=8)
    assert all("음" not in c["text"] for c in caps)
    assert all(len(c["text"]) <= 8 for c in caps)
    assert sum(len(c["words"]) for c in caps) == 6


def test_caption_breaks_on_sentence_end_and_tidies():
    words = [{"start": 0, "end": 0.3, "text": " 좋아요."}, {"start": 0.4, "end": 0.6, "text": " 다음"}]
    caps = planner.build_captions(words)
    assert [c["text"] for c in caps] == ["좋아요", "다음"]


def test_make_plan_removes_fillers_and_keeps_something():
    words = [{"start": 1.0, "end": 1.5, "text": " 안녕"}, {"start": 2.0, "end": 2.4, "text": " 어"}]
    plan = planner.make_plan(5.0, 30, [], words, presets.resolve("shorts"))
    cut = [s for s in plan["segments"] if not s["keep"]]
    assert any(s["start"] <= 2.1 and s["end"] >= 2.3 for s in cut)
    all_silent = planner.make_plan(5.0, 30, [(0, 5.0)], [], presets.resolve("shorts"))
    assert all_silent["segments"][0]["keep"]


def test_presets_resolve_coerces_types():
    s = presets.resolve("vlog", {"max_chars": "10", "zoom_punch": True, "unknown": 1})
    assert s["max_chars"] == 10 and s["color"] == "warm" and "unknown" not in s


def test_ass_helpers_and_word_highlight():
    assert ass_color("#FF8000") == "&H000080FF"
    assert ass_time(3661.5) == "1:01:01.50"
    words = [{"start": 0.0, "end": 0.4, "text": " 하나"}, {"start": 0.5, "end": 0.9, "text": " 둘"}]
    caps = [{"id": "c0", "start": 0.0, "end": 0.9, "text": "하나 둘", "words": words}]
    ass = build_ass(caps, TimeMap([(0.0, 1.0)]), 1080, 1920, presets.resolve("shorts", {"title": "제목"}))
    events = [l for l in ass.splitlines() if l.startswith("Dialogue")]
    assert len(events) == 3  # title + one event per word
    assert "\\c&H004DE1FF" in ass  # current word in the highlight colour
    caps[0]["text"] = "완전히 다른 문장"  # edited text -> single plain event
    ass = build_ass(caps, TimeMap([(0.0, 1.0)]), 1080, 1920, presets.resolve("shorts"))
    assert sum(l.startswith("Dialogue") for l in ass.splitlines()) == 1
