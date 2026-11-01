"""Irrigation Programs add-on: web server, apply/setup logic and the background runner."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import yaml
from aiohttp import web

import model
from ha import HA, HAError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("irrigation_programs")

HERE = os.path.dirname(os.path.abspath(__file__))
WWW = os.path.join(HERE, "www")
DATA = os.environ.get("IP_DATA", "/data")
CONF_DIR = os.environ.get("IP_CONFIG_DIR") or ("/homeassistant" if os.path.isdir("/homeassistant") else "/config")
IU_FILE = "irrigation_programs.yaml"
INCLUDE_LINE = f"irrigation_unlimited: !include {IU_FILE}"
INCLUDE_RE = re.compile(r"^irrigation_unlimited:\s*!include\s+irrigation_programs\.yaml\s*$", re.M)
OTHER_IU_RE = re.compile(r"^irrigation_unlimited:", re.M)
VALVE_DOMAINS = ("switch.", "valve.", "input_boolean.", "light.")


# ---------------------------------------------------------------- storage

def _path(name: str) -> str:
    return os.path.join(DATA, name)


def load_json(name: str, default):
    try:
        with open(_path(name), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(name: str, data) -> None:
    os.makedirs(DATA, exist_ok=True)
    tmp = _path(name) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, _path(name))


class _Dumper(yaml.SafeDumper):
    pass


def _str(dumper, value):
    style = '"' if (":" in value or value.strip() != value or value[:1].isdigit()) else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _str)


def iu_yaml(cfg: dict) -> tuple[str, dict]:
    iu, mapping = model.generate(cfg)
    head = ("# Written by the Irrigation Programs add-on. Do not edit by hand:\n"
            "# change programs in the Irrigation panel instead, this file is overwritten.\n")
    return head + yaml.dump(iu, Dumper=_Dumper, sort_keys=False, allow_unicode=True), mapping


# ---------------------------------------------------------------- app state

class App:
    def __init__(self) -> None:
        self.ha = HA()
        self.cfg = load_json("config.json", model.empty_config())
        self.applied = load_json("applied.json", None)       # {"config", "mapping", "at"}
        self.rt = load_json("runtime.json", {"looping": {}, "stop_at": {}})
        self.states: dict[str, dict] = {}
        self.states_at = 0.0
        self.tz = timezone.utc
        self.last_error = ""
        self.pending_cancel: dict[str, float] = {}
        self.last_start: dict[str, float] = {}

    # ---- helpers
    def now(self) -> datetime:
        return datetime.now(self.tz)

    def save_rt(self) -> None:
        save_json("runtime.json", self.rt)

    async def refresh_states(self, force: bool = False) -> None:
        if force or time.time() - self.states_at > 2:
            try:
                self.states = await self.ha.states()
                self.states_at = time.time()
                self.last_error = ""
            except HAError as err:
                self.last_error = str(err)

    def st(self, eid: str | None) -> dict:
        return self.states.get(eid or "", {}) or {}

    def applied_cfg(self) -> dict:
        return (self.applied or {}).get("config") or model.empty_config()

    def mp(self) -> dict:
        return (self.applied or {}).get("mapping") or {"mainlines": {}, "blocks": {}, "programs": {}}

    def seq_entity(self, pid: str) -> str | None:
        m = self.mp()["programs"].get(pid)
        return model.entity("sequence", m) if m else None

    def ctrl_entity(self, mlid: str) -> str | None:
        m = self.mp()["mainlines"].get(mlid)
        return model.entity("controller", m) if m else None

    # ---- setup status
    async def setup_status(self) -> dict:
        conf_path = os.path.join(CONF_DIR, "configuration.yaml")
        try:
            with open(conf_path, encoding="utf-8") as f:
                conf = f.read()
        except OSError:
            conf = ""
        include = bool(INCLUDE_RE.search(conf))
        other = (not include) and bool(OTHER_IU_RE.search(conf))
        installed = os.path.isdir(os.path.join(CONF_DIR, "custom_components", "irrigation_unlimited"))
        loaded = False
        ha_ok = True
        try:
            loaded = "irrigation_unlimited" in await self.ha.services()
        except HAError as err:
            ha_ok = False
            self.last_error = str(err)
        return {"ha_ok": ha_ok, "iu_installed": installed, "include": include, "other_iu_config": other,
                "iu_loaded": loaded, "config_dir": CONF_DIR, "error": self.last_error,
                "ready": ha_ok and installed and include and loaded}

    # ---- apply
    def running_programs(self) -> list[str]:
        out = []
        for pid in self.mp()["programs"]:
            if self.st(self.seq_entity(pid)).get("state") == "on":
                out.append(pid)
        return out

    async def apply(self, cfg: dict, force: bool) -> dict:
        errors, warnings = model.validate(cfg)
        if errors:
            return {"ok": False, "errors": errors, "warnings": warnings}
        await self.refresh_states(force=True)
        running = self.running_programs()
        if running and not force:
            names = [p["name"] for p in self.applied_cfg()["programs"] if p["id"] in running]
            return {"ok": False, "confirm": True, "running": names, "warnings": warnings}

        text, mapping = iu_yaml(cfg)
        target = os.path.join(CONF_DIR, IU_FILE)
        if os.path.exists(target):
            shutil.copyfile(target, target + ".bak")
        with open(target, "w", encoding="utf-8") as f:
            f.write(text)
        self.cfg = cfg
        save_json("config.json", cfg)

        setup = await self.setup_status()
        result = {"ok": True, "warnings": warnings, "reloaded": False, "setup": setup}
        if setup["ready"]:
            try:
                try:
                    check = await self.ha.check_config()
                except HAError as err:  # the check itself is optional
                    log.warning("Config check unavailable: %s", err)
                    check = {"result": "valid"}
                if check.get("result") != "valid":
                    result["warnings"] = warnings + [f"Home Assistant reports a configuration problem: {check.get('errors')}"]
                await self.ha.call("irrigation_unlimited", "reload", {})
                result["reloaded"] = True
            except HAError as err:
                result.update(ok=False, errors=[f"Could not reload Irrigation Unlimited: {err}"])
                return result
        # remember what Irrigation Unlimited is now running
        self.applied = {"config": cfg, "mapping": mapping, "at": self.now().isoformat()}
        save_json("applied.json", self.applied)
        # programs removed or no longer looping
        valid = {p["id"] for p in cfg["programs"] if p.get("mode") == "loop"}
        self.rt["looping"] = {k: v for k, v in self.rt["looping"].items() if k in valid}
        self.rt["stop_at"] = {}
        self.pending_cancel = {}
        self.save_rt()
        return result

    async def connect(self) -> dict:
        """Add the include line to configuration.yaml (and write the generated file)."""
        conf_path = os.path.join(CONF_DIR, "configuration.yaml")
        with open(conf_path, encoding="utf-8") as f:
            conf = f.read()
        if INCLUDE_RE.search(conf):
            return {"ok": True}
        if OTHER_IU_RE.search(conf):
            return {"ok": False, "errors": ["configuration.yaml already has an 'irrigation_unlimited:' section. "
                                            "Remove it first (this add-on manages Irrigation Unlimited)."]}
        text, _ = iu_yaml(self.cfg)
        with open(os.path.join(CONF_DIR, IU_FILE), "w", encoding="utf-8") as f:
            f.write(text)
        shutil.copyfile(conf_path, conf_path + ".irrigation_programs.bak")
        with open(conf_path, "a", encoding="utf-8") as f:
            f.write(("" if conf.endswith("\n") else "\n") + "\n" + INCLUDE_LINE + "\n")
        return {"ok": True, "restart_needed": True}

    # ---- actions
    async def iu(self, service: str, data: dict) -> None:
        await self.ha.call("irrigation_unlimited", service, data)

    async def action(self, body: dict) -> dict:
        act = body.get("action")
        cfg = self.applied_cfg()
        progs = model.by_id(cfg["programs"])
        await self.refresh_states(force=True)
        if act in ("start", "skip"):
            p = progs.get(body.get("program", ""))
            if not p or p["id"] not in self.mp()["programs"]:
                return {"ok": False, "errors": ["That program hasn't been applied yet. Save it first."]}
            seq = self.seq_entity(p["id"])
            if act == "skip":
                await self.iu("skip", {"entity_id": seq})
                return {"ok": True}
            others = [q for q in cfg["programs"] if q["mainline"] == p["mainline"] and q["id"] != p["id"]
                      and self.st(self.seq_entity(q["id"])).get("state") == "on"]
            if others and not body.get("force"):
                return {"ok": False, "confirm": True, "busy": [q["name"] for q in others]}
            for q in others:
                self.rt["looping"].pop(q["id"], None)
                await self.iu("cancel", {"entity_id": self.seq_entity(q["id"])})
            if p.get("mode") == "loop":
                self.rt["looping"][p["id"]] = True
            self.rt["stop_at"].pop(p["id"], None)
            self.save_rt()
            self.last_start[p["id"]] = time.time()
            await self.iu("manual_run", {"entity_id": self.ctrl_entity(p["mainline"]), "sequence_id": [p["id"]]})
            return {"ok": True}
        if act == "stop":
            mlid = body.get("mainline")
            for q in cfg["programs"]:
                if q["mainline"] != mlid:
                    continue
                self.rt["looping"].pop(q["id"], None)
                self.rt["stop_at"].pop(q["id"], None)
                self.pending_cancel.pop(q["id"], None)
                if self.st(self.seq_entity(q["id"])).get("state") == "on":
                    await self.iu("cancel", {"entity_id": self.seq_entity(q["id"])})
            self.save_rt()
            return {"ok": True}
        if act in ("pause", "resume"):
            ctrls = [self.ctrl_entity(body["mainline"])] if body.get("mainline") else \
                [model.entity("controller", m) for m in self.mp()["mainlines"].values()]
            for c in ctrls:
                if c:
                    await self.iu(act, {"entity_id": c, "sequence_id": [0]})
            return {"ok": True}
        return {"ok": False, "errors": [f"Unknown action {act}"]}

    # ---- live view
    def live(self) -> dict:
        cfg = self.applied_cfg()
        blocks = model.by_id(cfg["blocks"])
        out = []
        for ml in cfg["mainlines"]:
            if ml["id"] not in self.mp()["mainlines"]:
                continue
            card = {"id": ml["id"], "name": ml["name"], "state": "idle", "program": None, "next": None}
            ml_blocks = [b for b in cfg["blocks"] if b["mainline"] == ml["id"]]
            open_blocks = []
            for b in ml_blocks:
                eid = b.get("entity") or model.entity("zone", self.mp()["blocks"].get(b["id"], {"c": 0, "z": 0}))
                if self.st(eid).get("state") == "on":
                    open_blocks.append(b["number"])
            card["open_blocks"] = sorted(open_blocks)
            pumps = [e for e in ml.get("pump_entities", []) if e]
            card["pump"] = None if not pumps else any(self.st(e).get("state") == "on" for e in pumps)
            nxt = None
            for p in (p for p in cfg["programs"] if p["mainline"] == ml["id"]):
                s = self.st(self.seq_entity(p["id"]))
                a = s.get("attributes", {})
                if a.get("next_start") and p.get("enabled", True):
                    if nxt is None or a["next_start"] < nxt["at"]:
                        nxt = {"program": p["name"], "at": a["next_start"]}
                if s.get("state") != "on":
                    if self.rt["looping"].get(p["id"]):
                        card["state"] = "starting"
                        card["program"] = {"id": p["id"], "name": p["name"], "mode": p["mode"]}
                    continue
                step = a.get("current_zone")
                info = {"id": p["id"], "name": p["name"], "mode": p["mode"], "status": a.get("status"),
                        "step": step, "steps": len(p["steps"]), "repeat": a.get("repeat"),
                        "run_left": a.get("time_remaining"), "started": a.get("current_start")}
                if isinstance(step, int) and 1 <= step <= len(p["steps"]):
                    st = p["steps"][step - 1]
                    info["step_blocks"] = [blocks[x]["number"] for x in st["blocks"] if x in blocks]
                    info["step_duration_s"] = int(float(st["duration_min"]) * 60)
                    zm = self.mp()["blocks"].get(st["blocks"][0]) if st["blocks"] else None
                    za = self.st(model.entity("zone", zm)).get("attributes", {}) if zm else {}
                    info["step_left"] = za.get("time_remaining")
                if p["mode"] == "until":
                    info["stop_at"] = self.rt["stop_at"].get(p["id"])
                if p["mode"] == "loop":
                    info["looping"] = bool(self.rt["looping"].get(p["id"]))
                card["program"] = info
                card["state"] = "paused" if a.get("status") == "paused" else "running"
            card["next"] = nxt
            out.append(card)
        return {"mainlines": out, "updated": self.now().isoformat(), "error": self.last_error,
                "applied_at": (self.applied or {}).get("at")}

    # ---- background runner
    async def tick(self) -> None:
        await self.refresh_states(force=True)
        if self.last_error:
            return
        cfg = self.applied_cfg()
        now = self.now()
        for p in cfg["programs"]:
            seq = self.seq_entity(p["id"])
            if not seq:
                continue
            s = self.st(seq)
            on = s.get("state") == "on"
            a = s.get("attributes", {})
            pid = p["id"]
            if p.get("mode") == "until":
                if not on:
                    if self.rt["stop_at"].pop(pid, None):
                        self.save_rt()
                    self.pending_cancel.pop(pid, None)
                    continue
                if pid not in self.rt["stop_at"]:
                    try:
                        started = datetime.fromisoformat(a["current_start"]).astimezone(self.tz)
                    except (KeyError, TypeError, ValueError):
                        started = now
                    self.rt["stop_at"][pid] = model.next_time(started, p["window"]["end"]).isoformat()
                    self.save_rt()
                stop_at = datetime.fromisoformat(self.rt["stop_at"][pid])
                if now >= stop_at and pid not in self.pending_cancel:
                    delay = 0.0
                    if p.get("end_behavior") == "finish_step":
                        delay = self._step_left(p, a)
                    self.pending_cancel[pid] = time.time() + delay
                    log.info("%s reached its stop time, stopping in %ds", p["name"], delay)
                if pid in self.pending_cancel and time.time() >= self.pending_cancel[pid]:
                    log.info("Stopping %s (end of window)", p["name"])
                    await self._cancel(seq)
                    self.pending_cancel.pop(pid, None)
            elif p.get("mode") == "loop" and self.rt["looping"].get(pid):
                if not on and time.time() - self.last_start.get(pid, 0) > 20:
                    others = [q for q in cfg["programs"] if q["mainline"] == p["mainline"] and q["id"] != pid
                              and self.st(self.seq_entity(q["id"])).get("state") == "on"]
                    if others:
                        continue  # wait until the mainline is free
                    log.info("Restarting loop program %s", p["name"])
                    self.last_start[pid] = time.time()
                    try:
                        await self.iu("manual_run", {"entity_id": self.ctrl_entity(p["mainline"]), "sequence_id": [pid]})
                    except HAError as err:
                        log.warning("Restart failed: %s", err)

    def _step_left(self, p: dict, a: dict) -> float:
        step = a.get("current_zone")
        if not isinstance(step, int) or not 1 <= step <= len(p["steps"]):
            return 0.0
        first = p["steps"][step - 1]["blocks"][0]
        zm = self.mp()["blocks"].get(first)
        left = self.st(model.entity("zone", zm)).get("attributes", {}).get("time_remaining") if zm else None
        return parse_hms(left)

    async def _cancel(self, seq: str) -> None:
        try:
            await self.iu("cancel", {"entity_id": seq})
        except HAError as err:
            log.warning("Cancel failed: %s", err)

    async def runner(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:  # keep the runner alive whatever happens
                log.exception("runner tick failed")
            await asyncio.sleep(2)


def parse_hms(value) -> float:
    if not value:
        return 0.0
    m = re.match(r"^(?:(\d+) days?, )?(\d+):(\d\d):(\d\d)(?:\.\d+)?$", str(value))
    if not m:
        return 0.0
    d, h, mi, s = m.groups()
    return int(d or 0) * 86400 + int(h) * 3600 + int(mi) * 60 + int(s)


# ---------------------------------------------------------------- web

def routes(app_state: App) -> web.RouteTableDef:
    r = web.RouteTableDef()

    @r.get("/")
    async def index(_):
        return web.FileResponse(os.path.join(WWW, "index.html"), headers={"Cache-Control": "no-cache"})

    @r.get("/api/data")
    async def data(_):
        await app_state.refresh_states()
        ents = sorted(
            ({"id": k, "name": v.get("attributes", {}).get("friendly_name", k), "state": v.get("state")}
             for k, v in app_state.states.items()
             if k.startswith(VALVE_DOMAINS)),
            key=lambda e: e["id"])
        return web.json_response({"config": app_state.cfg, "applied_at": (app_state.applied or {}).get("at"),
                                  "entities": ents, "setup": await app_state.setup_status()})

    @r.post("/api/validate")
    async def validate(request):
        cfg = await request.json()
        errors, warnings = model.validate(cfg)
        return web.json_response({"errors": errors, "warnings": warnings})

    @r.put("/api/config")
    async def put_config(request):
        body = await request.json()
        return web.json_response(await app_state.apply(body["config"], bool(body.get("force"))))

    @r.get("/api/health")
    async def health(_):
        return web.json_response({"ok": True})

    @r.get("/api/yaml")
    async def get_yaml(_):
        text, _m = iu_yaml(app_state.cfg)
        return web.Response(text=text, content_type="text/plain")

    @r.get("/api/live")
    async def live(_):
        await app_state.refresh_states()
        return web.json_response(app_state.live())

    @r.post("/api/action")
    async def action(request):
        try:
            return web.json_response(await app_state.action(await request.json()))
        except HAError as err:
            return web.json_response({"ok": False, "errors": [str(err)]})

    @r.post("/api/setup/connect")
    async def connect(_):
        return web.json_response(await app_state.connect())

    @r.post("/api/setup/restart")
    async def restart(_):
        try:
            await app_state.ha.call("homeassistant", "restart", {})
        except HAError as err:
            return web.json_response({"ok": False, "errors": [str(err)]})
        return web.json_response({"ok": True})

    r.static("/static", WWW)
    return r


async def main() -> None:
    state = App()
    await state.ha.start()
    try:
        tzname = (await state.ha.config()).get("time_zone")
        state.tz = ZoneInfo(tzname) if tzname else timezone.utc
    except Exception as err:  # noqa: BLE001
        log.warning("Could not read the time zone (%s), using UTC", err)
    app = web.Application(client_max_size=4 * 1024 * 1024)
    app.add_routes(routes(state))
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("IP_PORT", "8099"))
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("Irrigation Programs listening on %s (config dir %s)", port, CONF_DIR)
    await state.runner()


if __name__ == "__main__":
    asyncio.run(main())
