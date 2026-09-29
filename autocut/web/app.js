"use strict";
/* AutoCut web editor — vanilla JS, no build step. All plan times are *source* times. */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const BUSY = new Set(["queued", "analyzing", "transcribing", "rendering"]);
const PRESET_DESC = {
  shorts: "세로 9:16 · 블러 배경 · 강조 자막 · 점프컷 줌",
  dynamic: "세로 꽉 채우기 · 짧은 컷 · 플래시 전환",
  vlog: "원본 비율 · 따뜻한 색감 · 잔잔한 자막",
  interview: "원본 비율 · 박스 자막 · 자연스러운 컷",
};
const COLOR_CSS = {
  none: "", vivid: "saturate(1.3) contrast(1.05)", warm: "sepia(.18) saturate(1.15) contrast(1.03)",
  cool: "hue-rotate(-8deg) saturate(1.05) brightness(1.02)", cinematic: "contrast(1.12) saturate(.85)",
  bw: "grayscale(1) contrast(1.15)",
};

const state = {
  presets: null, preset: "shorts", file: null,
  project: null, tab: "captions", previewMode: "edit",
  pxPerSec: 60, selectedSeg: null, selectedCap: null,
  saveTimer: null, pollTimer: null, lastCapKey: "",
};

// ------------------------------------------------------------------ utils
async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = await res.text();
    try { msg = JSON.parse(msg).detail || msg; } catch (_) { /* plain text */ }
    throw new Error(msg || res.statusText);
  }
  return res.json();
}
const jsonOpts = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

function toast(msg, isError = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.toggle("error", isError);
  t.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), isError ? 6000 : 3000);
}

function fmt(t) {
  t = Math.max(0, t || 0);
  const m = Math.floor(t / 60), s = t - m * 60;
  return `${m}:${s.toFixed(1).padStart(4, "0")}`;
}
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const norm = (s) => String(s).replace(/[^\p{L}\p{N}]/gu, "");
const uid = (p) => p + Date.now().toString(36) + Math.random().toString(36).slice(2, 5);

function showView(id) {
  for (const v of ["homeView", "processView", "editorView"]) $("#" + v).classList.toggle("hidden", v !== id);
}

// ------------------------------------------------------------------ time mapping
function keeps() { return state.project.segments.filter((s) => s.keep); }
function outDuration() { return keeps().reduce((a, s) => a + s.end - s.start, 0); }
function toOutput(t) {
  let acc = 0;
  for (const s of keeps()) {
    if (t < s.start) return acc;
    if (t < s.end) return acc + t - s.start;
    acc += s.end - s.start;
  }
  return acc;
}
function toSource(o) {
  let acc = 0;
  const ks = keeps();
  for (const s of ks) {
    const len = s.end - s.start;
    if (o <= acc + len) return s.start + (o - acc);
    acc += len;
  }
  return ks.length ? ks[ks.length - 1].end : 0;
}
const segAt = (t) => state.project.segments.find((s) => t >= s.start && t < s.end);
const isKept = (t) => { const s = segAt(t); return !!s && s.keep; };
const capKept = (c) => isKept((c.start + c.end) / 2) || toOutput(c.end) - toOutput(c.start) > 0.05;

// ------------------------------------------------------------------ home
async function initHome() {
  if (!state.presets) state.presets = await api("/api/presets");
  const grid = $("#presetGrid");
  grid.innerHTML = Object.entries(state.presets.presets).map(([k, p]) => `
    <button class="preset ${k === state.preset ? "active" : ""}" data-preset="${k}">
      <b>${esc(p.label)}</b><small>${esc(PRESET_DESC[k] || "")}</small></button>`).join("");
  loadHistory();
}

async function loadHistory() {
  const list = await api("/api/projects");
  const grid = $("#historyGrid");
  if (!list.length) { grid.innerHTML = `<p class="empty">아직 프로젝트가 없어요. 첫 영상을 올려보세요!</p>`; return; }
  const label = { done: "완성", ready: "편집 가능", error: "오류", rendering: "렌더링 중", queued: "대기", analyzing: "분석 중", transcribing: "자막 생성 중" };
  grid.innerHTML = list.map((p) => `
    <div class="history-item" data-id="${p.id}">
      <img src="/api/projects/${p.id}/thumb" alt="" onerror="this.style.visibility='hidden'">
      <div class="meta"><b>${esc(p.name || "제목 없음")}</b>
        <span>${label[p.status] || p.status}${p.media ? " · " + fmt(p.media.duration) : ""}</span></div>
      <button class="del" data-del="${p.id}" title="삭제">✕</button>
    </div>`).join("");
}

function pickFile(file) {
  if (!file) return;
  state.file = file;
  $("#dzFile").textContent = `${file.name} · ${(file.size / 1048576).toFixed(1)}MB`;
  $("#dropzone").classList.add("has-file");
  $("#startBtn").disabled = false;
}

function startUpload() {
  if (!state.file) return;
  const fd = new FormData();
  fd.append("file", state.file);
  fd.append("preset", state.preset);
  fd.append("auto_render", $("#autoRenderInput").checked ? "true" : "false");
  fd.append("settings", JSON.stringify({ title: $("#titleInput").value.trim(), language: $("#langInput").value }));
  const bgm = $("#bgmInput").files[0];
  if (bgm) fd.append("bgm", bgm);

  showView("processView");
  setProcess("upload", "업로드 중…", 0);
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/projects");
  xhr.upload.onprogress = (e) => e.lengthComputable && setProcess("upload", "업로드 중…", e.loaded / e.total);
  xhr.onload = () => {
    if (xhr.status >= 300) { showView("homeView"); toast("업로드 실패: " + xhr.responseText, true); return; }
    const p = JSON.parse(xhr.responseText);
    location.hash = `#/p/${p.id}`;
  };
  xhr.onerror = () => { showView("homeView"); toast("업로드 중 네트워크 오류가 발생했습니다.", true); };
  xhr.send(fd);
}

function setProcess(step, msg, progress) {
  $("#procStatus").textContent = msg;
  $("#procBar").style.width = `${Math.round(progress * 100)}%`;
  $("#procPct").textContent = `${Math.round(progress * 100)}%`;
  const order = ["upload", "analyzing", "transcribing", "rendering"];
  const idx = order.indexOf(step === "queued" ? "analyzing" : step);
  $$("#procSteps li").forEach((li, i) => {
    li.classList.toggle("done", i < idx);
    li.classList.toggle("active", i === idx);
  });
}

// ------------------------------------------------------------------ project loading / polling
async function openProject(id) {
  stopPolling();
  try {
    state.project = await api(`/api/projects/${id}`);
  } catch (e) {
    toast(e.message, true); location.hash = ""; return;
  }
  state.selectedSeg = state.selectedCap = null;
  const p = state.project;
  if (BUSY.has(p.status) && !p.segments.length) {
    showView("processView");
    setProcess(p.status, p.message, p.progress);
    poll();
  } else if (p.status === "error" && !p.segments.length) {
    showView("homeView");
    toast("처리 실패: " + p.message, true);
  } else {
    enterEditor();
    if (BUSY.has(p.status)) poll();
  }
}

function poll() {
  stopPolling();
  state.pollTimer = setTimeout(async () => {
    const id = state.project?.id;
    if (!id) return;
    let p;
    try { p = await api(`/api/projects/${id}`); } catch (_) { poll(); return; }
    if (state.project?.id !== id) return;
    const inEditor = !$("#editorView").classList.contains("hidden");
    if (!inEditor) {
      setProcess(p.status, p.message, p.progress);
      if (p.status === "error") { toast("처리 실패: " + p.message, true); showView("homeView"); loadHistory(); return; }
      // Analysis finished -> open the editor (keep waiting on the render there).
      if (p.segments.length) { state.project = p; enterEditor(); }
    } else {
      const wasBusy = BUSY.has(state.project.status);
      Object.assign(state.project, { status: p.status, message: p.message, progress: p.progress, output: p.output });
      renderExportPanel();
      if (wasBusy && p.status === "done") { toast("완성! 🎉"); setPreviewMode("output"); }
      if (p.status === "error") toast("렌더링 실패: " + p.message, true);
    }
    if (BUSY.has(p.status)) poll();
  }, 800);
}
function stopPolling() { clearTimeout(state.pollTimer); }

// ------------------------------------------------------------------ editor
function enterEditor() {
  showView("editorView");
  const p = state.project;
  $("#projectTitle").innerHTML = `<input type="text" id="nameInput" value="${esc(p.name || "")}">`;
  $("#nameInput").onchange = (e) => { p.name = e.target.value; scheduleSave(); };
  const v = $("#video"), bg = $("#bgVideo");
  const src = `/api/projects/${p.id}/source`;
  if (!v.src.endsWith(src)) { v.src = src; bg.src = src; }
  setPreviewMode(p.status === "done" && p.output ? "output" : "edit");
  renderTab();
  renderTimeline();
  renderExportPanel();
  applyFrameStyle();
}

function scheduleSave() {
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(save, 600);
  renderExportPanel();
}

async function save() {
  clearTimeout(state.saveTimer);
  state.saveTimer = null;
  const p = state.project;
  try {
    const res = await api(`/api/projects/${p.id}`, jsonOpts("PUT", {
      name: p.name, segments: p.segments, captions: p.captions, settings: p.settings }));
    Object.assign(p, { status: res.status, message: res.message, settings: res.settings });
    renderExportPanel();
  } catch (e) { toast("저장 실패: " + e.message, true); }
}

function setSetting(key, value, { rerenderTab = false } = {}) {
  state.project.settings[key] = value;
  applyFrameStyle();
  state.lastCapKey = "";
  if (rerenderTab) renderTab();
  scheduleSave();
}

function setPreviewMode(mode) {
  const p = state.project;
  if (mode === "output" && !(p.output && p.status !== "rendering")) mode = "edit";
  state.previewMode = mode;
  $$("#previewModeToggle button").forEach((b) => {
    b.classList.toggle("active", b.dataset.mode === mode);
    if (b.dataset.mode === "output") b.disabled = !p.output;
  });
  const out = $("#outVideo");
  $("#frame").classList.toggle("hidden", mode === "output");
  out.classList.toggle("hidden", mode !== "output");
  $(".transport").classList.toggle("hidden", mode === "output");
  if (mode === "output") {
    $("#video").pause();
    const url = `/api/projects/${p.id}/output?t=${Math.round(p.output.rendered_at || 0)}`;
    if (!out.src.endsWith(url)) out.src = url;
  } else {
    out.pause();
  }
}

// ---- frame / overlays
function outputAspect() {
  const s = state.project.settings, m = state.project.media;
  if (s.aspect === "9:16-crop" || s.aspect === "9:16-blur") return 9 / 16;
  if (s.aspect === "1:1") return 1;
  return m ? m.width / m.height : 16 / 9;
}

function applyFrameStyle() {
  const s = state.project.settings, f = $("#frame");
  f.style.setProperty("--ar", outputAspect());
  f.classList.toggle("blur", s.aspect === "9:16-blur");
  f.classList.toggle("crop", s.aspect === "9:16-crop" || s.aspect === "1:1");
  $("#video").style.filter = COLOR_CSS[s.color] || "";
  const t = $("#overlayTitle");
  t.innerHTML = s.title ? `<span style="background:${esc(s.title_color)}">${esc(s.title)}</span>` : "";
  f.style.setProperty("--hl", s.highlight_color);
  sizeOverlays();
}

function sizeOverlays() {
  const f = $("#frame"), s = state.project?.settings;
  if (!s) return;
  const w = f.clientWidth, h = f.clientHeight, vertical = outputAspect() < 1;
  const fs = Math.min(w, h) * (vertical ? 0.085 : 0.068) * (s.caption_size || 1);
  const cap = $("#overlayCaption");
  cap.style.setProperty("--fs", fs + "px");
  cap.style.fontSize = (s.caption_style === "minimal" ? fs * 0.8 : fs) + "px";
  cap.style.bottom = h * (vertical ? 0.26 : 0.08) + "px";
  cap.style.color = s.caption_color;
  $("#overlayTitle").style.fontSize = fs * 1.05 + "px";
}

function renderCaptionOverlay(t) {
  const p = state.project, s = p.settings, el = $("#overlayCaption");
  const cap = s.captions && isKept(t) ? p.captions.find((c) => t >= c.start && t < c.end) : null;
  let html = "", key = "";
  if (cap) {
    const words = (cap.words || []).filter((w) => isKept((w.start + w.end) / 2));
    const unedited = norm((cap.words || []).map((w) => w.text).join("")) === norm(cap.text);
    if (s.caption_style !== "minimal" && unedited && words.length > 1) {
      let cur = 0;
      words.forEach((w, i) => { if (t >= w.start) cur = i; });
      html = words.map((w, i) => `<span class="w ${i === cur ? "hl" : ""}">${esc(w.text.trim())}</span>`).join(" ");
      key = cap.id + ":" + cur;
    } else {
      html = esc(cap.text);
      key = cap.id + ":" + cap.text;
    }
    if (s.caption_style === "box") html = `<span class="boxed">${html}</span>`;
  }
  if (key === state.lastCapKey) return;
  const newCaption = cap && !state.lastCapKey.startsWith(cap.id + ":");
  state.lastCapKey = key;
  el.className = `overlay-caption ${s.caption_style}`;
  el.innerHTML = html;
  if (newCaption && s.caption_style === "pop") { void el.offsetWidth; el.classList.add("anim"); }
  if (cap && cap.id !== state.selectedCap && !$("#video").paused) highlightCaption(cap.id, false);
}

// ---- playback loop: skip cut segments, apply zoom, move playhead
function tick() {
  requestAnimationFrame(tick);
  const p = state.project;
  if (!p || $("#editorView").classList.contains("hidden") || state.previewMode !== "edit") return;
  const v = $("#video"), bg = $("#bgVideo");
  let t = v.currentTime;
  const seg = segAt(t);
  if (!v.paused && (!seg || !seg.keep)) {
    const next = p.segments.find((s) => s.keep && s.start > t - 0.001 && s !== seg);
    if (next) { v.currentTime = t = next.start + 0.001; } else { v.pause(); }
  }
  const zoom = seg && seg.keep && seg.zoom > 1 ? p.settings.zoom_amount : 1;
  const tf = zoom > 1 ? `scale(${zoom})` : "";
  $(".video-clip").style.transform = tf;
  if (p.settings.aspect === "9:16-blur") {
    bg.style.transform = tf;
    if (Math.abs(bg.currentTime - t) > 0.25) bg.currentTime = t;
    if (v.paused !== bg.paused) v.paused ? bg.pause() : bg.play().catch(() => {});
  }
  renderCaptionOverlay(t);
  $("#playhead").style.left = 12 + t * state.pxPerSec + "px";
  $("#timeLabel").textContent = `${fmt(toOutput(t))} / ${fmt(outDuration())}`;
  $("#playBtn").textContent = v.paused ? "▶" : "❚❚";
  if (!v.paused) {
    const sc = $("#timelineScroll"), x = 12 + t * state.pxPerSec;
    if (x < sc.scrollLeft || x > sc.scrollLeft + sc.clientWidth - 40) sc.scrollLeft = x - 60;
  }
}

function togglePlay() {
  if (state.previewMode !== "edit") return;
  const v = $("#video");
  if (v.paused) {
    if (!isKept(v.currentTime)) {
      const next = state.project.segments.find((s) => s.keep && s.start >= v.currentTime);
      v.currentTime = next ? next.start : (keeps()[0]?.start || 0);
    }
    v.play();
  } else v.pause();
}

function seek(t) {
  setPreviewMode("edit");
  const d = state.project.media?.duration || 0;
  $("#video").currentTime = Math.max(0, Math.min(d - 0.01, t));
  $("#bgVideo").currentTime = $("#video").currentTime;
}

// ------------------------------------------------------------------ timeline
function renderTimeline() {
  const p = state.project, pps = state.pxPerSec, d = p.media?.duration || 0;
  $("#timeline").style.width = d * pps + 24 + "px";
  const steps = [0.5, 1, 2, 5, 10, 15, 30, 60, 120];
  const step = steps.find((s) => s * pps >= 64) || 300;
  let ticks = "";
  for (let t = 0; t <= d; t += step) ticks += `<div class="tick" style="left:${12 + t * pps}px">${fmt(t).replace(/\.0$/, "")}</div>`;
  $("#ruler").innerHTML = ticks;

  $("#segTrack").innerHTML = p.segments.map((s) => {
    const cls = ["seg", s.keep ? "keep" : "cut", s.keep && s.zoom > 1 ? "zoomed" : "", s.id === state.selectedSeg ? "selected" : ""].join(" ");
    const label = s.keep ? (s.zoom > 1 ? "🔍" : "") : "✂";
    return `<div class="${cls}" data-seg="${s.id}" style="left:${12 + s.start * pps}px;width:${Math.max(1, (s.end - s.start) * pps - 1)}px"
      title="${fmt(s.start)}–${fmt(s.end)} ${s.keep ? "" : "(잘림)"}">${label}</div>`;
  }).join("");

  $("#capTrack").innerHTML = p.captions.map((c) => `
    <div class="cap-block ${c.id === state.selectedCap ? "active" : ""} ${capKept(c) ? "" : "cut"}" data-cap="${c.id}"
      style="left:${12 + c.start * pps}px;width:${Math.max(2, (c.end - c.start) * pps - 1)}px">${esc(c.text)}</div>`).join("");
  updateToolButtons();
}

function updateToolButtons() {
  const seg = state.project.segments.find((s) => s.id === state.selectedSeg);
  $("#toggleKeepBtn").disabled = !seg;
  $("#toggleZoomBtn").disabled = !seg || !seg.keep;
  $("#toggleKeepBtn").textContent = seg && !seg.keep ? "↩ 구간 복원" : "🗑 구간 삭제";
}

function timelineTime(e) {
  const rect = $("#timeline").getBoundingClientRect();
  return Math.max(0, (e.clientX - rect.left - 12) / state.pxPerSec);
}

function splitAtPlayhead() {
  const p = state.project, fps = p.media?.fps || 30;
  const t = Math.round($("#video").currentTime * fps) / fps;
  const seg = segAt(t);
  if (!seg || t - seg.start < 0.1 || seg.end - t < 0.1) { toast("구간 끝에 너무 가까워서 자를 수 없어요."); return; }
  const idx = p.segments.indexOf(seg);
  const right = { ...seg, id: uid("s"), start: t };
  seg.end = t;
  p.segments.splice(idx + 1, 0, right);
  state.selectedSeg = right.id;
  renderTimeline();
  scheduleSave();
}

function toggleKeep() {
  const seg = state.project.segments.find((s) => s.id === state.selectedSeg);
  if (!seg) return;
  seg.keep = !seg.keep;
  if (!keeps().length) { seg.keep = true; toast("최소 한 구간은 남겨야 해요."); return; }
  state.lastCapKey = "";
  renderTimeline();
  if (state.tab === "captions") renderTab();
  scheduleSave();
}

function toggleZoom() {
  const seg = state.project.segments.find((s) => s.id === state.selectedSeg);
  if (!seg || !seg.keep) return;
  seg.zoom = seg.zoom > 1 ? 1 : state.project.settings.zoom_amount;
  renderTimeline();
  scheduleSave();
}

/** Same rule as the planner: kept segments right after a cut alternate normal / punch-in. */
function applyAutoZoom(on) {
  let zoomNext = false;
  for (const s of state.project.segments) {
    if (!s.keep) { zoomNext = !zoomNext; continue; }
    s.zoom = on && zoomNext ? state.project.settings.zoom_amount : 1;
  }
  renderTimeline();
}

function addCaption() {
  const p = state.project, t = $("#video").currentTime;
  const c = { id: uid("c"), start: t, end: Math.min(t + 1.5, p.media.duration), text: "새 자막", words: [] };
  p.captions.push(c);
  p.captions.sort((a, b) => a.start - b.start);
  state.selectedCap = c.id;
  state.tab = "captions";
  renderTab();
  renderTimeline();
  scheduleSave();
  const ta = $(`.cap-item[data-cap="${c.id}"] textarea`);
  if (ta) { ta.focus(); ta.select(); }
}

function highlightCaption(id, scroll = true) {
  state.selectedCap = id;
  $$(".cap-block").forEach((b) => b.classList.toggle("active", b.dataset.cap === id));
  $$(".cap-item").forEach((b) => b.classList.toggle("active", b.dataset.cap === id));
  const item = $(`.cap-item[data-cap="${id}"]`);
  if (item) item.scrollIntoView({ block: scroll ? "center" : "nearest", behavior: "smooth" });
}

// ------------------------------------------------------------------ sidebar tabs
function chips(key, options) {
  const cur = state.project.settings[key];
  return `<div class="chips" data-chips="${key}">${options.map(([v, l]) =>
    `<button class="chip ${String(cur) === String(v) ? "active" : ""}" data-value="${v}">${l}</button>`).join("")}</div>`;
}
function range(key, min, max, step, unit = "") {
  const v = state.project.settings[key];
  return `<div class="range-row"><input type="range" data-range="${key}" min="${min}" max="${max}" step="${step}" value="${v}">
    <output>${v}${unit}</output></div>`;
}
function check(key, label) {
  return `<label class="check"><input type="checkbox" data-check="${key}" ${state.project.settings[key] ? "checked" : ""}> ${label}</label>`;
}

function renderTab() {
  $$("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === state.tab));
  const p = state.project, s = p.settings, body = $("#tabBody");
  if (state.tab === "captions") {
    body.innerHTML = `
      ${p.warning ? `<div class="warn">${esc(p.warning)}</div>` : ""}
      <div class="group">
        ${check("captions", "자막 표시")}
        <h3>자막 스타일</h3>
        ${chips("caption_style", [["pop", "팝 (강조)"], ["box", "박스"], ["minimal", "미니멀"]])}
        <h3>크기</h3>${range("caption_size", 0.6, 1.6, 0.05, "x")}
        <div class="row">
          <label class="field"><span>글자색</span><input type="color" data-color="caption_color" value="${s.caption_color}"></label>
          <label class="field"><span>강조색</span><input type="color" data-color="highlight_color" value="${s.highlight_color}"></label>
        </div>
      </div>
      <div class="group">
        <div class="row"><h3>자막 ${p.captions.length}개</h3>
          <button class="btn small" id="addCapBtn2" style="flex:none">＋ 재생 위치에 추가</button></div>
        <div class="cap-list">${p.captions.map(capItem).join("") || `<p class="note">자막이 없어요. 타임라인 위치를 정하고 추가해보세요.</p>`}</div>
      </div>`;
  } else if (state.tab === "effects") {
    body.innerHTML = `
      <div class="group"><h3>화면 비율</h3>
        ${chips("aspect", [["9:16-blur", "9:16 블러 배경"], ["9:16-crop", "9:16 꽉 채우기"], ["1:1", "1:1"], ["original", "원본"]])}</div>
      <div class="group"><h3>필터</h3>
        ${chips("color", [["none", "없음"], ["vivid", "선명하게"], ["warm", "따뜻하게"], ["cool", "시원하게"], ["cinematic", "시네마틱"], ["bw", "흑백"]])}</div>
      <div class="group"><h3>점프컷 줌</h3>
        ${check("zoom_punch", "컷마다 번갈아 줌인 (자동)")}
        <span class="note">줌 배율</span>${range("zoom_amount", 1.02, 1.4, 0.01, "x")}
        ${check("flash", "컷 전환 시 흰색 플래시")}</div>
      <div class="group"><h3>상단 제목</h3>
        <input type="text" id="titleEdit" value="${esc(s.title)}" placeholder="영상 위쪽에 계속 떠있는 제목" maxlength="40">
        <div class="row"><label class="field"><span>제목 배경색</span><input type="color" data-color="title_color" value="${s.title_color}"></label></div>
      </div>`;
  } else if (state.tab === "audio") {
    body.innerHTML = `
      <div class="group"><h3>목소리</h3>${check("normalize_audio", "음량 자동 맞춤 (쇼츠 표준 -14 LUFS)")}</div>
      <div class="group"><h3>배경음악</h3>
        ${p.bgm_name ? `<div class="row"><span>🎵 ${esc(p.bgm_name)}</span><button class="btn small danger" id="bgmDel" style="flex:none">삭제</button></div>` : `<p class="note">음악을 넣으면 말할 때 자동으로 작아져요 (덕킹).</p>`}
        <input type="file" id="bgmUpload" accept="audio/*">
        <span class="note">음악 볼륨</span>${range("bgm_volume", 0, 0.6, 0.01)}</div>`;
  } else {
    const cutCount = p.segments.filter((x) => !x.keep).length;
    body.innerHTML = `
      <div class="group"><h3>결과</h3>
        <div class="stat">원본 <b>${fmt(p.media?.duration)}</b></div>
        <div class="stat">편집본 <b>${fmt(outDuration())}</b></div>
        <div class="stat">잘라낸 구간 <b>${cutCount}개</b></div></div>
      <div class="group"><h3>자동 컷 설정</h3>
        ${check("cut_silence", "무음 구간 자르기")}
        <span class="note">무음 기준 (낮을수록 더 조용해야 무음으로 판단)</span>${range("silence_db", -55, -20, 1, "dB")}
        <span class="note">최소 무음 길이</span>${range("min_silence", 0.2, 2, 0.05, "s")}
        <span class="note">말 앞뒤 여유</span>${range("padding", 0, 0.4, 0.01, "s")}
        ${check("remove_fillers", '"음", "어" 같은 군말 자르기')}
        <span class="note">자막 한 줄 최대 글자</span>${range("max_chars", 6, 30, 1)}
        <button class="btn primary block" id="replanBtn">이 설정으로 다시 자동 편집</button>
        <p class="note">⚠ 직접 수정한 컷과 자막은 초기화돼요.</p></div>
      <div class="group"><button class="btn block" id="reanalyzeBtn">음성 인식부터 다시 하기</button></div>`;
  }
}

function capItem(c) {
  const kept = capKept(c);
  return `<div class="cap-item ${c.id === state.selectedCap ? "active" : ""} ${kept ? "" : "cut"}" data-cap="${c.id}">
    <div class="times">
      <input type="number" step="0.1" min="0" data-t="start" value="${toOutput(c.start).toFixed(1)}"> –
      <input type="number" step="0.1" min="0" data-t="end" value="${toOutput(c.end).toFixed(1)}">
      ${kept ? "" : "<span>(잘린 구간)</span>"}</div>
    <button class="x" data-del-cap="${c.id}" title="삭제">✕</button>
    <textarea rows="1">${esc(c.text)}</textarea></div>`;
}

function renderExportPanel() {
  const p = state.project, el = $("#exportPanel");
  if (!p) return;
  const busy = BUSY.has(p.status);
  const dirty = !!state.saveTimer;
  const stale = p.output && p.status !== "done";
  el.innerHTML = `
    <h3>내보내기</h3>
    <div class="stat">원본 <b>${fmt(p.media?.duration)}</b></div>
    <div class="stat">편집본 <b>${fmt(outDuration())}</b></div>
    <div class="stat">자막 <b>${p.captions.length}개</b></div>
    ${busy ? `<div class="export-progress"><span class="note">${esc(p.message)}</span>
        <div class="progress"><div class="progress-bar" style="width:${Math.round(p.progress * 100)}%"></div></div>
        <span class="note">${Math.round(p.progress * 100)}%</span></div>`
      : `<button class="btn primary block" id="renderBtn" ${dirty ? "disabled" : ""}>${p.output ? "다시 내보내기" : "영상 내보내기"}</button>`}
    ${p.status === "error" ? `<div class="warn">${esc(p.message)}</div>` : ""}
    ${p.output && !busy ? `
      ${stale ? `<p class="note">수정 사항이 있어요. 다시 내보내면 반영돼요.</p>` : ""}
      <a class="btn block" href="/api/projects/${p.id}/output?download=1" download>⬇ MP4 다운로드</a>
      <a class="btn block" href="/api/projects/${p.id}/captions.srt" download>⬇ 자막 SRT</a>` : ""}`;
}

// ------------------------------------------------------------------ events
function bindEvents() {
  window.addEventListener("hashchange", route);
  $("#homeLink").onclick = (e) => { e.preventDefault(); location.hash = ""; };

  // home
  $("#fileInput").onchange = (e) => pickFile(e.target.files[0]);
  const dz = $("#dropzone");
  dz.ondragover = (e) => { e.preventDefault(); dz.classList.add("over"); };
  dz.ondragleave = () => dz.classList.remove("over");
  dz.ondrop = (e) => { e.preventDefault(); dz.classList.remove("over"); pickFile(e.dataTransfer.files[0]); };
  $("#presetGrid").onclick = (e) => {
    const b = e.target.closest("[data-preset]");
    if (!b) return;
    state.preset = b.dataset.preset;
    $$(".preset").forEach((x) => x.classList.toggle("active", x === b));
  };
  $("#startBtn").onclick = startUpload;
  $("#historyGrid").onclick = async (e) => {
    const del = e.target.closest("[data-del]");
    if (del) {
      e.stopPropagation();
      if (!confirm("이 프로젝트를 삭제할까요?")) return;
      try { await api(`/api/projects/${del.dataset.del}`, { method: "DELETE" }); loadHistory(); } catch (err) { toast(err.message, true); }
      return;
    }
    const item = e.target.closest("[data-id]");
    if (item) location.hash = `#/p/${item.dataset.id}`;
  };

  // editor: tabs + settings
  $("#tabs").onclick = (e) => { const b = e.target.closest("[data-tab]"); if (b) { state.tab = b.dataset.tab; renderTab(); } };
  const body = $("#tabBody");
  body.addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (chip) {
      const key = chip.parentElement.dataset.chips;
      setSetting(key, chip.dataset.value, { rerenderTab: true });
      return;
    }
    const del = e.target.closest("[data-del-cap]");
    if (del) {
      state.project.captions = state.project.captions.filter((c) => c.id !== del.dataset.delCap);
      renderTab(); renderTimeline(); scheduleSave(); state.lastCapKey = "";
      return;
    }
    const item = e.target.closest(".cap-item");
    if (item && !e.target.matches("input, textarea")) {
      const c = state.project.captions.find((x) => x.id === item.dataset.cap);
      if (c) { seek(c.start + 0.01); highlightCaption(c.id, false); }
    }
    if (e.target.id === "addCapBtn2") addCaption();
    if (e.target.id === "replanBtn") replan();
    if (e.target.id === "reanalyzeBtn") reanalyze();
    if (e.target.id === "bgmDel") removeBgm();
  });
  body.addEventListener("input", (e) => {
    const t = e.target;
    if (t.dataset.range) {
      const v = parseFloat(t.value);
      t.nextElementSibling.textContent = t.value + (t.nextElementSibling.textContent.match(/[a-zA-Z]+$/)?.[0] || "");
      state.project.settings[t.dataset.range] = v;
      applyFrameStyle();
      if (t.dataset.range === "zoom_amount") state.project.segments.forEach((s) => { if (s.zoom > 1) s.zoom = v; });
      scheduleSave();
    } else if (t.dataset.color) {
      setSetting(t.dataset.color, t.value);
    } else if (t.id === "titleEdit") {
      setSetting("title", t.value);
    } else if (t.matches(".cap-item textarea")) {
      const c = state.project.captions.find((x) => x.id === t.closest(".cap-item").dataset.cap);
      c.text = t.value;
      state.lastCapKey = "";
      const block = $(`.cap-block[data-cap="${c.id}"]`);
      if (block) block.textContent = c.text;
      scheduleSave();
    }
  });
  body.addEventListener("change", (e) => {
    const t = e.target;
    if (t.dataset.check) {
      setSetting(t.dataset.check, t.checked);
      if (t.dataset.check === "zoom_punch") applyAutoZoom(t.checked);
    } else if (t.dataset.t) {
      const c = state.project.captions.find((x) => x.id === t.closest(".cap-item").dataset.cap);
      c[t.dataset.t] = toSource(parseFloat(t.value) || 0);
      if (c.end <= c.start) c.end = c.start + 0.5;
      c.words = [];  // manual timing: drop word-level highlight
      state.project.captions.sort((a, b) => a.start - b.start);
      renderTimeline(); scheduleSave(); state.lastCapKey = "";
    } else if (t.id === "bgmUpload" && t.files[0]) {
      uploadBgm(t.files[0]);
    }
  });

  // player
  $("#playBtn").onclick = togglePlay;
  $("#video").onclick = togglePlay;
  $("#frame").onclick = (e) => { if (e.target.id !== "video") togglePlay(); };
  $("#previewModeToggle").onclick = (e) => { const b = e.target.closest("[data-mode]"); if (b && !b.disabled) setPreviewMode(b.dataset.mode); };
  $("#video").onloadedmetadata = sizeOverlays;
  window.addEventListener("resize", sizeOverlays);
  new ResizeObserver(sizeOverlays).observe($("#playerWrap"));

  // timeline
  $("#pxPerSec").oninput = (e) => { state.pxPerSec = +e.target.value; renderTimeline(); };
  $("#timeline").addEventListener("click", (e) => {
    const cap = e.target.closest("[data-cap]");
    if (cap) {
      const c = state.project.captions.find((x) => x.id === cap.dataset.cap);
      if (state.tab !== "captions") { state.tab = "captions"; renderTab(); }
      seek(c.start + 0.01); highlightCaption(c.id);
      return;
    }
    const seg = e.target.closest("[data-seg]");
    state.selectedSeg = seg ? seg.dataset.seg : null;
    seek(timelineTime(e));
    renderTimeline();
  });
  $("#splitBtn").onclick = splitAtPlayhead;
  $("#toggleKeepBtn").onclick = toggleKeep;
  $("#toggleZoomBtn").onclick = toggleZoom;
  $("#addCaptionBtn").onclick = addCaption;

  // export
  $("#exportPanel").addEventListener("click", async (e) => {
    if (e.target.id !== "renderBtn") return;
    try {
      if (state.saveTimer) await save();
      state.project = { ...state.project, ...(await api(`/api/projects/${state.project.id}/render`, { method: "POST" })) };
      setPreviewMode("edit");
      renderExportPanel();
      poll();
    } catch (err) { toast(err.message, true); }
  });

  document.addEventListener("keydown", (e) => {
    if ($("#editorView").classList.contains("hidden") || e.target.matches("input, textarea, select")) return;
    if (e.code === "Space") { e.preventDefault(); togglePlay(); }
    else if (e.key === "s" || e.key === "S") splitAtPlayhead();
    else if (e.key === "Delete" || e.key === "Backspace") toggleKeep();
  });
}

async function replan() {
  if (!confirm("현재 설정으로 다시 자동 편집할까요? 직접 수정한 컷과 자막은 초기화됩니다.")) return;
  try {
    await save();
    const p = await api(`/api/projects/${state.project.id}/replan`, jsonOpts("POST", { settings: state.project.settings }));
    state.project = p;
    state.selectedSeg = state.selectedCap = null;
    state.lastCapKey = "";
    renderTab(); renderTimeline(); renderExportPanel();
    toast("다시 자동 편집했어요.");
  } catch (e) { toast(e.message, true); }
}

async function reanalyze() {
  if (!confirm("음성 인식부터 다시 할까요? 직접 수정한 내용은 초기화됩니다.")) return;
  try {
    await save();
    await api(`/api/projects/${state.project.id}/analyze`, { method: "POST" });
    state.project.segments = [];
    showView("processView");
    setProcess("analyzing", "다시 분석 중…", 0);
    poll();
  } catch (e) { toast(e.message, true); }
}

async function uploadBgm(file) {
  const fd = new FormData();
  fd.append("file", file);
  try {
    const p = await api(`/api/projects/${state.project.id}/bgm`, { method: "POST", body: fd });
    Object.assign(state.project, { bgm_file: p.bgm_file, bgm_name: p.bgm_name });
    renderTab();
    toast("배경음악을 넣었어요. 내보내기에 반영돼요.");
  } catch (e) { toast(e.message, true); }
}

async function removeBgm() {
  try {
    await api(`/api/projects/${state.project.id}/bgm`, { method: "DELETE" });
    Object.assign(state.project, { bgm_file: null, bgm_name: null });
    renderTab();
  } catch (e) { toast(e.message, true); }
}

// ------------------------------------------------------------------ router
function route() {
  const m = location.hash.match(/^#\/p\/(\w+)/);
  if (m) { openProject(m[1]); return; }
  stopPolling();
  $("#video").pause();
  $("#outVideo").pause();
  state.project = null;
  $("#projectTitle").innerHTML = "";
  showView("homeView");
  initHome().catch((e) => toast(e.message, true));
}

bindEvents();
route();
requestAnimationFrame(tick);
