"""Irrigation Programs: data model, validation and Irrigation Unlimited config generation.

Pure logic, no I/O, so it can be unit tested on its own.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
GRANULARITY_S = 5          # Irrigation Unlimited time boundary, overlaps must be a multiple
LOOP_REPEAT_SPAN_S = 7 * 86400   # "loop constantly" is re-armed at least once a week
MAX_REPEAT = 200
SNAKE = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)*$")
TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def empty_config() -> dict:
    return {"version": 1, "mainlines": [], "blocks": [], "programs": []}


# ---------------------------------------------------------------- helpers

def hms(seconds: int) -> str:
    """Seconds -> 'H:MM:SS' (negative allowed), the format Irrigation Unlimited accepts."""
    sign = "-" if seconds < 0 else ""
    s = abs(int(seconds))
    return f"{sign}{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def tmin(value: str) -> int:
    """'HH:MM' -> minutes after midnight."""
    m = TIME_RE.match(value or "")
    if not m:
        raise ValueError(value)
    return int(m.group(1)) * 60 + int(m.group(2))


def fmt_dur(seconds: float) -> str:
    seconds = int(round(seconds))
    h, m = seconds // 3600, (seconds % 3600) // 60
    return f"{h}h {m:02d}m" if h else f"{m}m"


def season_to_iu(value: str) -> str | None:
    """'MM-DD' -> 'DD Mon' (Irrigation Unlimited from/until format)."""
    if not value:
        return None
    m = re.match(r"^(\d{2})-(\d{2})$", value)
    if not m:
        raise ValueError(value)
    month, day = int(m.group(1)), int(m.group(2))
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        raise ValueError(value)
    return f"{day:02d} {MONTHS[month - 1].capitalize()}"


def by_id(items: list[dict]) -> dict[str, dict]:
    return {i["id"]: i for i in items}


def step_overlap(program: dict, step: dict, mainline: dict | None) -> int:
    if step.get("overlap_s") is not None and step.get("overlap_s") != "":
        return int(step["overlap_s"])
    return int((mainline or {}).get("overlap_s", 0) or 0)


def step_plan(program: dict, mainline: dict | None) -> list[dict]:
    """Start offset (s), duration (s) and overlap (s) of every step within one loop."""
    out, t = [], 0
    steps = program.get("steps", [])
    for i, st in enumerate(steps):
        d = int(round(float(st.get("duration_min", 0)) * 60))
        o = step_overlap(program, st, mainline)
        last = i == len(steps) - 1
        if last and program.get("mode", "once") == "once":
            o = 0
        out.append({"index": i, "offset": t, "duration": d, "overlap": o})
        t += d - o
    return out


def loop_period(program: dict, mainline: dict | None) -> int:
    """Seconds from the start of one loop to the start of the next."""
    plan = step_plan(program, mainline)
    return sum(p["duration"] - p["overlap"] for p in plan)


def run_length(program: dict, mainline: dict | None) -> int:
    """Seconds one pass of the program takes (once mode)."""
    plan = step_plan(program, mainline)
    if not plan:
        return 0
    return plan[-1]["offset"] + plan[-1]["duration"]


def window_seconds(window: dict) -> int:
    w = (tmin(window["end"]) - tmin(window["start"])) % 1440
    return (w or 1440) * 60


def repeat_count(program: dict, mainline: dict | None) -> int:
    mode = program.get("mode", "once")
    period = loop_period(program, mainline)
    if mode == "once" or period <= 0:
        return 1
    span = window_seconds(program["window"]) if mode == "until" else LOOP_REPEAT_SPAN_S
    return max(1, min(MAX_REPEAT, math.ceil(span / period)))


# ---------------------------------------------------------------- validation

def validate(cfg: dict) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    mls = by_id(cfg.get("mainlines", []))
    blocks = by_id(cfg.get("blocks", []))

    for ml in cfg.get("mainlines", []):
        if not str(ml.get("name", "")).strip():
            errors.append("A mainline has no name.")
        if not SNAKE.match(ml.get("id", "")):
            errors.append(f"Mainline '{ml.get('name')}' has an invalid id.")
        for key, label in (("pump_start_s", "pump start delay"), ("pump_stop_s", "pump stop delay"), ("overlap_s", "overlap")):
            try:
                v = int(ml.get(key, 0) or 0)
                if v < 0:
                    raise ValueError
            except (TypeError, ValueError):
                errors.append(f"Mainline '{ml.get('name')}': {label} must be 0 or more seconds.")

    numbers: dict[int, str] = {}
    for b in cfg.get("blocks", []):
        label = f"Block {b.get('number')}"
        try:
            n = int(b.get("number"))
            if n < 1:
                raise ValueError
        except (TypeError, ValueError):
            errors.append(f"A block has an invalid number ({b.get('number')!r}).")
            continue
        if n in numbers:
            errors.append(f"Block number {n} is used twice.")
        numbers[n] = b["id"]
        if not SNAKE.match(b.get("id", "")):
            errors.append(f"{label} has an invalid id.")
        if b.get("mainline") not in mls:
            errors.append(f"{label} is not on a mainline.")
        if not b.get("entity"):
            warnings.append(f"{label} has no valve switch, so it won't open anything.")
        try:
            if float(b.get("max_min", 0)) <= 0:
                raise ValueError
        except (TypeError, ValueError):
            errors.append(f"{label}: max runtime must be more than 0.")

    for p in cfg.get("programs", []):
        name = p.get("name") or "(no name)"
        tag = f"Program '{name}'"
        if not str(p.get("name", "")).strip():
            errors.append("A program has no name.")
        if not SNAKE.match(p.get("id", "")):
            errors.append(f"{tag} has an invalid id.")
        ml = mls.get(p.get("mainline"))
        if not ml:
            errors.append(f"{tag} is not on a mainline.")
        steps = p.get("steps", [])
        if not steps:
            errors.append(f"{tag} has no steps.")
        mode = p.get("mode", "once")
        if mode not in ("once", "until", "loop"):
            errors.append(f"{tag} has an unknown run mode.")
        for i, st in enumerate(steps, 1):
            sb = st.get("blocks", [])
            if not sb:
                errors.append(f"{tag}, step {i} has no blocks.")
            if len(set(sb)) != len(sb):
                errors.append(f"{tag}, step {i} lists a block twice.")
            try:
                dur_s = float(st.get("duration_min", 0)) * 60
                if dur_s < 60:
                    raise ValueError
            except (TypeError, ValueError):
                errors.append(f"{tag}, step {i}: duration must be at least 1 minute.")
                dur_s = 0
            ovl = step_overlap(p, st, ml)
            is_last = i == len(steps)
            if not (is_last and mode == "once"):
                if ovl < 0 or ovl % GRANULARITY_S:
                    errors.append(f"{tag}, step {i}: overlap must be a multiple of {GRANULARITY_S} seconds.")
                if dur_s and ovl >= dur_s:
                    errors.append(f"{tag}, step {i}: overlap must be shorter than the step.")
            for bid in sb:
                b = blocks.get(bid)
                if not b:
                    errors.append(f"{tag}, step {i} uses a block that no longer exists.")
                    continue
                if ml and b.get("mainline") != ml["id"]:
                    errors.append(f"{tag}, step {i}: block {b['number']} is on another mainline.")
                try:
                    if dur_s > float(b.get("max_min", 0)) * 60:
                        errors.append(
                            f"{tag}, step {i} runs {fmt_dur(dur_s)} but block {b['number']}'s "
                            f"max runtime is {fmt_dur(float(b['max_min']) * 60)}."
                        )
                except (TypeError, ValueError):
                    pass
            if i > 1 and set(sb) & set(steps[i - 2].get("blocks", [])):
                common = sorted(blocks[x]["number"] for x in set(sb) & set(steps[i - 2].get("blocks", [])) if x in blocks)
                warnings.append(f"{tag}: block(s) {', '.join(map(str, common))} are in step {i - 1} and step {i} back to back.")

        if mode == "once":
            for r, row in enumerate(p.get("starts", []), 1):
                if not TIME_RE.match(row.get("time", "")):
                    errors.append(f"{tag}: start time {r} is not a valid time.")
                if not row.get("days"):
                    errors.append(f"{tag}: start time {r} has no days ticked.")
        elif mode == "until":
            w = p.get("window") or {}
            if not TIME_RE.match(w.get("start", "")) or not TIME_RE.match(w.get("end", "")):
                errors.append(f"{tag}: set both a start and a stop time.")
            elif not w.get("days"):
                errors.append(f"{tag}: tick at least one day for the run window.")
            if p.get("end_behavior") not in ("finish_step", "stop_now"):
                errors.append(f"{tag}: choose what happens at the stop time.")
        season = p.get("season") or {}
        if bool(season.get("from")) != bool(season.get("until")):
            errors.append(f"{tag}: set both season dates or neither.")
        else:
            try:
                season_to_iu(season.get("from", ""))
                season_to_iu(season.get("until", ""))
            except ValueError:
                errors.append(f"{tag}: season dates must look like MM-DD.")

    if not errors:
        warnings.extend(find_clashes(cfg))
    return errors, warnings


def _occurrences(p: dict, ml: dict | None) -> list[tuple[int, int, str]]:
    """Run intervals in minutes-of-week: (start, end, label)."""
    out = []
    if not p.get("enabled", True):
        return out
    mode = p.get("mode", "once")
    if mode == "once":
        length = math.ceil(run_length(p, ml) / 60)
        for row in p.get("starts", []):
            for d in row.get("days", []):
                s = DAYS.index(d) * 1440 + tmin(row["time"])
                out.append((s, s + length, f"{d.capitalize()} {row['time']}"))
    elif mode == "until":
        w = p["window"]
        length = window_seconds(w) // 60
        for d in w.get("days", []):
            s = DAYS.index(d) * 1440 + tmin(w["start"])
            out.append((s, s + length, f"{d.capitalize()} {w['start']}"))
    return out


def find_clashes(cfg: dict) -> list[str]:
    mls = by_id(cfg.get("mainlines", []))
    progs = [p for p in cfg.get("programs", []) if p.get("enabled", True)]
    msgs = []
    week = 7 * 1440
    for i, a in enumerate(progs):
        occ_a = _occurrences(a, mls.get(a.get("mainline")))
        # a program running into its own next start
        for j, (s1, e1, lab1) in enumerate(occ_a):
            for s2, e2, _ in occ_a[j + 1:]:
                for sh in (-week, 0, week):
                    if s1 < e2 + sh and s2 + sh < e1:
                        msgs.append(f"'{a['name']}' is still running when its next start ({lab1}) comes round.")
                        break
        for b in progs[i + 1:]:
            if a.get("mainline") != b.get("mainline"):
                continue
            occ_b = _occurrences(b, mls.get(b.get("mainline")))
            hit = None
            for s1, e1, lab in occ_a:
                for s2, e2, _ in occ_b:
                    for sh in (-week, 0, week):
                        if s1 < e2 + sh and s2 + sh < e1:
                            hit = lab
                            break
                    if hit:
                        break
                if hit:
                    break
            if hit:
                msgs.append(f"'{a['name']}' and '{b['name']}' overlap on the same mainline ({hit}).")
    return sorted(set(msgs))


# ---------------------------------------------------------------- generation

def generate(cfg: dict) -> tuple[dict, dict]:
    """Build the Irrigation Unlimited config and the entity mapping.

    Returns (iu_config, mapping). mapping holds the position numbers Irrigation
    Unlimited uses in its entity ids (binary_sensor.irrigation_unlimited_c1_s2).
    """
    mls = cfg.get("mainlines", [])
    blocks = cfg.get("blocks", [])
    programs = cfg.get("programs", [])
    mapping = {"mainlines": {}, "blocks": {}, "programs": {}}
    controllers = []

    for ml in mls:
        ml_blocks = sorted((b for b in blocks if b.get("mainline") == ml["id"]), key=lambda b: int(b["number"]))
        if not ml_blocks:
            continue
        c_index = len(controllers) + 1
        ctrl: dict = {"name": ml["name"], "controller_id": ml["id"]}
        pumps = [e for e in ml.get("pump_entities", []) if e]
        if pumps:
            ctrl["entity_id"] = pumps if len(pumps) > 1 else pumps[0]
            ctrl["preamble"] = hms(int(ml.get("pump_start_s", 0) or 0))
            ctrl["postamble"] = hms(int(ml.get("pump_stop_s", 0) or 0))
        zones = []
        for z_index, b in enumerate(ml_blocks, 1):
            z = {
                "name": f"{b['number']} {b.get('name', '')}".strip(),
                "zone_id": b["id"],
                "enabled": bool(b.get("enabled", True)),
                "maximum": hms(int(round(float(b.get("max_min", 300)) * 60))),
            }
            if b.get("entity"):
                z["entity_id"] = b["entity"]
            zones.append(z)
            mapping["blocks"][b["id"]] = {"c": c_index, "z": z_index}
        ctrl["zones"] = zones

        seqs = []
        for p in (p for p in programs if p.get("mainline") == ml["id"]):
            s_index = len(seqs) + 1
            plan = step_plan(p, ml)
            seq: dict = {
                "name": p["name"],
                "sequence_id": p["id"],
                "enabled": bool(p.get("enabled", True)),
                "repeat": repeat_count(p, ml),
            }
            seq_zones = []
            for st, pl in zip(p["steps"], plan):
                seq_zones.append({
                    "zone_id": list(st["blocks"]),
                    "duration": hms(pl["duration"]),
                    "delay": hms(-pl["overlap"]),
                })
            seq["zones"] = seq_zones
            schedules = []
            season = p.get("season") or {}
            s_from, s_until = season_to_iu(season.get("from", "")), season_to_iu(season.get("until", ""))
            mode = p.get("mode", "once")
            rows = []
            if mode == "once":
                rows = [(r["time"], r["days"]) for r in p.get("starts", [])]
            elif mode == "until":
                rows = [(p["window"]["start"], p["window"]["days"])]
            for n, (t, days) in enumerate(rows, 1):
                sch: dict = {"name": f"Start {t}" if mode == "once" else f"Window {t}-{p['window']['end']}", "time": t}
                days = [d for d in DAYS if d in days]
                if len(days) < 7:
                    sch["weekday"] = days
                if s_from and s_until:
                    sch["from"], sch["until"] = s_from, s_until
                schedules.append(sch)
            if schedules:
                seq["schedules"] = schedules
            seqs.append(seq)
            mapping["programs"][p["id"]] = {"c": c_index, "s": s_index}
        if seqs:
            ctrl["sequences"] = seqs
        controllers.append(ctrl)
        mapping["mainlines"][ml["id"]] = {"c": c_index}

    if not controllers:
        controllers = [{"name": "Not set up yet", "enabled": False, "zones": [{"name": "Placeholder"}]}]

    iu = {"granularity": GRANULARITY_S, "refresh_interval": 10, "controllers": controllers}
    return iu, mapping


def entity(kind: str, m: dict) -> str:
    base = "binary_sensor.irrigation_unlimited"
    if kind == "controller":
        return f"{base}_c{m['c']}_m"
    if kind == "sequence":
        return f"{base}_c{m['c']}_s{m['s']}"
    return f"{base}_c{m['c']}_z{m['z']}"


# ---------------------------------------------------------------- live helpers

def step_at(program: dict, mainline: dict | None, elapsed_s: float) -> dict | None:
    """Which step is running `elapsed_s` seconds after the run started."""
    plan = step_plan(program, mainline)
    period = loop_period(program, mainline)
    if not plan or period <= 0 or elapsed_s < 0:
        return None
    loop = int(elapsed_s // period)
    t = elapsed_s - loop * period
    if program.get("mode", "once") == "once":
        loop, t = 0, elapsed_s
    current = None
    for pl in plan:
        if pl["offset"] <= t:
            current = pl
    if current is None:
        return None
    left = current["offset"] + current["duration"] - t
    return {"step": current["index"] + 1, "loop": loop + 1, "step_left_s": max(0, left),
            "step_duration_s": current["duration"], "steps": len(plan)}


def next_time(after: datetime, hhmm: str) -> datetime:
    """Next datetime at HH:MM strictly after `after` (same tz)."""
    m = tmin(hhmm)
    cand = after.replace(hour=m // 60, minute=m % 60, second=0, microsecond=0)
    if cand <= after:
        cand += timedelta(days=1)
    return cand
