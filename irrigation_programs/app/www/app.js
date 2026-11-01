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
  appliedAt: null,
  live: null,
  selProgram: null,
  issues: { errors: [], warnings: [] },
  issuesOpen: false,
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
    else if (k === "html") el.innerHTML = v;
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

function toast(msg, ms = 3200) {
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
function parseHMS(v) {
  if (!v) return null;
  const m = String(v).match(/^(?:(\d+) days?, )?(\d+):(\d\d):(\d\d)/);
  if (!m) return null;
  return (+m[1] || 0) * 86400 + +m[2] * 3600 + +m[3] * 60 + +m[4];
}
function fmtWhen(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const now = new Date();
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  const sameDay = d.toDateString() === now.toDateString();
  const tomorrow = new Date(now.getTime() + 86400000).toDateString() === d.toDateString();
  if (sameDay) return `today ${time}`;
  if (tomorrow) return `tomorrow ${time}`;
  return d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" }) + " " + time;
}
const tmin = (t) => { const [a, b] = (t || "0:0").split(":").map(Number); return a * 60 + b; };
const addMin = (t, m) => { const x = ((tmin(t) + Math.round(m)) % 1440 + 1440) % 1440; return `${String(Math.floor(x / 60)).padStart(2, "0")}:${String(x % 60).padStart(2, "0")}`; };

/* model mirrors (summary only, the server does the real validation) */
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
  S.appliedAt = d.applied_at;
  if (!S.selProgram && S.draft.programs.length) S.selProgram = S.draft.programs[0].id;
  $("#setup-dot").hidden = !!d.setup.ready;
}

function setTab(tab) {
  S.tab = tab;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
  document.querySelectorAll(".view").forEach((v) => (v.hidden = v.id !== "view-" + tab));
  render();
  if (tab === "live") pollLive();
}

function render() {
  ({ live: renderLive, programs: renderPrograms, blocks: renderBlocks, mainlines: renderMainlines, setup: renderSetup })[S.tab]();
  renderSavebar();
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

/* ------------------------------------------------------------ save bar */
function renderSavebar() {
  const bar = $("#savebar");
  bar.hidden = !dirty();
  if (bar.hidden) return;
  const { errors, warnings } = S.issues;
  const tog = $("#issues-toggle");
  const n = errors.length + warnings.length;
  tog.hidden = n === 0;
  tog.className = "link " + (errors.length ? "err" : "warn");
  tog.textContent = errors.length
    ? `${errors.length} problem${errors.length > 1 ? "s" : ""} to fix` + (warnings.length ? `, ${warnings.length} warning${warnings.length > 1 ? "s" : ""}` : "")
    : `${warnings.length} warning${warnings.length > 1 ? "s" : ""}`;
  const ul = $("#issues");
  ul.hidden = !S.issuesOpen || n === 0;
  ul.innerHTML = "";
  errors.forEach((e) => ul.append(h("li", { class: "err" }, e)));
  warnings.forEach((w) => ul.append(h("li", { class: "warn" }, w)));
  $("#save").disabled = errors.length > 0;
}

async function save(force = false) {
  const btn = $("#save");
  btn.disabled = true;
  btn.textContent = "Saving…";
  try {
    const r = await api("PUT", "api/config", { config: S.draft, force });
    if (r.confirm) {
      const ok = await confirmBox("Watering is running",
        [`Applying the changes stops the watering that's running now (${r.running.join(", ")}).`,
         "Programs set to loop constantly start again by themselves straight after."],
        "Apply and stop watering", true);
      if (ok) return save(true);
      return;
    }
    if (!r.ok) {
      S.issues = { errors: r.errors || ["Could not save."], warnings: r.warnings || [] };
      S.issuesOpen = true;
      renderSavebar();
      return;
    }
    S.saved = clone(S.draft);
    S.issues = { errors: [], warnings: r.warnings || [] };
    S.setup = r.setup;
    $("#setup-dot").hidden = !!r.setup.ready;
    const haProblem = (r.warnings || []).find((w) => w.startsWith("Home Assistant reports"));
    if (r.reloaded) toast(haProblem || "Saved and applied to Irrigation Unlimited", haProblem ? 9000 : 3200);
    else { toast("Saved. Finish the setup steps so it starts running."); setTab("setup"); return; }
    render();
  } catch (e) {
    toast("Save failed: " + e.message, 6000);
  } finally {
    btn.disabled = false;
    btn.textContent = "Save & apply";
  }
}

/* ------------------------------------------------------------ live */
let liveTimer = null;
async function pollLive() {
  clearTimeout(liveTimer);
  if (S.tab !== "live") return;
  try { S.live = await api("GET", "api/live"); } catch (e) { S.live = { error: e.message, mainlines: [] }; }
  if (S.tab === "live") renderLive();
  liveTimer = setTimeout(pollLive, 3000);
}

async function act(body, label) {
  try {
    let r = await api("POST", "api/action", body);
    if (r.confirm) {
      const ok = await confirmBox("Mainline is busy",
        `${r.busy.join(", ")} is running on this mainline. Stop it and start the new program?`, "Stop it and start");
      if (!ok) return;
      r = await api("POST", "api/action", { ...body, force: true });
    }
    if (!r.ok) toast((r.errors || ["Failed"]).join(" "), 6000);
    else if (label) toast(label);
    setTimeout(pollLive, 600);
  } catch (e) { toast(e.message, 6000); }
}

function renderLive() {
  const v = $("#view-live");
  v.innerHTML = "";
  const cfg = S.saved;
  if (!S.setup?.ready) {
    v.append(h("div", { class: "notice" }, "Setup isn't finished yet, so nothing will water. ",
      h("button", { class: "link", onclick: () => setTab("setup") }, "Open Setup")));
  }
  if (S.live?.error) v.append(h("div", { class: "notice err" }, "Can't reach Home Assistant: " + S.live.error));
  if (!cfg.mainlines.length) {
    v.append(h("div", { class: "card empty" },
      h("h2", {}, "Nothing set up yet"),
      h("p", {}, "Start by adding a mainline, then your blocks, then a program."),
      h("button", { class: "btn primary", onclick: () => setTab("mainlines") }, "Add a mainline")));
    return;
  }
  const grid = h("div", { class: "live-grid" });
  const cards = S.live?.mainlines || [];
  for (const ml of cfg.mainlines) {
    const c = cards.find((x) => x.id === ml.id);
    grid.append(liveCard(ml, c));
  }
  v.append(grid);
  if (S.live?.updated) v.append(h("p", { class: "muted", style: "margin-top:14px;font-size:13px" },
    "Live from Home Assistant · updates every few seconds" + (S.live.applied_at ? ` · programs last applied ${fmtWhen(S.live.applied_at)}` : "")));
}

function liveCard(ml, c) {
  const card = h("div", { class: "card ml-card " + (c?.state || "") });
  const state = c?.state || "idle";
  const pillText = { idle: "Idle", running: "Running", paused: "Paused", starting: "Starting" }[state];
  card.append(h("div", { class: "ml-head" },
    h("h3", {}, ml.name),
    c && c.pump !== null ? h("span", { class: "pump " + (c.pump ? "on" : "") }, c.pump ? "● Pump on" : "○ Pump off") : null,
    h("span", { class: "pill " + state }, pillText)));
  if (!c) {
    card.append(h("p", { class: "muted" }, "Not applied yet. Add blocks to this mainline and save."));
    return card;
  }
  const p = c.program;
  if (p && state !== "starting") {
    const prog = S.saved.programs.find((x) => x.id === p.id);
    const modeTxt = p.mode === "loop" ? "Loops nonstop" : p.mode === "until" ? `Loops until ${prog?.window?.end || ""}` : "Runs once";
    card.append(h("div", {}, h("div", { class: "prog-name" }, p.name), h("div", { class: "mode-tag" }, modeTxt)));
    const left = parseHMS(p.step_left);
    const pct = left !== null && p.step_duration_s ? Math.min(100, 100 * (1 - left / p.step_duration_s)) : 0;
    const loop = p.repeat && p.mode !== "once" ? ` · loop ${String(p.repeat).split("/")[0]}` : "";
    card.append(
      h("div", { class: "stepline" },
        h("div", {}, h("div", { class: "muted" }, `Step ${p.step || "–"} of ${p.steps}${loop}`),
          h("div", { class: "big" }, left !== null ? fmtDur(left) : "–")),
        h("div", { class: "muted", style: "text-align:right" }, "left in this step")),
      h("div", { class: "bar" }, h("i", { style: `width:${pct}%` })));
    const kv = h("dl", { class: "kv" });
    kv.append(h("dt", {}, "Open now"), h("dd", {}, c.open_blocks.length
      ? h("div", { class: "chips" }, c.open_blocks.map((n) => h("span", { class: "chip open" }, h("b", {}, n))))
      : h("span", { class: "muted" }, "none")));
    if (p.mode === "once" && p.run_left) {
      const end = new Date(Date.now() + (parseHMS(p.run_left) || 0) * 1000);
      kv.append(h("dt", {}, "Finishes"), h("dd", {}, fmtWhen(end.toISOString())));
    }
    if (p.mode === "until" && p.stop_at) kv.append(h("dt", {}, "Stops"), h("dd", {}, fmtWhen(p.stop_at)));
    card.append(kv);
    const paused = state === "paused";
    card.append(h("div", { class: "ml-actions" },
      h("button", { class: "btn", onclick: () => act({ action: "skip", program: p.id }, "Skipping to the next step") }, "Skip step"),
      h("button", { class: "btn", onclick: () => act({ action: paused ? "resume" : "pause", mainline: ml.id }, paused ? "Resumed" : "Paused") }, paused ? "Resume" : "Pause"),
      h("button", { class: "btn danger", onclick: async () => {
        if (await confirmBox("Stop watering?", `Stop ${p.name} on ${ml.name}?`, "Stop", true)) act({ action: "stop", mainline: ml.id }, "Stopped");
      } }, "Stop")));
    return card;
  }
  if (state === "starting") {
    card.append(h("p", {}, `${p.name} is about to start again…`),
      h("div", { class: "ml-actions" }, h("button", { class: "btn danger", onclick: () => act({ action: "stop", mainline: ml.id }, "Stopped") }, "Stop looping")));
    return card;
  }
  // idle
  const kv = h("dl", { class: "kv" });
  kv.append(h("dt", {}, "Next"), h("dd", {}, c.next ? `${c.next.program}, ${fmtWhen(c.next.at)}` : h("span", { class: "muted" }, "nothing scheduled")));
  if (c.open_blocks.length) kv.append(h("dt", {}, "Open now"), h("dd", {}, c.open_blocks.join(", ")));
  card.append(kv);
  const progs = S.saved.programs.filter((x) => x.mainline === ml.id && x.enabled !== false);
  if (progs.length) {
    const sel = h("select", {}, progs.map((x) => h("option", { value: x.id }, x.name)));
    card.append(h("div", { class: "idle-start" }, sel,
      h("button", { class: "btn primary", onclick: () => act({ action: "start", program: sel.value }, "Starting") }, "Start")));
  } else {
    card.append(h("p", { class: "muted" }, "No programs on this mainline yet."));
  }
  return card;
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
  S.selProgram = p.id;
  changed();
}

function renderPrograms() {
  const v = $("#view-programs");
  v.innerHTML = "";
  const d = S.draft;
  const list = h("div", { class: "card plist" });
  for (const ml of d.mainlines) {
    const ps = d.programs.filter((p) => p.mainline === ml.id);
    list.append(h("div", { class: "group" }, ml.name));
    if (!ps.length) list.append(h("div", { class: "muted", style: "padding:4px 8px 8px;font-size:13px" }, "No programs"));
    for (const p of ps) {
      list.append(h("button", {
        class: "pitem" + (p.id === S.selProgram ? " on" : "") + (p.enabled === false ? " off" : ""),
        onclick: () => { S.selProgram = p.id; render(); },
      }, h("span", { class: "n" }, p.name), h("span", { class: "m" }, modeLabel(p))));
    }
  }
  const orphan = d.programs.filter((p) => !d.mainlines.some((m) => m.id === p.mainline));
  if (orphan.length) {
    list.append(h("div", { class: "group" }, "No mainline"));
    orphan.forEach((p) => list.append(h("button", { class: "pitem" + (p.id === S.selProgram ? " on" : ""), onclick: () => { S.selProgram = p.id; render(); } }, h("span", { class: "n" }, p.name))));
  }
  list.append(h("div", { style: "padding:8px" }, h("button", { class: "btn primary", style: "width:100%", onclick: newProgram }, "+ New program")));

  const p = d.programs.find((x) => x.id === S.selProgram);
  const ed = h("div", { class: "card editor" });
  if (!p) {
    ed.append(h("div", { class: "empty" }, h("h2", {}, "No program selected"),
      h("p", {}, "A program runs your blocks in steps: each step opens one or more blocks for a set time, then the next step starts."),
      h("button", { class: "btn primary", onclick: newProgram }, "Create a program")));
  } else {
    programEditor(ed, p);
  }
  v.append(h("div", { class: "split" }, list, ed));
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
  // head
  ed.append(h("div", { class: "editor-head" },
    h("input", { type: "text", class: "name", value: p.name, "aria-label": "Program name", oninput: (e) => { p.name = e.target.value; changed(false); }, onchange: () => render() }),
    h("label", { class: "field" }, "Mainline",
      h("select", { onchange: (e) => {
        const steps = p.steps.flatMap((s) => s.blocks).length;
        p.mainline = e.target.value;
        if (steps) { p.steps.forEach((s) => (s.blocks = [])); toast("Blocks cleared: they belong to the other mainline"); }
        changed();
      } }, d.mainlines.map((m) => h("option", { value: m.id, selected: m.id === p.mainline }, m.name)))),
    h("label", { class: "check", style: "padding-bottom:7px" }, h("input", { type: "checkbox", checked: p.enabled !== false, onchange: (e) => { p.enabled = e.target.checked; changed(); } }), "Enabled")));

  // run mode
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
        h("div", { class: "field", style: "font-size:13px;color:var(--ink-2);font-weight:550;display:flex;flex-direction:column;gap:4px" }, "Days", daysPicker(w.days, () => changed()))),
      h("div", { style: "margin-top:12px" }, h("div", { class: "muted", style: "font-size:13px;margin-bottom:4px" }, "At the stop time"),
        h("label", { class: "check", style: "margin-right:18px" }, h("input", { type: "radio", name: "endb", checked: p.end_behavior === "finish_step", onchange: () => { p.end_behavior = "finish_step"; changed(false); } }), "Finish the running step, then stop"),
        h("label", { class: "check" }, h("input", { type: "radio", name: "endb", checked: p.end_behavior === "stop_now", onchange: () => { p.end_behavior = "stop_now"; changed(false); } }), "Stop immediately")));
  } else {
    modeSec.append(h("p", { class: "muted", style: "margin:10px 0 0" },
      "Runs the steps over and over, from when you press Start on the Live page until you press Stop. It starts again by itself after a Home Assistant restart."));
  }
  ed.append(modeSec);

  // steps
  ed.append(stepsSection(p));
  ed.append(seasonPicker(p));

  // footer actions
  ed.append(h("div", { class: "row", style: "justify-content:space-between;margin-top:6px" },
    h("div", { class: "row" },
      h("button", { class: "btn", onclick: () => {
        const c = clone(p); c.id = rid("p"); c.name = p.name + " (copy)";
        d.programs.splice(d.programs.indexOf(p) + 1, 0, c); S.selProgram = c.id; changed();
      } }, "Duplicate")),
    h("button", { class: "btn danger", onclick: async () => {
      if (!(await confirmBox("Delete program?", `Delete “${p.name}”? This takes effect when you save.`, "Delete", true))) return;
      d.programs.splice(d.programs.indexOf(p), 1);
      S.selProgram = d.programs[0]?.id || null; changed();
    } }, "Delete program")));
}

function stepsSection(p) {
  const d = S.draft;
  const blocks = byId(d.blocks);
  const ml = mlOf(p);
  const sec = h("div", { class: "section" }, h("div", { class: "label" }, "Steps"));
  const tbl = h("table", { class: "steps" },
    h("thead", {}, h("tr", {}, h("th", {}, "#"), h("th", {}, "Blocks open together"), h("th", {}, "Duration"),
      h("th", {}, p.mode === "once" ? "Overlap with next" : "Overlap with next"), h("th", {}))));
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
      ovl = h("div", {},
        h("span", { class: "dur" }, h("input", { type: "number", class: "num", min: 0, step: 5,
          placeholder: String(ml?.overlap_s ?? 0), value: st.overlap_s ?? "",
          oninput: (e) => { st.overlap_s = e.target.value === "" ? null : +e.target.value; changed(false); renderSummary(); } }), h("span", {}, "s")),
        h("div", { class: "ovl-hint" }, [st.overlap_s === null || st.overlap_s === undefined || st.overlap_s === "" ? "mainline default" : "",
          isLast ? "into next loop" : ""].filter(Boolean).join(" · ")));
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
  tbl.append(tb);
  sec.append(h("div", { class: "table-wrap" }, tbl),
    h("div", { style: "margin-top:8px" }, h("button", { class: "btn small", onclick: () => {
      const last = p.steps[p.steps.length - 1];
      p.steps.push({ blocks: [], duration_min: last ? last.duration_min : 60, overlap_s: null }); changed();
    } }, "+ Add step")));
  const sum = h("div", { class: "summary", id: "summary" });
  sec.append(sum);
  setTimeout(renderSummary);
  return sec;
}

function renderSummary() {
  const p = S.draft.programs.find((x) => x.id === S.selProgram);
  const el = $("#summary");
  if (!p || !el) return;
  el.innerHTML = "";
  const item = (label, val) => h("div", {}, h("small", {}, label), h("strong", {}, val));
  const nBlocks = new Set(p.steps.flatMap((s) => s.blocks)).size;
  el.append(item("Steps", p.steps.length), item("Blocks used", nBlocks));
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
  const d = S.draft;
  const mlBlocks = d.blocks.filter((b) => b.mainline === p.mainline).sort((a, b) => a.number - b.number);
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
          st.blocks = mlBlocks.filter((b) => sel.has(b.id)).map((b) => b.id); close(); changed();
        } }, "Done")));
  });
}

/* ------------------------------------------------------------ blocks */
function entityInput(value, onChange, cls = "ent") {
  return h("input", { type: "text", class: cls, list: "ents", value: value || "", placeholder: "switch.valve_1",
    onchange: (e) => onChange(e.target.value.trim()) });
}
function entDatalist() {
  return h("datalist", { id: "ents" }, S.entities.map((e) => h("option", { value: e.id }, e.name)));
}

function renderBlocks() {
  const v = $("#view-blocks");
  v.innerHTML = "";
  const d = S.draft;
  v.append(h("h2", {}, "Blocks"), h("p", { class: "sub" }, "Each block is one field or section with its own valve. Programs open blocks in steps."));
  if (!d.mainlines.length) {
    v.append(h("div", { class: "card empty" }, h("p", {}, "Add a mainline first, then put blocks on it."),
      h("button", { class: "btn primary", onclick: () => setTab("mainlines") }, "Go to Mainlines")));
    return;
  }
  const count = h("input", { type: "number", class: "narrow", min: 1, max: 100, value: 1 });
  const mlSel = h("select", {}, d.mainlines.map((m) => h("option", { value: m.id }, m.name)));
  v.append(h("div", { class: "toolbar" },
    h("input", { type: "text", placeholder: "Filter blocks", value: S.blockFilter, oninput: (e) => { S.blockFilter = e.target.value.toLowerCase(); drawTable(); } }),
    h("span", { class: "grow" }),
    h("span", { class: "muted" }, "Add"), count, h("span", { class: "muted" }, "blocks on"), mlSel,
    h("button", { class: "btn primary", onclick: () => {
      let next = Math.max(0, ...d.blocks.map((b) => +b.number || 0)) + 1;
      for (let i = 0; i < Math.min(100, +count.value || 1); i++) {
        d.blocks.push({ id: rid("b"), number: next, name: `Block ${next}`, entity: "", mainline: mlSel.value, max_min: 300, enabled: true });
        next++;
      }
      changed();
    } }, "Add")));
  const card = h("div", { class: "card table-wrap" });
  v.append(card, entDatalist());
  const usedIn = (bid) => d.programs.filter((p) => p.steps.some((s) => s.blocks.includes(bid))).map((p) => p.name);

  function drawTable() {
    card.innerHTML = "";
    const list = [...d.blocks].sort((a, b) => a.number - b.number)
      .filter((b) => !S.blockFilter || `${b.number} ${b.name} ${b.entity}`.toLowerCase().includes(S.blockFilter));
    if (!d.blocks.length) { card.append(h("div", { class: "empty" }, "No blocks yet. Use Add above.")); return; }
    const tb = h("tbody");
    list.forEach((b) => {
      const hh = Math.floor(b.max_min / 60), mm = Math.round(b.max_min % 60);
      const hIn = h("input", { type: "number", class: "narrow", min: 0, value: hh, oninput: () => { b.max_min = (+hIn.value || 0) * 60 + (+mIn.value || 0); changed(false); } });
      const mIn = h("input", { type: "number", class: "narrow", min: 0, max: 59, value: mm, oninput: () => { b.max_min = (+hIn.value || 0) * 60 + (+mIn.value || 0); changed(false); } });
      const ent = S.entities.find((e) => e.id === b.entity);
      tb.append(h("tr", {},
        h("td", { class: "num-cell" }, h("input", { type: "number", class: "num", min: 1, value: b.number, onchange: (e) => { b.number = +e.target.value; changed(); } })),
        h("td", {}, h("input", { type: "text", value: b.name, oninput: (e) => { b.name = e.target.value; changed(false); } })),
        h("td", {}, entityInput(b.entity, (val) => { b.entity = val; changed(); }),
          b.entity && !ent ? h("div", { class: "warn-cell" }, "Not found in Home Assistant") : null,
          !b.entity ? h("div", { class: "warn-cell" }, "No valve set") : null),
        h("td", {}, h("select", { onchange: (e) => {
          const used = usedIn(b.id);
          b.mainline = e.target.value;
          if (used.length) toast(`Block ${b.number} is used in ${used.join(", ")}: those programs will show a problem until fixed.`, 6000);
          changed();
        } }, d.mainlines.map((m) => h("option", { value: m.id, selected: m.id === b.mainline }, m.name)))),
        h("td", {}, h("span", { class: "dur" }, hIn, h("span", {}, "h"), mIn, h("span", {}, "m"))),
        h("td", {}, h("input", { type: "checkbox", checked: b.enabled !== false, title: "Enabled", onchange: (e) => { b.enabled = e.target.checked; changed(false); } })),
        h("td", {}, h("button", { class: "icon-btn del", title: "Delete block", onclick: async () => {
          const used = usedIn(b.id);
          if (used.length && !(await confirmBox("Block is in use", `Block ${b.number} is used in ${used.join(", ")}. Delete it and take it out of those programs?`, "Delete", true))) return;
          d.programs.forEach((p) => p.steps.forEach((s) => (s.blocks = s.blocks.filter((x) => x !== b.id))));
          d.blocks.splice(d.blocks.indexOf(b), 1); changed();
        } }, "✕"))));
    });
    card.append(h("table", { class: "tbl" },
      h("thead", {}, h("tr", {}, h("th", {}, "No."), h("th", {}, "Name"), h("th", {}, "Valve switch"), h("th", {}, "Mainline"),
        h("th", { title: "Safety limit: the valve is closed if it runs longer than this" }, "Max runtime"), h("th", {}, "On"), h("th", {}))), tb));
  }
  drawTable();
}

/* ------------------------------------------------------------ mainlines */
function renderMainlines() {
  const v = $("#view-mainlines");
  v.innerHTML = "";
  const d = S.draft;
  v.append(h("h2", {}, "Mainlines"),
    h("p", { class: "sub" }, "A mainline is one water supply with its pump or main valve. Only one program runs on a mainline at a time."));
  const wrap = h("div", { class: "steps-list" });
  d.mainlines.forEach((ml) => {
    const nBlocks = d.blocks.filter((b) => b.mainline === ml.id).length;
    const nProgs = d.programs.filter((p) => p.mainline === ml.id).length;
    const pumps = ml.pump_entities || (ml.pump_entities = []);
    const pumpList = h("div", { class: "starts" });
    const drawPumps = () => {
      pumpList.innerHTML = "";
      pumps.forEach((e, i) => pumpList.append(h("div", { class: "start-row" },
        entityInput(e, (val) => { pumps[i] = val; changed(); }),
        h("button", { class: "icon-btn del", title: "Remove", onclick: () => { pumps.splice(i, 1); changed(); } }, "✕"))));
      pumpList.append(h("div", {}, h("button", { class: "btn small", onclick: () => { pumps.push(""); drawPumps(); } }, pumps.length ? "+ Add another" : "+ Add pump / main valve")));
    };
    drawPumps();
    const num = (key, label, hint) => h("label", { class: "field" }, label,
      h("span", { class: "dur" }, h("input", { type: "number", class: "num", min: 0, step: key === "overlap_s" ? 5 : 1, value: ml[key] ?? 0,
        oninput: (e) => { ml[key] = +e.target.value || 0; changed(false); } }), h("span", {}, "s")),
      hint ? h("span", { class: "muted", style: "font-weight:400" }, hint) : null);
    wrap.append(h("div", { class: "card" },
      h("div", { class: "row", style: "justify-content:space-between;align-items:center;margin-bottom:12px" },
        h("input", { type: "text", class: "name", style: "font-size:17px;font-weight:650;flex:1;max-width:420px", value: ml.name, oninput: (e) => { ml.name = e.target.value; changed(false); } }),
        h("span", { class: "muted" }, `${nBlocks} block${nBlocks === 1 ? "" : "s"} · ${nProgs} program${nProgs === 1 ? "" : "s"}`),
        h("button", { class: "btn danger small", onclick: async () => {
          if (nBlocks || nProgs) { toast("Move or delete its blocks and programs first"); return; }
          if (await confirmBox("Delete mainline?", `Delete ${ml.name}?`, "Delete", true)) { d.mainlines.splice(d.mainlines.indexOf(ml), 1); changed(); }
        } }, "Delete")),
      h("div", { class: "row", style: "align-items:flex-start;gap:28px" },
        h("div", { class: "field" }, h("span", { style: "font-size:13px;color:var(--ink-2);font-weight:550" }, "Pump / main valve switches"), pumpList),
        num("pump_start_s", "Pump starts before first block", ""),
        num("pump_stop_s", "Pump stays on after last block", ""),
        num("overlap_s", "Default step overlap", "next step opens this long before the current one closes"))));
  });
  wrap.append(entDatalist());
  v.append(wrap, h("div", { style: "margin-top:12px" }, h("button", { class: "btn primary", onclick: () => {
    d.mainlines.push({ id: rid("ml"), name: `Mainline ${d.mainlines.length + 1}`, pump_entities: [], pump_start_s: 5, pump_stop_s: 5, overlap_s: 30 });
    changed();
  } }, "+ Add mainline")));
}

/* ------------------------------------------------------------ setup */
function renderSetup() {
  const v = $("#view-setup");
  v.innerHTML = "";
  const s = S.setup || {};
  v.append(h("h2", {}, "Setup"), h("p", { class: "sub" }, "This add-on writes your programs into Irrigation Unlimited, which does the actual switching. These steps connect the two."));
  if (!s.ha_ok) v.append(h("div", { class: "notice err" }, "Can't reach Home Assistant: " + (s.error || "")));
  const step = (ok, title, text, btn) => h("div", { class: "card setup-step" },
    h("div", { class: "tick " + (ok ? "ok" : "bad") }, ok ? "✓" : "!"),
    h("div", {}, h("h3", {}, title), h("div", { class: "muted" }, text)), btn || h("span"));
  v.append(h("div", { class: "steps-list" },
    step(s.iu_installed, "Irrigation Unlimited installed",
      s.iu_installed ? "Found in custom_components." : "Install “Irrigation Unlimited” from HACS (Integrations), then come back here."),
    step(s.include, "Connected to configuration.yaml",
      s.include ? "configuration.yaml loads irrigation_programs.yaml." :
        s.other_iu_config ? "configuration.yaml already has its own irrigation_unlimited: section. Remove it, then connect." :
          "Adds one line to configuration.yaml so Irrigation Unlimited reads the programs from this add-on.",
      s.include ? null : h("button", { class: "btn primary", disabled: !!s.other_iu_config, onclick: async () => {
        const r = await api("POST", "api/setup/connect");
        if (!r.ok) return toast((r.errors || []).join(" "), 7000);
        toast("Connected. Now restart Home Assistant.");
        await refreshSetup();
      } }, "Connect")),
    step(s.iu_loaded, "Irrigation Unlimited running",
      s.iu_loaded ? "Running. Saving programs applies them straight away." : "Restart Home Assistant once so it loads Irrigation Unlimited with your programs.",
      s.iu_loaded ? null : h("button", { class: "btn", disabled: !s.include || !s.iu_installed, onclick: async () => {
        if (!(await confirmBox("Restart Home Assistant?", "Home Assistant restarts and is unavailable for a minute. Any watering that's running stops.", "Restart", true))) return;
        await api("POST", "api/setup/restart");
        toast("Restarting Home Assistant… this page reconnects by itself.", 8000);
        setTimeout(() => location.reload(), 60000);
      } }, "Restart Home Assistant"))));

  const pre = h("pre", { class: "yaml", hidden: true });
  v.append(h("div", { class: "card", style: "margin-top:18px" },
    h("div", { class: "row", style: "justify-content:space-between;align-items:center" },
      h("div", {}, h("h3", {}, "Generated Irrigation Unlimited config"),
        h("div", { class: "muted" }, `Written to ${s.config_dir || "/config"}/irrigation_programs.yaml` + (S.appliedAt ? ` · last applied ${fmtWhen(S.appliedAt)}` : ""))),
      h("button", { class: "btn small", onclick: async () => { pre.textContent = await api("GET", "api/yaml"); pre.hidden = !pre.hidden; } }, "Show / hide")),
    pre));
}

async function refreshSetup() {
  const d = await api("GET", "api/data");
  S.setup = d.setup;
  S.entities = d.entities;
  $("#setup-dot").hidden = !!d.setup.ready;
  render();
}

/* ------------------------------------------------------------ boot */
document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));
$("#save").addEventListener("click", () => save(false));
$("#discard").addEventListener("click", async () => {
  if (!(await confirmBox("Discard changes?", "Throw away everything you changed since the last save?", "Discard", true))) return;
  S.draft = clone(S.saved);
  S.issues = { errors: [], warnings: [] };
  if (!S.draft.programs.some((p) => p.id === S.selProgram)) S.selProgram = S.draft.programs[0]?.id || null;
  render();
});
$("#issues-toggle").addEventListener("click", () => { S.issuesOpen = !S.issuesOpen; renderSavebar(); });
$("#pause-all").addEventListener("click", () => act({ action: "pause" }, "All watering paused"));
$("#resume-all").addEventListener("click", () => act({ action: "resume" }, "All watering resumed"));
window.addEventListener("beforeunload", (e) => { if (dirty()) { e.preventDefault(); e.returnValue = ""; } });

(async () => {
  try {
    await loadData();
    setTab(S.setup.ready && S.saved.mainlines.length ? "live" : S.saved.mainlines.length ? "live" : "mainlines");
  } catch (e) {
    $("#view-live").append(h("div", { class: "notice err" }, "Could not load: " + e.message));
  }
})();
