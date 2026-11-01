"""The watering engine: runs programs step by step and switches valves and pumps directly.

Key rules
- Timing is kept as "program time" (seconds into the program). It only advances while
  Home Assistant is reachable and the add-on is alive, so after a crash or restart a run
  carries on from where it was (position is saved to disk every few seconds).
- Opening: blocks open first, the pump/main valve follows `pump_start_s` later.
- Closing (end of program, pause, stop): the pump/main valve closes first, blocks follow
  `pump_stop_s` later (at least 2 s). Within one tick pumps are always switched off before
  blocks, and blocks are always switched on before pumps.
- Several programs may run on the same mainline at once; a block or pump is open while
  any run needs it.
"""
from __future__ import annotations

import copy
import logging
import math
import time
import uuid
from datetime import datetime, timedelta

import model

log = logging.getLogger("irrigation_programs.engine")

GAP_S = 10            # a tick gap longer than this means we were down: don't count it
RESYNC_AFTER_S = 20   # resend a command if the entity still disagrees after this long
MIN_CLOSE_LEAD_S = 2  # pump closes at least this long before blocks on pause/stop
SAVE_EVERY_S = 5


def service_for(entity: str, on: bool) -> tuple[str, str]:
    domain = entity.split(".", 1)[0]
    if domain == "valve":
        return "valve", "open_valve" if on else "close_valve"
    if domain in ("switch", "input_boolean", "light", "fan"):
        return domain, "turn_on" if on else "turn_off"
    return "homeassistant", "turn_on" if on else "turn_off"


def is_on(state: str | None) -> bool | None:
    if state in ("on", "open", "opening"):
        return True
    if state in ("off", "closed", "closing"):
        return False
    return None


class Engine:
    def __init__(self, store, ha, tz):
        self.store = store          # object with load(name, default) / save(name, data)
        self.ha = ha
        self.tz = tz
        st = store.load("engine.json", {})
        self.runs: list[dict] = st.get("runs", [])
        self.commanded: dict[str, dict] = st.get("commanded", {})   # entity -> {"on": bool, "at": ts}
        self.overrides: dict[str, dict] = st.get("overrides", {})   # entity -> {"on": False}
        self.fired: dict[str, float] = st.get("fired", {})
        self.events: list[dict] = st.get("events", [])
        self.last_tick: float | None = None
        self.last_save = 0.0
        self.ha_ok = False
        self.states: dict[str, dict] = {}
        self.cfg = model.empty_config()
        self.dirty = True
        self.wall = time.time   # replaceable in tests
        self.recent: list[tuple[float, str]] = []   # recent switching, for the header line

    # ------------------------------------------------------------ persistence / log
    def save(self, force: bool = False) -> None:
        if force or self.dirty or self.wall() - self.last_save >= SAVE_EVERY_S:
            self.store.save("engine.json", {"runs": self.runs, "commanded": self.commanded, "overrides": self.overrides,
                                            "fired": self.fired, "events": self.events[-300:]})
            self.last_save = self.wall()
            self.dirty = False

    def event(self, text: str, kind: str = "info") -> None:
        log.info(text)
        self.events.append({"at": self.wall(), "text": text, "kind": kind})
        self.events = self.events[-300:]
        self.dirty = True

    def now_dt(self) -> datetime:
        return datetime.fromtimestamp(self.wall(), self.tz)

    # ------------------------------------------------------------ run geometry
    @staticmethod
    def geometry(run: dict) -> dict:
        prog, ml = run["prog"], run["ml"]
        plan = model.step_plan(prog, ml)
        period = sum(p["duration"] - p["overlap"] for p in plan)
        return {"plan": plan, "n": len(plan), "period": period}

    def g_start(self, run, g, geo=None):
        geo = geo or self.geometry(run)
        loop, i = divmod(g, geo["n"])
        return loop * geo["period"] + geo["plan"][i]["offset"]

    def g_end(self, run, g, geo=None):
        geo = geo or self.geometry(run)
        return self.g_start(run, g, geo) + geo["plan"][g % geo["n"]]["duration"]

    def last_g(self, run, geo=None) -> int | None:
        """Last global step index this run will do (None = loops forever)."""
        geo = geo or self.geometry(run)
        if run.get("last_g") is not None:
            return run["last_g"]
        if run["prog"].get("mode", "once") == "once":
            return geo["n"] - 1
        return None

    def end_e(self, run, geo=None) -> float:
        geo = geo or self.geometry(run)
        lg = self.last_g(run, geo)
        return math.inf if lg is None else self.g_end(run, lg, geo)

    def active_steps(self, run, e=None, geo=None) -> list[int]:
        """Global step indices open at program time e (normally 1, 2 during an overlap)."""
        geo = geo or self.geometry(run)
        if not geo["n"] or geo["period"] <= 0:
            return []
        e = run["e"] if e is None else e
        lg = self.last_g(run, geo)
        loop = int(e // geo["period"])
        if run["prog"].get("mode", "once") == "once":
            loop = 0
        out = []
        for lp in (loop - 1, loop):
            if lp < 0:
                continue
            for i in range(geo["n"]):
                g = lp * geo["n"] + i
                if lg is not None and g > lg:
                    continue
                if self.g_start(run, g, geo) <= e < self.g_end(run, g, geo):
                    out.append(g)
        return sorted(out)

    def current_g(self, run, geo=None) -> int | None:
        act = self.active_steps(run, geo=geo)
        return act[-1] if act else None

    # ------------------------------------------------------------ starting / stopping
    def program_run(self, pid: str) -> dict | None:
        return next((r for r in self.runs if r.get("program_id") == pid), None)

    def start_program(self, prog: dict, source: str = "manual") -> tuple[bool, str]:
        if self.program_run(prog["id"]):
            return False, f"{prog['name']} is already running."
        ml = model.by_id(self.cfg["mainlines"]).get(prog["mainline"])
        if not ml:
            return False, "That program has no mainline."
        if not prog.get("steps"):
            return False, "That program has no steps."
        now = self.wall()
        run = {"id": uuid.uuid4().hex[:8], "program_id": prog["id"], "name": prog["name"], "source": source,
               "prog": copy.deepcopy(prog), "ml": copy.deepcopy(ml), "mainline": ml["id"],
               "started_at": now, "e": 0.0, "state": "running", "opened_at": None, "phase_at": None,
               "last_g": None, "stop_at": None, "stop_handled": False}
        if prog.get("mode") == "until":
            run["stop_at"] = model.next_time(self.now_dt(), prog["window"]["end"]).timestamp()
        self.runs.append(run)
        self.event(f"Started {prog['name']} ({'schedule' if source == 'schedule' else source}).")
        return True, ""

    def start_block(self, block: dict) -> tuple[bool, str]:
        prog = {"id": f"block_{block['id']}", "name": f"Block {block['number']} (manual)", "mainline": block["mainline"],
                "mode": "once", "steps": [{"blocks": [block["id"]], "duration_min": float(block.get("default_min", 60)), "overlap_s": 0}]}
        return self.start_program(prog, source="block")

    def begin_phase(self, run: dict, kind: str) -> None:
        """kind: 'pausing' or 'stopping' (pump closes first, blocks follow)."""
        if run["state"] in ("pausing", "stopping"):
            if kind == "stopping":
                run["state"] = "stopping"
            return
        if run["state"] == "paused":
            if kind == "stopping":
                self.runs.remove(run)
                self.event(f"Stopped {run['name']}.")
            return
        run["state"] = kind
        run["phase_at"] = self.wall()
        self.dirty = True

    def stop_run(self, run: dict, reason: str = "") -> None:
        self.begin_phase(run, "stopping")
        if reason:
            self.event(f"Stopping {run['name']}: {reason}")

    def pause_run(self, run: dict) -> None:
        if run["state"] == "running":
            self.begin_phase(run, "pausing")
            self.event(f"Paused {run['name']}.")

    def resume_run(self, run: dict) -> None:
        if run["state"] in ("paused", "pausing"):
            run["state"] = "running"
            run["opened_at"] = None
            run["phase_at"] = None
            self.event(f"Resumed {run['name']}.")
            self.dirty = True

    def skip_step(self, run: dict) -> None:
        geo = self.geometry(run)
        g = self.current_g(run, geo)
        if g is None:
            return
        lg = self.last_g(run, geo)
        if lg is not None and g >= lg:
            self.stop_run(run, "skipped the last step")
            return
        run["e"] = float(self.g_start(run, g + 1, geo))
        self.event(f"{run['name']}: skipped step {g % geo['n'] + 1}.")
        self.dirty = True

    def lead(self, run) -> float:
        # +1 s: the pump is switched off on the first tick after the phase starts
        return max(MIN_CLOSE_LEAD_S, float(run["ml"].get("pump_stop_s", 0) or 0)) + 1

    # ------------------------------------------------------------ desired state
    def run_wants(self, run: dict) -> tuple[set[str], bool]:
        """(block ids that should be open, pump needed) for one run."""
        geo = self.geometry(run)
        now = self.wall()
        state = run["state"]
        if state == "paused":
            return set(), False
        blocks = set()
        for g in self.active_steps(run, geo=geo):
            blocks.update(geo["plan"][g % geo["n"]]["blocks"])
        if state in ("pausing", "stopping"):
            if now - (run["phase_at"] or now) >= self.lead(run):
                return set(), False
            return blocks, False
        ps = float(run["ml"].get("pump_start_s", 0) or 0)
        pe = float(run["ml"].get("pump_stop_s", 0) or 0)
        opened = run.get("opened_at")
        pump = bool(blocks) and opened is not None and now - opened >= ps and run["e"] < self.end_e(run, geo) - pe
        return blocks, pump

    def desired(self) -> dict[str, bool]:
        """entity -> should be on, for every entity this add-on manages."""
        blocks = model.by_id(self.cfg["blocks"])
        mls = model.by_id(self.cfg["mainlines"])
        want: dict[str, bool] = {}
        for b in self.cfg["blocks"]:
            if b.get("entity"):
                want[b["entity"]] = False
        for ml in self.cfg["mainlines"]:
            for e in ml.get("pump_entities", []):
                if e:
                    want[e] = False
        for run in self.runs:
            bl, pump = self.run_wants(run)
            for bid in bl:
                b = blocks.get(bid)
                if b and b.get("entity") and b.get("enabled", True):
                    want[b["entity"]] = True
            ml = mls.get(run["mainline"]) or run["ml"]
            for e in ml.get("pump_entities", []):
                if e:
                    want[e] = want.get(e, False) or pump
        # manual "close" overrides last until nothing wants the entity any more
        for ent, ov in list(self.overrides.items()):
            if not want.get(ent):
                self.overrides.pop(ent)
                self.dirty = True
            else:
                want[ent] = ov.get("on", False)
        return want

    def pump_entities(self) -> set[str]:
        return {e for ml in self.cfg["mainlines"] for e in ml.get("pump_entities", []) if e} | \
               {e for r in self.runs for e in r["ml"].get("pump_entities", []) if e}

    # ------------------------------------------------------------ switching
    async def send(self, entity: str, on: bool, why: str = "") -> bool:
        domain, service = service_for(entity, on)
        try:
            await self.ha.call(domain, service, {"entity_id": entity})
        except Exception as err:  # noqa: BLE001
            self.event(f"Could not switch {entity} {'on' if on else 'off'}: {err}", "error")
            return False
        self.commanded[entity] = {"on": on, "at": self.wall()}
        self.recent.append((self.wall(), f"{'Opened' if on else 'Closed'} {self.label(entity)}"))
        self.recent = self.recent[-10:]
        self.dirty = True
        if why:
            self.event(why)
        return True

    async def actuate(self, want: dict[str, bool], resync_now: bool = False) -> None:
        pumps = self.pump_entities()
        now = self.wall()

        def needs(ent, on):
            c = self.commanded.get(ent)
            if c is None:          # never switched by this add-on: only act when it must open
                return on
            return c["on"] != on

        order = (
            [(e, False) for e in pumps if want.get(e) is False],             # 1 pumps off first
            [(e, True) for e in want if e not in pumps and want[e]],         # 2 blocks on
            [(e, False) for e in want if e not in pumps and not want[e]],    # 3 blocks off
            [(e, True) for e in pumps if want.get(e)],                       # 4 pumps on last
        )
        for group in order:
            for ent, on in group:
                if needs(ent, on):
                    await self.send(ent, on)
        # resync: entity disagrees with what we last sent for a while -> send again
        for ent, c in self.commanded.items():
            if ent not in want:
                continue
            actual = is_on(self.states.get(ent, {}).get("state"))
            if actual is None or actual == c["on"]:
                c.pop("mismatch_since", None)
                continue
            since = c.setdefault("mismatch_since", now)
            if resync_now or (now - since >= RESYNC_AFTER_S and now - c.get("at", 0) >= RESYNC_AFTER_S):
                if await self.send(ent, c["on"]):
                    self.event(f"{ent} was {'off' if c['on'] else 'on'} but should be {'on' if c['on'] else 'off'}: sent the command again.", "warn")

    # ------------------------------------------------------------ scheduler
    def schedule(self) -> None:
        now = self.now_dt()
        for p in self.cfg["programs"]:
            if not p.get("enabled", True) or p.get("mode") == "loop":
                continue
            for t, days in model.start_rows(p):
                try:
                    m = model.tmin(t)
                except ValueError:
                    continue
                for back in (0, 1):   # today, or yesterday's start shortly before midnight
                    day = (now - timedelta(days=back)).date()
                    at = now.replace(year=day.year, month=day.month, day=day.day, hour=m // 60, minute=m % 60, second=0, microsecond=0)
                    if not (at <= now < at + timedelta(minutes=10)):
                        continue
                    if model.DAYS[day.weekday()] not in days or not model.in_season(day, p.get("season")):
                        continue
                    key = f"{p['id']}|{at.isoformat()}"
                    if key in self.fired:
                        continue
                    self.fired[key] = self.wall()
                    self.dirty = True
                    ok, msg = self.start_program(p, source="schedule")
                    if not ok:
                        self.event(f"Skipped the {t} start of {p['name']}: {msg}", "warn")
        cutoff = self.wall() - 3 * 86400
        for k in [k for k, v in self.fired.items() if v < cutoff]:
            self.fired.pop(k)

    # ------------------------------------------------------------ main tick
    async def tick(self, cfg: dict) -> None:
        self.cfg = cfg
        now = self.wall()
        try:
            self.states = await self.ha.states()
            ha_ok = True
        except Exception as err:  # noqa: BLE001
            ha_ok = False
            if self.ha_ok:
                self.event(f"Lost contact with Home Assistant ({err}). Programs are held where they are.", "error")
        gap = now - self.last_tick if self.last_tick else None
        resumed = ha_ok and (not self.ha_ok or gap is None or gap > GAP_S)
        self.last_tick = now
        if not ha_ok:
            self.ha_ok = False
            return
        if resumed and self.runs and gap is not None:
            self.event("Back in contact: carrying on from where each program was.")
        if resumed:
            for r in self.runs:
                if r["state"] == "running" and not self._blocks_still_open(r):
                    r["opened_at"] = None         # pump waits for the blocks again
        self.ha_ok = True
        dt = 0.0 if resumed or gap is None else gap

        for run in list(self.runs):
            geo = self.geometry(run)
            if run["state"] == "running":
                run["e"] += dt
                # loop-until: reached the stop time
                if run.get("stop_at") and now >= run["stop_at"] and not run.get("stop_handled"):
                    run["stop_handled"] = True
                    if run["prog"].get("end_behavior") == "stop_now":
                        self.stop_run(run, "reached its stop time")
                    else:
                        g = self.current_g(run, geo)
                        run["last_g"] = g if g is not None else int(run["e"] // max(1, geo["period"])) * geo["n"]
                        self.event(f"{run['name']} reached its stop time: finishing the current step.")
                if run["state"] == "running" and run["e"] >= self.end_e(run, geo):
                    self.runs.remove(run)
                    self.event(f"Finished {run['name']}.")
                    continue
            if run["state"] in ("pausing", "stopping") and now - (run["phase_at"] or now) >= self.lead(run):
                if run["state"] == "stopping":
                    self.runs.remove(run)
                    self.event(f"Stopped {run['name']}.")
                else:
                    run["state"] = "paused"
            self.dirty = True

        self.schedule()
        await self.actuate(self.desired(), resync_now=resumed)
        for r in self.runs:
            if r["state"] == "running" and r.get("opened_at") is None and self.run_wants(r)[0]:
                r["opened_at"] = now
        self.save()

    def _blocks_still_open(self, run: dict) -> bool:
        blocks = model.by_id(self.cfg["blocks"])
        ents = [blocks[b]["entity"] for b in self.run_wants(run)[0] if b in blocks and blocks[b].get("entity")]
        return bool(ents) and all(is_on(self.states.get(e, {}).get("state")) is True for e in ents)

    # ------------------------------------------------------------ manual commands
    async def block_command(self, block: dict, on: bool) -> tuple[bool, str]:
        ent = block.get("entity")
        if not ent:
            return False, f"Block {block['number']} has no valve switch."
        want = self.desired()
        in_runs = [r for r in self.runs if block["id"] in self.run_wants(r)[0]]
        if on:
            if want.get(ent):          # already wanted open: just resend
                self.overrides.pop(ent, None)
                await self.send(ent, True, f"Sent open to block {block['number']} again.")
                return True, "Sent the open signal again."
            ok, msg = self.start_block(block)
            if not ok:
                self.overrides.pop(ent, None)
                await self.send(ent, True, f"Sent open to block {block['number']}.")
            return True, f"Opening block {block['number']} for {model.fmt_dur(float(block.get('default_min', 60)) * 60)}."
        # close
        manual = [r for r in in_runs if r["source"] == "block"]
        for r in manual:
            self.stop_run(r)
        others = [r for r in in_runs if r["source"] != "block"]
        if others:
            self.overrides[ent] = {"on": False}
            self.dirty = True
        if not manual:
            await self.send(ent, False, f"Sent close to block {block['number']}" +
                            (f" (held closed while {', '.join(r['name'] for r in others)} needs it)." if others else "."))
            return True, "Sent the close signal." + (" It stays closed for the rest of this step." if others else "")
        return True, f"Closing block {block['number']} (pump first)."

    async def pump_command(self, ml: dict, on: bool) -> tuple[bool, str]:
        ents = [e for e in ml.get("pump_entities", []) if e]
        if not ents:
            return False, "This mainline has no pump or main valve."
        for e in ents:
            if not on and self.desired().get(e):
                self.overrides[e] = {"on": False}
            else:
                self.overrides.pop(e, None)
            await self.send(e, on, f"Sent {'open' if on else 'close'} to {e}.")
        return True, f"Sent the {'open' if on else 'close'} signal to the pump."

    def label(self, entity: str) -> str:
        for b in self.cfg["blocks"]:
            if b.get("entity") == entity:
                return f"block {b['number']}"
        for ml in self.cfg["mainlines"]:
            if entity in ml.get("pump_entities", []):
                return f"main on {ml['name']}"
        return entity

    def activity(self) -> list[str]:
        """Short lines describing valve switching happening right now."""
        now = self.wall()
        out = []
        for run in self.runs:
            mlname = run["ml"].get("name", "")
            ps = float(run["ml"].get("pump_start_s", 0) or 0)
            pe = float(run["ml"].get("pump_stop_s", 0) or 0)
            if run["state"] in ("pausing", "stopping") and run.get("phase_at"):
                left = self.lead(run) - (now - run["phase_at"])
                if left > 0:
                    out.append(f"Waiting {math.ceil(left)} s to close blocks on {mlname}")
            elif run["state"] == "running":
                opened = run.get("opened_at")
                if opened is not None and now - opened < ps:
                    out.append(f"Waiting {math.ceil(ps - (now - opened))} s to open main on {mlname}")
                to_end = self.end_e(run) - run["e"]
                if to_end != math.inf:
                    if pe < to_end <= pe + 10:
                        out.append(f"Closing main on {mlname} in {math.ceil(to_end - pe)} s")
                    elif 0 < to_end <= pe:
                        out.append(f"Waiting {math.ceil(to_end)} s to close blocks on {mlname}")
        out += [t for at, t in self.recent if now - at <= 4]
        seen = set()
        return [x for x in out if not (x in seen or seen.add(x))][:3]

    # ------------------------------------------------------------ live view data
    def run_view(self, run: dict) -> dict:
        geo = self.geometry(run)
        now = self.wall()
        blocks = model.by_id(self.cfg["blocks"])
        held = run["state"] != "running"
        n = geo["n"]
        cur = self.current_g(run, geo)
        active = set(self.active_steps(run, geo=geo))
        lg = self.last_g(run, geo)
        base_g = (cur // n) * n if cur is not None else int(run["e"] // max(1, geo["period"])) * n
        mode = run["prog"].get("mode", "once")
        if mode == "once":
            base_g = 0

        def at(e):
            return None if held else now + (e - run["e"])

        steps = []
        for i in range(n):
            g = base_g + i
            pl = geo["plan"][i]
            s, en = self.g_start(run, g, geo), self.g_end(run, g, geo)
            if lg is not None and g > lg:
                status = "skipped"
            elif g in active:
                status = "running"
            elif en <= run["e"]:
                status = "done"
            else:
                status = "later"
            steps.append({
                "n": i + 1, "status": status, "duration_s": pl["duration"], "overlap_s": pl["overlap"],
                "blocks": [{"id": b, "number": blocks.get(b, {}).get("number", "?"), "name": blocks.get(b, {}).get("name", "")} for b in pl["blocks"]],
                "starts_at": at(s), "ends_at": at(min(en, self.end_e(run, geo))),
                "left_s": max(0.0, en - run["e"]) if status == "running" else None,
            })
        nxt = next((st for st in steps if st["status"] == "later"), None)
        if nxt:
            nxt["status"] = "next"
        end = self.end_e(run, geo)
        return {
            "id": run["id"], "program_id": run["program_id"], "name": run["name"], "source": run["source"],
            "mode": mode, "state": run["state"], "started_at": run["started_at"],
            "elapsed_s": run["e"], "loop": (cur // n + 1) if cur is not None and mode != "once" else None,
            "period_s": geo["period"], "step": (cur % n + 1) if cur is not None else None, "steps": steps,
            "ends_at": None if end == math.inf else (None if held else now + end - run["e"]),
            "stop_at": run.get("stop_at"), "finishing": run.get("last_g") is not None,
            "next_loop_at": None if mode == "once" or held or lg is not None and base_g + n > lg else now + (base_g // n + 1) * geo["period"] - run["e"],
        }

