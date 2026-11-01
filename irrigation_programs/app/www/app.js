/* Irrigation Programs – editor and live view (plain JS, no build step). */
"use strict";

const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const DAY_LABEL = { mon: "Mo", tue: "Tu", wed: "We", thu: "Th", fri: "Fr", sat: "Sa", sun: "Su" };
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MODES = [
  ["once", "Once at set times"],
  ["until", "Loop until a set time"],
  ["loop", "Loop constantly"],
];

const S = {
  tab: "live",
  saved: null,        // config as last saved
  draft: null,        // config being edited
  entities: [],
  setup: null,
  live: null,
  editing: null,       // program open in the editor
  issues: { errors: [], warnings: [] },
  issuesOpen: true,
  savedNotes: [],     // warnings shown after a save
  blockFilter: "",
};

/* ------------------------------------------------------------ helpers */
const $ = (sel) => document.querySelector(sel);
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else if (k === "checked") el.checked = !!v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const clone = (o) => JSON.parse(JSON.stringify(o));
const rid = (p) => p + "_" + Math.random().toString(36).slice(2, 8).replace(/[^a-z0-9]/g, "x");
const byId = (list) => Object.fromEntries(list.map((x) => [x.id, x]));
const dirty = () => S.saved && JSON.stringify(S.saved) !== JSON.stringify(S.draft);

async function api(method, path, body) {
  const r = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  const t = r.headers.get("content-type") || "";
  return t.includes("json") ? r.json() : r.text();
}

function toast(msg, ms = 3500) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast.t);
  toast.t = setTimeout(() => (t.hidden = true), ms);
}

function modal(build) {
  const bg = $("#modal"), box = $("#modal-box");
  box.innerHTML = "";
  return new Promise((resolve) => {
    const close = (v) => { bg.hidden = true; resolve(v); };
    build(box, close);
    bg.hidden = false;
    bg.onclick = (e) => { if (e.target === bg) close(null); };
  });
}

function confirmBox(title, text, okLabel = "Continue", danger = false) {
  return modal((box, close) => {
    box.append(
      h("h2", {}, title),
      h("div", { class: "body" }, ...[].concat(text).map((t) => h("p", {}, t))),
      h("div", { class: "foot" },
        h("button", { class: "btn ghost", onclick: () => close(false) }, "Cancel"),
        h("button", { class: "btn " + (danger ? "danger" : "primary"), onclick: () => close(true) }, okLabel)),
    );
  });
}

function fmtDur(sec) {
  sec = Math.max(0, Math.round(sec));
  const hh = Math.floor(sec / 3600), mm = Math.floor((sec % 3600) / 60), ss = sec % 60;
  if (hh) return `${hh}h ${String(mm).padStart(2, "0")}m`;
  if (mm) return `${mm}m ${String(ss).padStart(2, "0")}s`;
  return `${ss}s`;
}
function clock(ts) {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}
function fmtWhen(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000), now = new Date();
  const time = clock(ts);
  if (d.toDateString() === now.toDateString()) return `today ${time}`;
  if (new Date(now.getTime() + 86400000).toDateString() === d.toDateString()) return `tomorrow ${time}`;
  return d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" }) + " " + time;
}
const tmin = (t) => { const [a, b] = (t || "0:0").split(":").map(Number); return a * 60 + b; };
const addMin = (t, m) => { const x = ((tmin(t) + Math.round(m)) % 1440 + 1440) % 1440; return `${String(Math.floor(x / 60)).padStart(2, "0")}:${String(x % 60).padStart(2, "0")}`; };

/* model mirrors for summaries (the server does the real checks) */
function mlOf(p) { return S.draft.mainlines.find((m) => m.id === p.mainline); }
function stepOverlap(p, st) {
  if (st.overlap_s !== null && st.overlap_s !== undefined && st.overlap_s !== "") return +st.overlap_s;
  return +(mlOf(p)?.overlap_s || 0);
}
function plan(p) {
  let t = 0;
  return p.steps.map((st, i) => {
    const d = Math.round((+st.duration_min || 0) * 60);
    let o = stepOverlap(p, st);
    if (i === p.steps.length - 1 && p.mode === "once") o = 0;
    const r = { offset: t, duration: d, overlap: o };
    t += d - o;
    return r;
  });
}
const loopPeriod = (p) => plan(p).reduce((a, x) => a + x.duration - x.overlap, 0);
const runLength = (p) => { const pl = plan(p); return pl.length ? pl[pl.length - 1].offset + pl[pl.length - 1].duration : 0; };
function windowSec(w) { let x = (tmin(w.end) - tmin(w.start)) % 1440; if (x < 0) x += 1440; return (x || 1440) * 60; }
function modeLabel(p) {
  if (p.mode === "once") return p.starts?.length ? p.starts.map((s) => s.time).join(", ") : "manual only";
  if (p.mode === "until") return `${p.window.start}–${p.window.end}`;
  return "nonstop";
}

/* ------------------------------------------------------------ loading */
async function loadData() {
  const d = await api("GET", "api/data");
  S.saved = d.config;
  S.draft = clone(d.config);
  S.entities = d.entities;
  S.setup = d.setup;
}

function setTab(tab) {
  S.tab = tab;
  if (tab !== "programs") S.editing = null;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
  document.querySelectorAll(".view").forEach((v) => (v.hidden = v.id !== "view-" + tab));
  render();
  window.scrollTo(0, 0);
}

function render() {
  ({ live: renderLive, programs: renderPrograms, blocks: renderBlocks, mainlines: renderMainlines })[S.tab]();
  renderSavebar();
  renderNotes();
}

let validateTimer = null;
function changed(rerender = true) {
  if (rerender) render(); else renderSavebar();
  clearTimeout(validateTimer);
  validateTimer = setTimeout(async () => {
    try { S.issues = await api("POST", "api/validate", S.draft); } catch (e) { /* ignore */ }
    renderSavebar();
  }, 350);
}

/* ------------------------------------------------------------ save bar + notes */
function renderSavebar() {
  const bar = $("#savebar");
  bar.hidden = !dirty();
  if (bar.hidden) return;
  const { errors, warnings } = S.issues;
  const tog = $("#issues-toggle");
  const n = errors.length + warnings.length;
  tog.hidden = n === 0;
  tog.className = "link " + (errors.length ? "err" : "warn");
  const parts = [];
  if (errors.length) parts.push(`${errors.length} problem${errors.length > 1 ? "s" : ""} to fix before saving`);
  if (warnings.length) parts.push(`${warnings.length} warning${warnings.length > 1 ? "s" : ""}`);
  tog.textContent = parts.join(", ") + (S.issuesOpen ? " ▾" : " ▸");
  const ul = $("#issues");
  ul.hidden = !S.issuesOpen || n === 0;
  ul.innerHTML = "";
  errors.forEach((e) => ul.append(h("li", { class: "err" }, h("b", {}, "Problem: "), e)));
  warnings.forEach((w) => ul.append(h("li", { class: "warn" }, h("b", {}, "Warning: "), w)));
  $("#save").disabled = errors.length > 0;
}

function renderNotes() {
  const box = $("#notes");
  box.innerHTML = "";
  if (!S.savedNotes.length) return;
  box.append(h("div", { class: "notice" },
    h("div", { class: "row", style: "justify-content:space-between;align-items:flex-start;gap:8px" },
      h("div", {}, h("b", {}, "Saved, with notes:"),
        h("ul", { style: "margin:6px 0 0;padding-left:18px" }, S.savedNotes.map((w) => h("li", {}, w)))),
      h("button", { class: "icon-btn", title: "Dismiss", onclick: () => { S.savedNotes = []; renderNotes(); } }, "✕"))));
}

async function save() {
  const btn = $("#save");
  btn.disabled = true;
  btn.textContent = "Saving…";
  try {
    const r = await api("PUT", "api/config", { config: S.draft });
    if (!r.ok) {
      S.issues = { errors: r.errors || ["Could not save."], warnings: r.warnings || [] };
      S.issuesOpen = true;
      renderSavebar();
      return;
    }
    S.saved = clone(S.draft);
    S.issues = { errors: [], warnings: [] };
    S.savedNotes = [...(r.warnings || [])];
    if (r.running?.length) S.savedNotes.push(`Already running (${r.running.join(", ")}): they finish with the settings they started with. New settings apply from their next start.`);
    toast("Saved");
    render();
  } catch (e) {
    toast("Save failed: " + e.message, 6000);
  } finally {
    btn.disabled = false;
    btn.textContent = "Save";
  }
}

/* ------------------------------------------------------------ live data */
let liveTimer = null;
async function pollLive() {
  clearTimeout(liveTimer);
  try { S.live = await api("GET", "api/live"); } catch (e) { S.live = { error: e.message, ha_ok: false, mainlines: [], blocks: [], events: [], activity: [] }; }
  renderActivity();
  if (S.tab === "live") renderLive();
  if (S.tab === "blocks") drawBlockTable();
  if (S.tab === "programs" && !S.editing) drawProgramTable();
  liveTimer = setTimeout(pollLive, 2000);
}

function renderActivity() {
  const el = $("#activity");
  const a = S.live?.activity || [];
  if (S.live && S.live.ha_ok === false) { el.textContent = "No contact with Home Assistant"; el.className = "activity err"; return; }
  el.textContent = a.length ? a[0] : "";
  el.title = a.join("\n");
  el.className = "activity" + (a.length ? " busy" : "");
}

async function act(body, label, confirm) {
  if (confirm && !(await confirmBox(...confirm))) return;
  try {
    const r = await api("POST", "api/action", body);
    if (!r.ok) toast((r.errors || ["Failed"]).join(" "), 6000);
    else toast(r.message || label);
    setTimeout(pollLive, 300);
  } catch (e) { toast(e.message, 6000); }
}

const runningIds = () => new Map((S.live?.mainlines || []).flatMap((m) => m.runs.map((r) => [r.program_id, r.closing && r.state === "running" ? "closing" : r.state])));

/* ------------------------------------------------------------ live view */
function renderLive() {
  const v = $("#view-live");
  v.innerHTML = "";
  const cfg = S.saved;
  const L = S.live;
  if (L && L.ha_ok === false) v.append(h("div", { class: "notice err" }, "No contact with Home Assistant right now. Running programs are held where they are and carry on when it's back."));
  if (!cfg.mainlines.length) {
    v.append(h("div", { class: "card empty" },
      h("h2", {}, "Nothing set up yet"),
      h("p", {}, "Start by adding a mainline, then your blocks, then a program."),
      h("button", { class: "btn primary", onclick: () => setTab("mainlines") }, "Add a mainline")));
    return;
  }
  for (const ml of cfg.mainlines) v.append(mainlineSection(ml, (L?.mainlines || []).find((x) => x.id === ml.id)));
  if (L?.events?.length) {
    v.append(h("div", { class: "card", style: "margin-top:16px" },
      h("h3", {}, "Recent activity"),
      h("ul", { class: "events" }, L.events.slice(0, 15).map((e) =>
        h("li", { class: e.kind }, h("span", { class: "t" }, clock(e.at)), h("span", {}, e.text))))));
  }
}

function mainlineSection(ml, c) {
  const runs = c?.runs || [];
  const running = runs.filter((r) => r.state === "running").length;
  const paused = runs.filter((r) => r.state === "paused" || r.state === "pausing").length;
  const pumps = c?.pumps || [];
  const pumpOn = pumps.some((p) => p.actual);
  const sec = h("section", { class: "mlsec" + (running ? " active" : paused ? " paused" : "") });

  // header: title, state, mainline controls
  const status = running ? `${running} running` + (paused ? `, ${paused} paused` : "") : paused ? `${paused} paused` : "Idle";
  sec.append(h("header", { class: "mlsec-head" },
    h("div", { class: "mlsec-title" },
      h("h2", {}, ml.name),
      h("span", { class: "pill " + (running ? "running" : paused ? "paused" : "") }, status)),
    runs.length ? h("div", { class: "mlsec-ctrl" },
      h("button", { class: "btn small", onclick: () => act({ action: "pause_all", mainline: ml.id }, "Mainline paused") }, "Pause all"),
      h("button", { class: "btn small", onclick: () => act({ action: "resume_all", mainline: ml.id }, "Mainline resumed") }, "Resume all"),
      h("button", { class: "btn small danger", onclick: () => act({ action: "stop_all", mainline: ml.id }, "Stopping",
        ["Stop everything?", `Stop all programs on ${ml.name}?`, "Stop all", true]) }, "Stop all")) : null));

  // pump strip
  sec.append(pumps.length
    ? h("div", { class: "pump-row" },
      h("span", { class: "pump " + (pumpOn ? "on" : "") }, pumpOn ? "● Main open" : "○ Main closed"),
      h("span", { class: "muted small" }, pumps.map((p) => p.id).join(", ")),
      pumps.some((p) => p.out_of_sync) ? h("span", { class: "tag warn" }, "out of sync") : null,
      pumps.some((p) => p.held_closed) ? h("span", { class: "tag warn" }, "held closed") : null,
      h("span", { class: "grow" }),
      h("button", { class: "btn small", onclick: () => act({ action: "pump_open", mainline: ml.id }, "Sent open") }, "Open"),
      h("button", { class: "btn small", onclick: () => act({ action: "pump_close", mainline: ml.id }, "Sent close") }, "Close"))
    : h("div", { class: "pump-row muted" }, "No pump / main valve set"));

  const held = [];
  for (const r of runs) for (const st of r.steps) if (["running", "closing"].includes(st.status)) for (const b of st.blocks) if (b.held) held.push(`block ${b.number} (${r.name}, step ${st.n})`);
  if (held.length) sec.append(h("div", { class: "held-note" },
    h("b", {}, "Held closed by you: "), held.join(", "),
    ". The rest of the step keeps running. It opens again at the next step that needs it, or press Open on the Blocks page."));
  const body = h("div", { class: "mlsec-body" });
  const runsCol = h("div", { class: "runs" });
  if (runs.length) runs.forEach((r) => runsCol.append(runPanel(r)));
  else runsCol.append(h("div", { class: "muted idle-note" }, "Nothing running on this mainline."));
  const side = h("aside", { class: "mlsec-side" });
  const progs = S.saved.programs.filter((x) => x.mainline === ml.id && x.enabled !== false);
  const idle = progs.filter((p) => !runs.some((r) => r.program_id === p.id));
  side.append(h("div", { class: "label" }, "Start a program"));
  if (idle.length) {
    const sel = h("select", {}, idle.map((x) => h("option", { value: x.id }, x.name)));
    side.append(h("div", { class: "idle-start" }, sel,
      h("button", { class: "btn primary", onclick: () => act({ action: "start", program: sel.value }, "Started") }, "Start")));
  } else side.append(h("div", { class: "muted small" }, progs.length ? "All programs on this mainline are running." : "No enabled programs on this mainline."));
  side.append(h("div", { class: "label", style: "margin-top:14px" }, "Coming up"));
  if (c?.upcoming?.length) c.upcoming.forEach((u) => side.append(h("div", { class: "up-row" }, h("span", {}, u.program), h("span", { class: "muted" }, fmtWhen(u.at)))));
  else side.append(h("div", { class: "muted small" }, "Nothing scheduled."));
  body.append(runsCol, side);
  sec.append(body);
  return sec;
}

function runPanel(r) {
  const paused = r.state === "paused" || r.state === "pausing";
  const stopping = r.state === "stopping";
  const box = h("div", { class: "run " + r.state + (r.closing && r.state === "running" ? " closing" : "") });
  const modeTxt = r.source === "block" ? "Manual block run"
    : r.mode === "loop" ? `Loops nonstop${r.loop ? ` · loop ${r.loop}` : ""}`
      : r.mode === "until" ? `Loops until ${clock(r.stop_at)}${r.loop ? ` · loop ${r.loop}` : ""}`
        : "Runs once";
  const closingNow = r.state === "running" && r.closing;
  const stateTxt = { running: closingNow ? (r.steps.some((s) => s.status === "running") ? "Changing step" : "Closing") : r.finishing ? "Finishing step" : "Running", pausing: "Pausing…", paused: "Paused", stopping: "Stopping…" }[r.state];
  box.append(h("div", { class: "run-head" },
    h("div", {}, h("div", { class: "prog-name" }, r.name), h("div", { class: "mode-tag" }, modeTxt)),
    h("span", { class: "pill " + (r.state === "pausing" || stopping || closingNow ? "closing" : paused ? "paused" : "running") }, stateTxt)));
  const facts = [`Started ${clock(r.started_at)}`];
  if (r.ends_at) facts.push(`ends ${fmtWhen(r.ends_at)}`);
  else if (r.mode === "loop") facts.push("runs until you stop it");
  if (r.next_loop_at && !r.finishing) facts.push(`next loop ${clock(r.next_loop_at)}`);
  if (paused) facts.push("timer stopped while paused");
  box.append(h("div", { class: "muted facts" }, facts.join(" · ")));
  const list = h("ol", { class: "steplist" });
  for (const s of r.steps) {
    const right = s.status === "closing"
      ? h("div", { class: "st-right closing-txt" }, h("b", {}, `closing · ${fmtDur(s.left_s)}`))
      : s.status === "running"
      ? h("div", { class: "st-right" }, h("b", {}, (paused ? "⏸ " : "") + fmtDur(s.left_s) + " left"), h("div", { class: "bar" }, h("i", { style: `width:${100 * (1 - s.left_s / s.duration_s)}%` })))
      : s.status === "done" ? h("div", { class: "st-right muted" }, "✓ done")
        : s.status === "skipped" ? h("div", { class: "st-right muted" }, "won't run")
          : h("div", { class: "st-right muted" }, s.starts_at ? `${s.status === "next" ? "next · " : ""}${clock(s.starts_at)}` : (s.status === "next" ? "next" : ""));
    list.append(h("li", { class: "st " + s.status },
      h("span", { class: "st-n" }, s.n),
      h("div", { class: "st-main" },
        h("div", { class: "chips" }, s.blocks.map((b) => h("span", {
          class: "chip" + (b.held && ["running", "closing"].includes(s.status) ? " held" : s.status === "closing" ? " closing" : s.status === "running" ? " open" : ""),
          title: b.held ? "Held closed by you" : "" }, h("b", {}, b.number), b.name ? " " + b.name : "", b.held && ["running", "closing"].includes(s.status) ? " · held closed" : ""))),
        h("div", { class: "muted st-dur" }, fmtDur(s.duration_s) + (s.overlap_s ? ` · ${s.overlap_s}s overlap` : ""))),
      right));
  }
  box.append(list, h("div", { class: "ml-actions" },
    h("button", { class: "btn small", disabled: paused || stopping, onclick: () => act({ action: "skip", run: r.id }, "Skipped to the next step") }, "Skip step"),
    h("button", { class: "btn small", disabled: stopping, onclick: () => act({ action: paused ? "resume" : "pause", run: r.id }, paused ? "Resumed" : "Paused") }, paused ? "Resume" : "Pause"),
    h("button", { class: "btn small danger", disabled: stopping, onclick: () => act({ action: "stop", run: r.id }, "Stopping", ["Stop this program?", `Stop ${r.name}?`, "Stop", true]) }, "Stop")));
  return box;
}

/* ------------------------------------------------------------ programs */
function newProgram() {
  const ml = S.draft.mainlines[0];
  if (!ml) { toast("Add a mainline first"); setTab("mainlines"); return; }
  const p = {
    id: rid("p"), name: "New program", mainline: ml.id, enabled: true, mode: "once",
    starts: [{ time: "06:00", days: [...DAYS] }],
    window: { start: "18:00", end: "06:00", days: [...DAYS] }, end_behavior: "finish_step",
    season: { from: "", until: "" },
    steps: [{ blocks: [], duration_min: 60, overlap_s: null }],
  };
  S.draft.programs.push(p);
  S.editing = p.id;
  changed();
}

function progTotal(p) {
  if (!p.steps.length) return "–";
  return p.mode === "once" ? fmtDur(runLength(p)) : `${fmtDur(loopPeriod(p))} / loop`;
}

function renderPrograms() {
  const v = $("#view-programs");
  v.innerHTML = "";
  const p = S.editing && S.draft.programs.find((x) => x.id === S.editing);
  if (p) {
    const ed = h("div", { class: "card editor" });
    v.append(h("div", { class: "row", style: "margin-bottom:12px" },
      h("button", { class: "btn", onclick: () => { S.editing = null; render(); } }, "← All programs")), ed);
    programEditor(ed, p);
    return;
  }
  S.editing = null;
  v.append(h("div", { class: "page-head" },
    h("div", {}, h("h2", {}, "Programs"), h("p", { class: "sub" }, "Tick to enable or disable a program. ✎ edits it.")),
    h("button", { class: "btn primary", onclick: newProgram }, "+ New program")),
  h("div", { id: "prog-table" }));
  drawProgramTable();
}

function drawProgramTable() {
  const box = $("#prog-table");
  if (!box) return;
  box.innerHTML = "";
  const d = S.draft;
  const running = runningIds();
  if (!d.programs.length) {
    box.append(h("div", { class: "card empty" }, h("h2", {}, "No programs yet"),
      h("p", {}, "A program runs your blocks in steps: each step opens one or more blocks for a set time, then the next step starts."),
      h("button", { class: "btn primary", onclick: newProgram }, "Create a program")));
    return;
  }
  for (const ml of d.mainlines) {
    const ps = d.programs.filter((p) => p.mainline === ml.id);
    if (!ps.length) continue;
    const tb = h("tbody");
    for (const p of ps) {
      const on = p.enabled !== false;
      const run = running.has(p.id);
      const blocksUsed = new Set(p.steps.flatMap((s) => s.blocks)).size;
      const rs = running.get(p.id);
      tb.append(h("tr", { class: run ? (rs === "paused" ? "row-paused" : ["pausing", "stopping", "closing"].includes(rs) ? "row-closing" : "row-run") : on ? "" : "row-off" },
        h("td", { class: "cb", "data-label": "On" }, h("input", { type: "checkbox", checked: on, title: on ? "Enabled" : "Disabled", onchange: (e) => { p.enabled = e.target.checked; changed(); } })),
        h("td", { "data-label": "Program" }, h("div", { class: "strong" }, p.name),
          h("div", { class: "muted small" }, `${p.steps.length} step${p.steps.length === 1 ? "" : "s"} · ${blocksUsed} block${blocksUsed === 1 ? "" : "s"}`)),
        h("td", { "data-label": "When" }, h("div", {}, MODES.find((m) => m[0] === p.mode)?.[1] || p.mode), h("div", { class: "muted small" }, modeLabel(p))),
        h("td", { class: "lbl", "data-label": "Time" }, progTotal(p)),
        h("td", { "data-label": "Status" }, run ? ({ paused: h("span", { class: "state paused" }, "⏸ Paused"), pausing: h("span", { class: "state closing" }, "● Pausing"),
          stopping: h("span", { class: "state closing" }, "● Stopping"), closing: h("span", { class: "state closing" }, "● Changing step / closing") }[running.get(p.id)]
          || h("span", { class: "state open" }, "● Running")) : on ? h("span", { class: "state" }, "Enabled") : h("span", { class: "state off" }, "Disabled")),
        h("td", { class: "acts" }, h("button", { class: "btn small", onclick: () => { S.editing = p.id; render(); window.scrollTo(0, 0); } }, "✎ Edit"))));
    }
    box.append(h("div", { class: "card group-card" }, h("div", { class: "group-title" }, ml.name),
      h("table", { class: "tbl stack" }, h("thead", {}, h("tr", {}, h("th", {}, "On"), h("th", {}, "Program"), h("th", {}, "When"), h("th", {}, "Time"), h("th", {}, "Status"), h("th", {}))), tb)));
  }
}

function daysPicker(days, onChange) {
  const wrap = h("div", { class: "days" });
  DAYS.forEach((dname) => {
    wrap.append(h("button", {
      class: days.includes(dname) ? "on" : "", title: dname,
      onclick: () => {
        const i = days.indexOf(dname);
        if (i >= 0) days.splice(i, 1); else days.push(dname);
        days.sort((a, b) => DAYS.indexOf(a) - DAYS.indexOf(b));
        onChange();
      },
    }, DAY_LABEL[dname]));
  });
  return wrap;
}

function seasonPicker(p) {
  const s = p.season || (p.season = { from: "", until: "" });
  const on = !!(s.from || s.until);
  const part = (key) => {
    const [mm, dd] = (s[key] || "01-01").split("-");
    const month = h("select", { onchange: (e) => { s[key] = `${String(e.target.value).padStart(2, "0")}-${dd}`; changed(false); } },
      MONTHS.map((m, i) => h("option", { value: String(i + 1).padStart(2, "0"), selected: +mm === i + 1 }, m)));
    const day = h("input", { type: "number", class: "narrow", min: 1, max: 31, value: +dd,
      onchange: (e) => { s[key] = `${mm}-${String(Math.min(31, Math.max(1, +e.target.value || 1))).padStart(2, "0")}`; changed(false); } });
    return h("span", { class: "row", style: "gap:4px;align-items:center" }, day, month);
  };
  return h("div", { class: "section" },
    h("div", { class: "label" }, "Season"),
    h("label", { class: "check" }, h("input", { type: "checkbox", checked: on, onchange: (e) => {
      if (e.target.checked) { s.from = "09-01"; s.until = "04-30"; } else { s.from = ""; s.until = ""; }
      changed();
    } }), "Only run between two dates"),
    on ? h("div", { class: "row", style: "margin-top:8px;align-items:center" }, h("span", { class: "muted" }, "From"), part("from"), h("span", { class: "muted" }, "until"), part("until")) : null);
}

function programEditor(ed, p) {
  const d = S.draft;
  ed.append(h("div", { class: "editor-head" },
    h("input", { type: "text", class: "name", value: p.name, "aria-label": "Program name", oninput: (e) => { p.name = e.target.value; changed(false); }, onchange: () => render() }),
    h("label", { class: "field" }, "Mainline",
      h("select", { onchange: (e) => {
        const used = p.steps.flatMap((s) => s.blocks).length;
        p.mainline = e.target.value;
        if (used) { p.steps.forEach((s) => (s.blocks = [])); toast("Blocks cleared: they belong to the other mainline"); }
        changed();
      } }, d.mainlines.map((m) => h("option", { value: m.id, selected: m.id === p.mainline }, m.name)))),
    h("label", { class: "check", style: "padding-bottom:7px" }, h("input", { type: "checkbox", checked: p.enabled !== false, onchange: (e) => { p.enabled = e.target.checked; changed(); } }), "Enabled")));

  const seg = h("div", { class: "seg" });
  MODES.forEach(([m, label]) => seg.append(h("button", { class: p.mode === m ? "on" : "", onclick: () => { p.mode = m; changed(); } }, label)));
  const modeSec = h("div", { class: "section" }, h("div", { class: "label" }, "Run mode"), seg);
  if (p.mode === "once") {
    const starts = h("div", { class: "starts", style: "margin-top:12px" });
    (p.starts || (p.starts = [])).forEach((s, i) => {
      starts.append(h("div", { class: "start-row" },
        h("input", { type: "time", value: s.time, onchange: (e) => { s.time = e.target.value; changed(); } }),
        daysPicker(s.days, () => changed()),
        h("button", { class: "icon-btn del", title: "Remove start time", onclick: () => { p.starts.splice(i, 1); changed(); } }, "✕")));
    });
    if (!p.starts.length) starts.append(h("div", { class: "muted" }, "No start times: this program only runs when you press Start."));
    starts.append(h("div", {}, h("button", { class: "btn small", onclick: () => { p.starts.push({ time: "06:00", days: [...DAYS] }); changed(); } }, "+ Add start time")));
    modeSec.append(starts);
  } else if (p.mode === "until") {
    const w = p.window || (p.window = { start: "18:00", end: "06:00", days: [...DAYS] });
    p.end_behavior = p.end_behavior || "finish_step";
    modeSec.append(
      h("div", { class: "start-row", style: "margin-top:12px" },
        h("label", { class: "field" }, "Start", h("input", { type: "time", value: w.start, onchange: (e) => { w.start = e.target.value; changed(); } })),
        h("label", { class: "field" }, "Stop", h("input", { type: "time", value: w.end, onchange: (e) => { w.end = e.target.value; changed(); } })),
        h("div", { class: "field" }, "Days", daysPicker(w.days, () => changed()))),
      h("div", { style: "margin-top:12px" }, h("div", { class: "muted", style: "font-size:13px;margin-bottom:4px" }, "At the stop time"),
        h("label", { class: "check", style: "margin-right:18px" }, h("input", { type: "radio", name: "endb", checked: p.end_behavior === "finish_step", onchange: () => { p.end_behavior = "finish_step"; changed(false); } }), "Finish the running step, then stop"),
        h("label", { class: "check" }, h("input", { type: "radio", name: "endb", checked: p.end_behavior === "stop_now", onchange: () => { p.end_behavior = "stop_now"; changed(false); } }), "Stop immediately")));
  } else {
    modeSec.append(h("p", { class: "muted", style: "margin:10px 0 0" },
      "Runs the steps over and over, from when you press Start on the Live page until you press Stop. After a restart it carries on where it was."));
  }
  ed.append(modeSec, stepsSection(p), seasonPicker(p));
  ed.append(h("div", { class: "row", style: "justify-content:space-between;margin-top:6px" },
    h("button", { class: "btn", onclick: () => {
      const c = clone(p); c.id = rid("p"); c.name = p.name + " (copy)";
      d.programs.splice(d.programs.indexOf(p) + 1, 0, c); S.editing = c.id; changed();
    } }, "Duplicate"),
    h("button", { class: "btn danger", onclick: async () => {
      if (!(await confirmBox("Delete program?", `Delete “${p.name}”? This takes effect when you save.`, "Delete", true))) return;
      d.programs.splice(d.programs.indexOf(p), 1);
      S.editing = null; changed();
    } }, "Delete program")));
}

function stepsSection(p) {
  const blocks = byId(S.draft.blocks);
  const ml = mlOf(p);
  const sec = h("div", { class: "section" }, h("div", { class: "label" }, "Steps"));
  const tb = h("tbody");
  p.steps.forEach((st, i) => {
    const isLast = i === p.steps.length - 1;
    const hh = Math.floor((+st.duration_min || 0) / 60), mm = Math.round((+st.duration_min || 0) % 60);
    const setDur = (H, M) => { st.duration_min = Math.max(0, (+H || 0) * 60 + (+M || 0)); changed(false); renderSummary(); };
    const hIn = h("input", { type: "number", class: "narrow", min: 0, value: hh, "aria-label": "hours", oninput: (e) => setDur(e.target.value, mIn.value) });
    const mIn = h("input", { type: "number", class: "narrow", min: 0, max: 59, value: mm, "aria-label": "minutes", oninput: (e) => setDur(hIn.value, e.target.value) });
    const chips = st.blocks.length
      ? h("div", { class: "chips" }, st.blocks.map((b) => blocks[b]
        ? h("span", { class: "chip" }, h("b", {}, blocks[b].number), blocks[b].name ? " " + blocks[b].name : "")
        : h("span", { class: "chip" }, "deleted")))
      : h("span", { class: "add-blocks" }, "+ Choose blocks");
    let ovl;
    if (isLast && p.mode === "once") ovl = h("span", { class: "muted" }, "—");
    else {
      const usesDefault = st.overlap_s === null || st.overlap_s === undefined || st.overlap_s === "";
      ovl = h("div", {},
        h("span", { class: "dur" }, h("input", { type: "number", class: "num", min: 0, step: 5,
          placeholder: String(ml?.overlap_s ?? 0), value: st.overlap_s ?? "",
          oninput: (e) => { st.overlap_s = e.target.value === "" ? null : +e.target.value; changed(false); renderSummary(); } }), h("span", {}, "s")),
        h("div", { class: "ovl-hint" }, [usesDefault ? "mainline default" : "", isLast ? "into next loop" : ""].filter(Boolean).join(" · ")));
    }
    tb.append(h("tr", {},
      h("td", { class: "n" }, i + 1),
      h("td", { class: "blocks", onclick: () => pickBlocks(p, st) }, chips),
      h("td", {}, h("span", { class: "dur" }, hIn, h("span", {}, "h"), mIn, h("span", {}, "m"))),
      h("td", {}, ovl),
      h("td", { class: "acts" },
        h("button", { class: "icon-btn", title: "Move up", disabled: i === 0, onclick: () => { p.steps.splice(i - 1, 0, p.steps.splice(i, 1)[0]); changed(); } }, "↑"),
        h("button", { class: "icon-btn", title: "Move down", disabled: isLast, onclick: () => { p.steps.splice(i + 1, 0, p.steps.splice(i, 1)[0]); changed(); } }, "↓"),
        h("button", { class: "icon-btn", title: "Duplicate step", onclick: () => { p.steps.splice(i + 1, 0, clone(st)); changed(); } }, "⧉"),
        h("button", { class: "icon-btn del", title: "Delete step", onclick: () => { p.steps.splice(i, 1); changed(); } }, "✕"))));
  });
  sec.append(h("div", { class: "table-wrap" }, h("table", { class: "steps stack" },
    h("thead", {}, h("tr", {}, h("th", {}, "#"), h("th", {}, "Blocks open together"), h("th", {}, "Duration"), h("th", {}, "Overlap with next"), h("th", {}))), tb)),
  h("div", { style: "margin-top:8px" }, h("button", { class: "btn small", onclick: () => {
    p.steps.push({ blocks: [], duration_min: 60, overlap_s: null, fresh: true }); changed();
  } }, "+ Add step")),
  h("div", { class: "summary", id: "summary" }));
  setTimeout(renderSummary);
  return sec;
}

function renderSummary() {
  const p = S.draft.programs.find((x) => x.id === S.editing);
  const el = $("#summary");
  if (!p || !el) return;
  el.innerHTML = "";
  const item = (label, val) => h("div", {}, h("small", {}, label), h("strong", {}, val));
  el.append(item("Steps", p.steps.length), item("Blocks used", new Set(p.steps.flatMap((s) => s.blocks)).size));
  if (p.mode === "once") {
    const len = runLength(p);
    el.append(item("Total time", fmtDur(len)));
    if (p.starts?.length) el.append(item("Ends", p.starts.map((s) => `${s.time}→${addMin(s.time, len / 60)}`).join(", ")));
  } else {
    const per = loopPeriod(p);
    el.append(item("One loop", per > 0 ? fmtDur(per) : "–"));
    if (p.mode === "until" && per > 0) el.append(item("Loops in window", (windowSec(p.window) / per).toFixed(1)));
  }
}

function pickBlocks(p, st) {
  const mlBlocks = S.draft.blocks.filter((b) => b.mainline === p.mainline).sort((a, b) => a.number - b.number);
  const sel = new Set(st.blocks);
  const usedElsewhere = new Set(p.steps.filter((x) => x !== st).flatMap((x) => x.blocks));
  modal((box, close) => {
    let filter = "";
    const grid = h("div", { class: "pick-grid" });
    const draw = () => {
      grid.innerHTML = "";
      mlBlocks.filter((b) => !filter || `${b.number} ${b.name}`.toLowerCase().includes(filter)).forEach((b) => {
        grid.append(h("label", { class: "pick" + (sel.has(b.id) ? " on" : "") + (usedElsewhere.has(b.id) ? " used" : ""), title: usedElsewhere.has(b.id) ? "Also used in another step" : "" },
          h("input", { type: "checkbox", checked: sel.has(b.id), onchange: (e) => { e.target.checked ? sel.add(b.id) : sel.delete(b.id); draw(); } }),
          h("b", {}, b.number), h("small", {}, b.name || "")));
      });
      if (!mlBlocks.length) grid.append(h("p", { class: "muted" }, "This mainline has no blocks yet. Add them on the Blocks tab."));
    };
    draw();
    box.append(
      h("h2", {}, "Blocks for this step"),
      h("div", { class: "row" },
        h("input", { type: "text", placeholder: "Search blocks", style: "flex:1", oninput: (e) => { filter = e.target.value.toLowerCase(); draw(); } }),
        h("button", { class: "btn small", onclick: () => { sel.clear(); draw(); } }, "Clear")),
      h("p", { class: "muted", style: "margin:0;font-size:13px" }, "Faded blocks are already used in another step of this program."),
      h("div", { class: "body" }, grid),
      h("div", { class: "foot" },
        h("button", { class: "btn ghost", onclick: () => close() }, "Cancel"),
        h("button", { class: "btn primary", onclick: () => {
          const wasEmpty = !st.blocks.length;
          st.blocks = mlBlocks.filter((b) => sel.has(b.id)).map((b) => b.id);
          if ((wasEmpty || st.fresh) && st.blocks.length) {
            st.duration_min = Math.max(...mlBlocks.filter((b) => sel.has(b.id)).map((b) => +b.default_min || 60));
          }
          delete st.fresh;
          close(); changed();
        } }, "Done")));
  });
}

/* ------------------------------------------------------------ blocks */
function entDatalist() {
  return h("datalist", { id: "ents" }, S.entities.map((e) => h("option", { value: e.id }, e.name)));
}

function renderBlocks() {
  const v = $("#view-blocks");
  v.innerHTML = "";
  const d = S.draft;
  v.append(h("div", { class: "page-head" },
    h("div", {}, h("h2", {}, "Blocks"), h("p", { class: "sub" }, "Open and Close always send the signal, even if the valve already looks open or closed.")),
    h("div", { class: "row" },
      h("input", { type: "text", placeholder: "Filter blocks", value: S.blockFilter, oninput: (e) => { S.blockFilter = e.target.value.toLowerCase(); drawBlockTable(); } }),
      h("button", { class: "btn primary", onclick: addBlocksDialog }, "+ Add block"))));
  if (!d.mainlines.length) {
    v.append(h("div", { class: "card empty" }, h("p", {}, "Add a mainline first, then put blocks on it."),
      h("button", { class: "btn primary", onclick: () => setTab("mainlines") }, "Go to Mainlines")));
    return;
  }
  v.append(h("div", { id: "block-table" }), entDatalist());
  drawBlockTable();
}

function drawBlockTable() {
  const box = $("#block-table");
  if (!box) return;
  box.innerHTML = "";
  const d = S.draft;
  const live = byId(S.live?.blocks || []);
  const savedIds = new Set(S.saved.blocks.map((b) => b.id));
  if (!d.blocks.length) { box.append(h("div", { class: "card empty" }, "No blocks yet. Use “Add block”.")); return; }
  const groups = [...d.mainlines.map((m) => [m.id, m.name]), ["", "No mainline"]];
  for (const [mlid, mlname] of groups) {
    const list = d.blocks.filter((b) => (mlid ? b.mainline === mlid : !d.mainlines.some((m) => m.id === b.mainline)))
      .sort((a, b) => a.number - b.number)
      .filter((b) => !S.blockFilter || `${b.number} ${b.name} ${b.entity}`.toLowerCase().includes(S.blockFilter));
    if (!list.length) continue;
    const nOpen = list.filter((b) => live[b.id]?.valve?.actual).length;
    const tb = h("tbody");
    for (const b of list) {
      const lv = live[b.id];
      const valve = lv?.valve;
      let status;
      if (!b.entity) status = h("span", { class: "muted" }, "no valve set");
      else if (!savedIds.has(b.id)) status = h("span", { class: "muted" }, "save first");
      else if (!valve || valve.actual === null) status = h("span", { class: "tag warn" }, valve?.raw ? `“${valve.raw}”` : "unknown");
      else status = h("span", { class: "state " + (valve.actual ? "open" : "") }, valve.actual ? "● Open" : "○ Closed");
      if (valve?.actual && (lv?.open_by || []).length && lv.open_by.every((o) => o.closing)) status = h("span", { class: "state closing" }, "● Closing");
      const by = (lv?.open_by || []).map((o) => (o.manual ? "manual" : `${o.run} · step ${o.step}`) + (o.closing ? ` · closing in ${fmtDur(o.left_s)}` : ` · ${fmtDur(o.left_s)} left`));
      const notes = [];
      if (valve?.out_of_sync) notes.push(h("span", { class: "tag warn" }, `out of sync: should be ${valve.commanded ? "open" : "closed"}`));
      if (valve?.held_closed) notes.push(h("span", { class: "tag closing" }, "held closed by you until the next step that needs it"));
      if (valve?.actual && !valve.wanted && !valve.out_of_sync) notes.push(h("span", { class: "tag warn" }, "open, but no program needs it"));
      if (b.enabled === false) notes.push(h("span", { class: "tag" }, "disabled"));
      const canAct = b.entity && savedIds.has(b.id);
      const closing = (lv?.open_by || []).length && lv.open_by.every((o) => o.closing);
      const rowCls = valve?.held_closed ? "row-closing" : valve?.actual ? (closing ? "row-closing" : "row-run") : b.enabled === false ? "row-off" : "";
      tb.append(h("tr", { class: rowCls },
        h("td", { class: "mono num-cell", "data-label": "No." }, b.number),
        h("td", { "data-label": "Block" }, h("div", { class: "strong" }, b.name || "—"), h("div", { class: "muted small" }, b.entity || "")),
        h("td", { class: "lbl", "data-label": "Default runtime" }, fmtDur((+b.default_min || 0) * 60)),
        h("td", { "data-label": "Now" }, h("div", { class: "row", style: "gap:6px;align-items:center" }, status, ...notes), by.length ? h("div", { class: "muted small" }, by.join(" | ")) : null),
        h("td", { class: "acts" },
          h("button", { class: "btn small", disabled: !canAct, title: `Open (runs ${fmtDur((+b.default_min || 0) * 60)} if nothing else has it open)`, onclick: () => act({ action: "block_open", block: b.id }, "Sent open") }, "Open"),
          h("button", { class: "btn small", disabled: !canAct, onclick: () => act({ action: "block_close", block: b.id }, "Sent close") }, "Close"),
          h("button", { class: "btn small", title: "Edit block", onclick: () => editBlock(b) }, "✎"))));
    }
    box.append(h("div", { class: "card group-card" },
      h("div", { class: "group-title" }, mlname, h("span", { class: "muted small" }, ` · ${list.length} block${list.length === 1 ? "" : "s"}${nOpen ? ` · ${nOpen} open` : ""}`)),
      h("table", { class: "tbl stack" }, h("thead", {}, h("tr", {}, h("th", {}, "No."), h("th", {}, "Block / valve"), h("th", {}, "Default runtime"), h("th", {}, "Now"), h("th", {}))), tb)));
  }
}

function blockForm(b) {
  const d = S.draft;
  const hh = Math.floor((+b.default_min || 0) / 60), mm = Math.round((+b.default_min || 0) % 60);
  const f = {
    number: h("input", { type: "number", class: "num", min: 1, value: b.number }),
    name: h("input", { type: "text", value: b.name || "" }),
    entity: h("input", { type: "text", list: "ents", value: b.entity || "", placeholder: "switch.valve_1" }),
    mainline: h("select", {}, d.mainlines.map((m) => h("option", { value: m.id, selected: m.id === b.mainline }, m.name))),
    hours: h("input", { type: "number", class: "narrow", min: 0, value: hh }),
    mins: h("input", { type: "number", class: "narrow", min: 0, max: 59, value: mm }),
    enabled: h("input", { type: "checkbox", checked: b.enabled !== false }),
  };
  const el = h("div", { class: "form-grid" },
    h("label", { class: "field" }, "Number", f.number),
    h("label", { class: "field" }, "Name", f.name),
    h("label", { class: "field" }, "Valve switch", f.entity),
    h("label", { class: "field" }, "Mainline", f.mainline),
    h("label", { class: "field" }, "Default runtime", h("span", { class: "dur" }, f.hours, h("span", {}, "h"), f.mins, h("span", {}, "m")),
      h("span", { class: "muted", style: "font-weight:400" }, "Used when you press Open, and as the starting duration when you add the block to a new step.")),
    h("label", { class: "check" }, f.enabled, "Enabled (a disabled block is skipped by programs)"),
    entDatalist());
  const read = () => ({ number: +f.number.value, name: f.name.value.trim(), entity: f.entity.value.trim(), mainline: f.mainline.value,
    default_min: (+f.hours.value || 0) * 60 + (+f.mins.value || 0), enabled: f.enabled.checked });
  return { el, read };
}

function editBlock(b, isNew = false) {
  const d = S.draft;
  const usedIn = d.programs.filter((p) => p.steps.some((s) => s.blocks.includes(b.id))).map((p) => p.name);
  modal((box, close) => {
    const form = blockForm(b);
    box.append(h("h2", {}, isNew ? "Add a block" : `Edit block ${b.number}`), h("div", { class: "body" }, form.el),
      usedIn.length ? h("p", { class: "muted", style: "margin:0;font-size:13px" }, "Used in: " + usedIn.join(", ")) : null,
      h("div", { class: "foot", style: "justify-content:space-between" },
        isNew ? h("span") : h("button", { class: "btn danger", onclick: async () => {
          close();
          if (!(await confirmBox("Delete block?", usedIn.length ? `Block ${b.number} is used in ${usedIn.join(", ")}. Delete it and take it out of those programs?` : `Delete block ${b.number}?`, "Delete", true))) return;
          d.programs.forEach((p) => p.steps.forEach((s) => (s.blocks = s.blocks.filter((x) => x !== b.id))));
          d.blocks.splice(d.blocks.indexOf(b), 1); changed();
        } }, "Delete block"),
        h("div", { class: "row" },
          h("button", { class: "btn ghost", onclick: () => close() }, "Cancel"),
          h("button", { class: "btn primary", onclick: () => {
            const v = form.read();
            if (v.mainline !== b.mainline && usedIn.length) toast(`Block ${b.number} is used in ${usedIn.join(", ")}: those programs will show a problem until fixed.`, 6000);
            const clash = d.blocks.find((x) => x !== b && v.entity && x.entity === v.entity);
            if (clash) { toast(`Valve ${v.entity} is already used by block ${clash.number}.`, 6000); return; }
            if (d.blocks.some((x) => x !== b && +x.number === +v.number)) { toast(`Block number ${v.number} is already used.`, 6000); return; }
            Object.assign(b, v);
            if (isNew) d.blocks.push(b);
            close(); changed();
          } }, isNew ? "Add block" : "Done"))));
  });
}

function addBlocksDialog() {
  const d = S.draft;
  if (!d.mainlines.length) { toast("Add a mainline first"); return; }
  const n = Math.max(0, ...d.blocks.map((b) => +b.number || 0)) + 1;
  const last = d.blocks[d.blocks.length - 1];
  editBlock({ id: rid("b"), number: n, name: `Block ${n}`, entity: "", mainline: last?.mainline || d.mainlines[0].id,
    default_min: last ? +last.default_min || 60 : 60, enabled: true }, true);
}

/* ------------------------------------------------------------ mainlines */
function renderMainlines() {
  const v = $("#view-mainlines");
  v.innerHTML = "";
  const d = S.draft;
  v.append(h("h2", {}, "Mainlines"),
    h("p", { class: "sub" }, "A mainline is one water supply with its pump or main valve. Several programs can run on the same mainline at once."));
  const wrap = h("div", { class: "steps-list" });
  d.mainlines.forEach((ml) => {
    const nBlocks = d.blocks.filter((b) => b.mainline === ml.id).length;
    const nProgs = d.programs.filter((p) => p.mainline === ml.id).length;
    const pumps = ml.pump_entities || (ml.pump_entities = []);
    const pumpList = h("div", { class: "starts" });
    const drawPumps = () => {
      pumpList.innerHTML = "";
      pumps.forEach((e, i) => pumpList.append(h("div", { class: "start-row" },
        h("input", { type: "text", class: "ent", list: "ents", value: e, placeholder: "switch.pump", onchange: (ev) => { pumps[i] = ev.target.value.trim(); changed(false); } }),
        h("button", { class: "icon-btn del", title: "Remove", onclick: () => { pumps.splice(i, 1); changed(); } }, "✕"))));
      pumpList.append(h("div", {}, h("button", { class: "btn small", onclick: () => { pumps.push(""); drawPumps(); } }, pumps.length ? "+ Add another" : "+ Add pump / main valve")));
    };
    drawPumps();
    const num = (key, label, hint) => h("label", { class: "field" }, label,
      h("span", { class: "dur" }, h("input", { type: "number", class: "num", min: 0, step: key === "overlap_s" ? 5 : 1, value: ml[key] ?? 0,
        oninput: (e) => { ml[key] = +e.target.value || 0; changed(false); } }), h("span", {}, "s")),
      h("span", { class: "muted", style: "font-weight:400;max-width:220px" }, hint));
    wrap.append(h("div", { class: "card" },
      h("div", { class: "row", style: "justify-content:space-between;align-items:center;margin-bottom:12px" },
        h("input", { type: "text", style: "font-size:17px;font-weight:650;flex:1;max-width:420px", value: ml.name, oninput: (e) => { ml.name = e.target.value; changed(false); } }),
        h("span", { class: "muted" }, `${nBlocks} block${nBlocks === 1 ? "" : "s"} · ${nProgs} program${nProgs === 1 ? "" : "s"}`),
        h("button", { class: "btn danger small", onclick: async () => {
          if (nBlocks || nProgs) { toast("Move or delete its blocks and programs first"); return; }
          if (await confirmBox("Delete mainline?", `Delete ${ml.name}?`, "Delete", true)) { d.mainlines.splice(d.mainlines.indexOf(ml), 1); changed(); }
        } }, "Delete")),
      h("div", { class: "row", style: "align-items:flex-start;gap:28px" },
        h("div", { class: "field" }, h("span", { style: "font-size:13px;color:var(--ink-2);font-weight:550" }, "Pump / main valve switches"), pumpList),
        num("pump_start_s", "Pump opens after blocks", "Time between the blocks opening and the main opening."),
        num("pump_stop_s", "Pump closes before blocks", "Time between the main closing and the blocks closing."),
        num("overlap_s", "Default step overlap", "The next step opens this long before the current one closes."))));
  });
  wrap.append(entDatalist());
  v.append(wrap, h("div", { style: "margin-top:12px" }, h("button", { class: "btn primary", onclick: () => {
    d.mainlines.push({ id: rid("ml"), name: `Mainline ${d.mainlines.length + 1}`, pump_entities: [], pump_start_s: 5, pump_stop_s: 5, overlap_s: 30 });
    changed();
  } }, "+ Add mainline")));
}

/* ------------------------------------------------------------ boot */
document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));
$("#save").addEventListener("click", save);
$("#discard").addEventListener("click", async () => {
  if (!(await confirmBox("Discard changes?", "Throw away everything you changed since the last save?", "Discard", true))) return;
  S.draft = clone(S.saved);
  S.issues = { errors: [], warnings: [] };
  if (!S.draft.programs.some((p) => p.id === S.editing)) S.editing = null;
  render();
});
$("#issues-toggle").addEventListener("click", () => { S.issuesOpen = !S.issuesOpen; renderSavebar(); });
$("#pause-all").addEventListener("click", () => act({ action: "pause_all" }, "Everything paused"));
$("#resume-all").addEventListener("click", () => act({ action: "resume_all" }, "Everything resumed"));
window.addEventListener("beforeunload", (e) => { if (dirty()) { e.preventDefault(); e.returnValue = ""; } });

(async () => {
  try {
    await loadData();
    setTab(S.saved.mainlines.length ? "live" : "mainlines");
    pollLive();
  } catch (e) {
    $("#view-live").append(h("div", { class: "notice err" }, "Could not load: " + e.message));
  }
})();
