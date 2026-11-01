"""Irrigation Programs: data model, validation and timing helpers.

Pure logic, no I/O, so it can be unit tested on its own.
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
OVERLAP_STEP_S = 5
SNAKE = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)*$")
TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def empty_config() -> dict:
    return {"version": 2, "mainlines": [], "blocks": [], "programs": []}


def migrate(cfg: dict) -> dict:
    """Bring older saved configs up to date."""
    for b in cfg.get("blocks", []):
        if "default_min" not in b:
            b["default_min"] = 60
        b.pop("max_min", None)
    cfg["version"] = 2
    return cfg


# ---------------------------------------------------------------- helpers

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


def by_id(items: list[dict]) -> dict[str, dict]:
    return {i["id"]: i for i in items}


def step_overlap(step: dict, mainline: dict | None) -> int:
    if step.get("overlap_s") is not None and step.get("overlap_s") != "":
        return int(step["overlap_s"])
    return int((mainline or {}).get("overlap_s", 0) or 0)


def step_plan(program: dict, mainline: dict | None) -> list[dict]:
    """Start offset (s), duration (s) and overlap (s) of every step within one loop."""
    out, t = [], 0
    steps = program.get("steps", [])
    for i, st in enumerate(steps):
        d = int(round(float(st.get("duration_min", 0)) * 60))
        o = step_overlap(st, mainline)
        if i == len(steps) - 1 and program.get("mode", "once") == "once":
            o = 0
        o = max(0, min(o, d - 1)) if d > 1 else 0
        out.append({"index": i, "offset": t, "duration": d, "overlap": o, "blocks": list(st.get("blocks", []))})
        t += d - o
    return out


def loop_period(program: dict, mainline: dict | None) -> int:
    """Seconds from the start of one loop to the start of the next."""
    return sum(p["duration"] - p["overlap"] for p in step_plan(program, mainline))


def run_length(program: dict, mainline: dict | None) -> int:
    """Seconds one pass of the program takes."""
    plan = step_plan(program, mainline)
    if not plan:
        return 0
    return plan[-1]["offset"] + plan[-1]["duration"]


def window_seconds(window: dict) -> int:
    w = (tmin(window["end"]) - tmin(window["start"])) % 1440
    return (w or 1440) * 60


def in_season(day: date, season: dict | None) -> bool:
    season = season or {}
    a, b = season.get("from"), season.get("until")
    if not a or not b:
        return True
    md = day.strftime("%m-%d")
    return a <= md <= b if a <= b else (md >= a or md <= b)


def next_time(after: datetime, hhmm: str) -> datetime:
    """Next datetime at HH:MM strictly after `after` (same tz)."""
    m = tmin(hhmm)
    cand = after.replace(hour=m // 60, minute=m % 60, second=0, microsecond=0)
    if cand <= after:
        cand += timedelta(days=1)
    return cand


def start_rows(program: dict) -> list[tuple[str, list[str]]]:
    mode = program.get("mode", "once")
    if mode == "once":
        return [(r["time"], r.get("days", [])) for r in program.get("starts", [])]
    if mode == "until":
        w = program.get("window") or {}
        return [(w["start"], w.get("days", []))] if w.get("start") else []
    return []


def upcoming(program: dict, now: datetime, days: int = 7, limit: int = 3) -> list[datetime]:
    """Next scheduled start times of a program."""
    if not program.get("enabled", True):
        return []
    out = []
    for d in range(days + 1):
        day = (now + timedelta(days=d)).date()
        if not in_season(day, program.get("season")):
            continue
        for t, dlist in start_rows(program):
            if DAYS[day.weekday()] not in dlist:
                continue
            m = tmin(t)
            at = now.replace(year=day.year, month=day.month, day=day.day, hour=m // 60, minute=m % 60, second=0, microsecond=0)
            if at > now:
                out.append(at)
    return sorted(out)[:limit]


# ---------------------------------------------------------------- validation

def validate(cfg: dict) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    mls = by_id(cfg.get("mainlines", []))
    blocks = by_id(cfg.get("blocks", []))

    for ml in cfg.get("mainlines", []):
        name = ml.get("name") or "(no name)"
        if not str(ml.get("name", "")).strip():
            errors.append("A mainline has no name.")
        if not SNAKE.match(ml.get("id", "")):
            errors.append(f"Mainline '{name}' has an invalid id.")
        for key, label in (("pump_start_s", "pump start delay"), ("pump_stop_s", "pump stop lead time"), ("overlap_s", "overlap")):
            try:
                if int(ml.get(key, 0) or 0) < 0:
                    raise ValueError
            except (TypeError, ValueError):
                errors.append(f"Mainline '{name}': {label} must be 0 or more seconds.")
        if not [e for e in ml.get("pump_entities", []) if e]:
            warnings.append(f"Mainline '{name}' has no pump or main valve set.")

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
            if float(b.get("default_min", 0)) < 1:
                raise ValueError
        except (TypeError, ValueError):
            errors.append(f"{label}: default runtime must be at least 1 minute.")

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
            ovl = step_overlap(st, ml)
            if not (i == len(steps) and mode == "once"):
                if ovl < 0 or ovl % OVERLAP_STEP_S:
                    errors.append(f"{tag}, step {i}: overlap must be a multiple of {OVERLAP_STEP_S} seconds.")
                if dur_s and ovl >= dur_s:
                    errors.append(f"{tag}, step {i}: overlap must be shorter than the step.")
            for bid in sb:
                b = blocks.get(bid)
                if not b:
                    errors.append(f"{tag}, step {i} uses a block that no longer exists.")
                elif ml and b.get("mainline") != ml["id"]:
                    errors.append(f"{tag}, step {i}: block {b['number']} is on another mainline.")
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
        elif season.get("from") and not all(re.match(r"^\d{2}-\d{2}$", season[k]) for k in ("from", "until")):
            errors.append(f"{tag}: season dates must look like MM-DD.")
        if ml and steps and not any(e.startswith(tag) for e in errors):
            pump_time = int(ml.get("pump_start_s", 0) or 0) + int(ml.get("pump_stop_s", 0) or 0)
            if mode == "once" and pump_time >= run_length(p, ml):
                warnings.append(f"{tag} is shorter than the pump start + stop times, so the pump won't switch on.")

    if not errors:
        warnings.extend(self_clashes(cfg))
    return errors, warnings


def self_clashes(cfg: dict) -> list[str]:
    """Programs still running when their own next start comes round (that start is skipped)."""
    mls = by_id(cfg.get("mainlines", []))
    msgs = []
    week = 7 * 1440
    for p in cfg.get("programs", []):
        if not p.get("enabled", True) or p.get("mode") != "once":
            continue
        length = math.ceil(run_length(p, mls.get(p.get("mainline"))) / 60)
        occ = sorted(DAYS.index(d) * 1440 + tmin(r["time"]) for r in p.get("starts", []) for d in r.get("days", []))
        for i, s in enumerate(occ):
            nxt = occ[(i + 1) % len(occ)] + (week if i + 1 == len(occ) else 0)
            if len(occ) > 1 and s + length > nxt:
                msgs.append(f"'{p['name']}' takes {fmt_dur(length * 60)}, so it is still running at its next start time. That start will be skipped.")
                break
            if len(occ) == 1 and length > week:
                msgs.append(f"'{p['name']}' runs longer than a week.")
    return msgs
