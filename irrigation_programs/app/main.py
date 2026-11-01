"""Irrigation Programs add-on: web server around the watering engine."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
from datetime import timezone
from zoneinfo import ZoneInfo

from aiohttp import web

import model
from engine import Engine, is_on
from ha import HA, HAError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("irrigation_programs")

HERE = os.path.dirname(os.path.abspath(__file__))
WWW = os.path.join(HERE, "www")
DATA = os.environ.get("IP_DATA", "/data")
CONF_DIR = os.environ.get("IP_CONFIG_DIR") or ("/homeassistant" if os.path.isdir("/homeassistant") else "/config")
OLD_IU_FILE = "irrigation_programs.yaml"
OLD_INCLUDE_RE = re.compile(r"\n*^irrigation_unlimited:\s*!include\s+irrigation_programs\.yaml\s*$\n?", re.M)
VALVE_DOMAINS = ("switch.", "valve.", "input_boolean.", "light.")
TICK_S = 1.0


class Store:
    def load(self, name, default):
        try:
            with open(os.path.join(DATA, name), encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return default

    def save(self, name, data) -> None:
        os.makedirs(DATA, exist_ok=True)
        path = os.path.join(DATA, name)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(path + ".tmp", path)


PLACEHOLDER = ("# No longer used by the Irrigation Programs add-on (it now switches the valves itself).\n"
               "# Safe to delete together with its line in configuration.yaml.\n"
               "controllers:\n  - name: Not used\n    enabled: false\n    zones:\n      - name: Placeholder\n")


class App:
    def __init__(self) -> None:
        self.store = Store()
        self.ha = HA()
        self.cfg = model.migrate(self.store.load("config.json", model.empty_config()))
        self.engine: Engine | None = None
        self.tz = timezone.utc
        self.last_tick_error = ""

    # ---------------------------------------------------------------- old Irrigation Unlimited link
    def _conf(self) -> str:
        try:
            with open(os.path.join(CONF_DIR, "configuration.yaml"), encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""

    async def neutralise_iu(self) -> None:
        """Earlier versions handed the programs to Irrigation Unlimited. Make sure it no longer runs them."""
        path = os.path.join(CONF_DIR, OLD_IU_FILE)
        if not os.path.exists(path):
            return
        with open(path, encoding="utf-8") as f:
            current = f.read()
        if current == PLACEHOLDER:
            return
        shutil.copyfile(path, path + ".bak")
        with open(path, "w", encoding="utf-8") as f:
            f.write(PLACEHOLDER)
        try:
            if "irrigation_unlimited" in await self.ha.services():
                await self.ha.call("irrigation_unlimited", "reload", {})
        except HAError as err:
            log.warning("Could not reload Irrigation Unlimited: %s", err)
        log.info("Irrigation Unlimited no longer runs any programs from this add-on.")

    async def remove_iu_link(self) -> dict:
        conf_path = os.path.join(CONF_DIR, "configuration.yaml")
        conf = self._conf()
        if OLD_INCLUDE_RE.search(conf):
            shutil.copyfile(conf_path, conf_path + ".irrigation_programs.bak")
            with open(conf_path, "w", encoding="utf-8") as f:
                f.write(OLD_INCLUDE_RE.sub("\n", conf).rstrip("\n") + "\n")
        path = os.path.join(CONF_DIR, OLD_IU_FILE)
        for p in (path, path + ".bak"):
            if os.path.exists(p):
                os.remove(p)
        return {"ok": True}

    async def setup_status(self) -> dict:
        ha_ok = True
        try:
            await self.ha.config()
        except HAError as err:
            ha_ok = False
            self.last_tick_error = str(err)
        return {"ha_ok": ha_ok, "error": self.last_tick_error,
                "iu_link": bool(OLD_INCLUDE_RE.search(self._conf())),
                "iu_installed": os.path.isdir(os.path.join(CONF_DIR, "custom_components", "irrigation_unlimited")),
                "engine_ok": bool(self.engine and self.engine.ha_ok)}

    # ---------------------------------------------------------------- live data
    def live(self) -> dict:
        eng = self.engine
        cfg = self.cfg
        blocks = model.by_id(cfg["blocks"])
        st = eng.states
        want = eng.desired()
        now_dt = eng.now_dt()
        runs_by_ml: dict[str, list] = {}
        open_by: dict[str, list] = {}
        for r in eng.runs:
            v = eng.run_view(r)
            runs_by_ml.setdefault(r["mainline"], []).append(v)
            for s in v["steps"]:
                if s["status"] == "running":
                    for b in s["blocks"]:
                        open_by.setdefault(b["id"], []).append({"run": v["name"], "step": s["n"], "left_s": s["left_s"],
                                                                "manual": r["source"] == "block", "run_id": r["id"]})

        def ent_view(e):
            actual = is_on(st.get(e, {}).get("state"))
            c = eng.commanded.get(e)
            return {"id": e, "actual": actual, "raw": st.get(e, {}).get("state"), "wanted": bool(want.get(e)),
                    "commanded": None if c is None else c["on"],
                    "out_of_sync": c is not None and actual is not None and actual != c["on"],
                    "held_closed": e in eng.overrides}

        mls = []
        for ml in cfg["mainlines"]:
            up = []
            for p in cfg["programs"]:
                if p.get("mainline") == ml["id"]:
                    for at in model.upcoming(p, now_dt, limit=2):
                        up.append({"program": p["name"], "program_id": p["id"], "at": at.timestamp()})
            up.sort(key=lambda x: x["at"])
            mls.append({"id": ml["id"], "name": ml["name"],
                        "pumps": [ent_view(e) for e in ml.get("pump_entities", []) if e],
                        "runs": runs_by_ml.get(ml["id"], []), "upcoming": up[:4]})
        bl = []
        for b in sorted(cfg["blocks"], key=lambda x: int(x.get("number") or 0)):
            ev = ent_view(b["entity"]) if b.get("entity") else None
            bl.append({"id": b["id"], "number": b["number"], "name": b.get("name", ""), "mainline": b["mainline"],
                       "entity": b.get("entity"), "default_min": b.get("default_min"), "enabled": b.get("enabled", True),
                       "valve": ev, "open_by": open_by.get(b["id"], [])})
        return {"mainlines": mls, "blocks": bl, "events": list(reversed(eng.events[-40:])),
                "ha_ok": eng.ha_ok, "now": eng.wall(), "error": self.last_tick_error}

    # ---------------------------------------------------------------- actions
    async def action(self, body: dict) -> dict:
        eng = self.engine
        act = body.get("action")
        if act == "start":
            p = model.by_id(self.cfg["programs"]).get(body.get("program", ""))
            if not p:
                return {"ok": False, "errors": ["That program isn't saved yet."]}
            errors, _ = model.validate({**self.cfg, "programs": [p]})
            errors = [e for e in errors if p["name"] in e]
            if errors:
                return {"ok": False, "errors": errors}
            ok, msg = eng.start_program(p)
            return {"ok": ok, "errors": [msg] if msg else []}
        run = next((r for r in eng.runs if r["id"] == body.get("run")), None)
        if act in ("pause", "resume", "stop", "skip") and body.get("run"):
            if not run:
                return {"ok": False, "errors": ["That program has already finished."]}
            {"pause": eng.pause_run, "resume": eng.resume_run, "stop": eng.stop_run, "skip": eng.skip_step}[act](run)
            return {"ok": True}
        if act in ("pause_all", "resume_all", "stop_all"):
            for r in list(eng.runs):
                if body.get("mainline") and r["mainline"] != body["mainline"]:
                    continue
                {"pause_all": eng.pause_run, "resume_all": eng.resume_run, "stop_all": eng.stop_run}[act](r)
            return {"ok": True}
        if act in ("block_open", "block_close"):
            b = model.by_id(self.cfg["blocks"]).get(body.get("block", ""))
            if not b:
                return {"ok": False, "errors": ["That block isn't saved yet."]}
            ok, msg = await eng.block_command(b, act == "block_open")
            return {"ok": ok, "message": msg, "errors": [] if ok else [msg]}
        if act in ("pump_open", "pump_close"):
            ml = model.by_id(self.cfg["mainlines"]).get(body.get("mainline", ""))
            if not ml:
                return {"ok": False, "errors": ["Unknown mainline."]}
            ok, msg = await eng.pump_command(ml, act == "pump_open")
            return {"ok": ok, "message": msg, "errors": [] if ok else [msg]}
        return {"ok": False, "errors": [f"Unknown action {act}"]}

    async def loop(self) -> None:
        while True:
            try:
                await self.engine.tick(self.cfg)
                self.last_tick_error = "" if self.engine.ha_ok else self.last_tick_error
            except Exception as err:  # keep the engine alive whatever happens
                log.exception("engine tick failed")
                self.last_tick_error = str(err)
            await asyncio.sleep(TICK_S)


def routes(app: App) -> web.RouteTableDef:
    r = web.RouteTableDef()

    @r.get("/")
    async def index(_):
        return web.FileResponse(os.path.join(WWW, "index.html"), headers={"Cache-Control": "no-cache"})

    @r.get("/api/health")
    async def health(_):
        return web.json_response({"ok": True})

    @r.get("/api/data")
    async def data(_):
        st = app.engine.states
        ents = sorted(({"id": k, "name": v.get("attributes", {}).get("friendly_name", k), "state": v.get("state")}
                       for k, v in st.items() if k.startswith(VALVE_DOMAINS)), key=lambda e: e["id"])
        return web.json_response({"config": app.cfg, "entities": ents, "setup": await app.setup_status()})

    @r.post("/api/validate")
    async def validate(request):
        errors, warnings = model.validate(await request.json())
        return web.json_response({"errors": errors, "warnings": warnings})

    @r.put("/api/config")
    async def put_config(request):
        cfg = model.migrate((await request.json())["config"])
        errors, warnings = model.validate(cfg)
        if errors:
            return web.json_response({"ok": False, "errors": errors, "warnings": warnings})
        app.cfg = cfg
        app.store.save("config.json", cfg)
        running = [r["name"] for r in app.engine.runs if r["source"] != "block"]
        return web.json_response({"ok": True, "warnings": warnings, "running": running})

    @r.get("/api/live")
    async def live(_):
        return web.json_response(app.live())

    @r.post("/api/action")
    async def action(request):
        try:
            res = await app.action(await request.json())
            app.engine.save(force=True)
            return web.json_response(res)
        except HAError as err:
            return web.json_response({"ok": False, "errors": [str(err)]})

    @r.post("/api/setup/remove_iu")
    async def remove_iu(_):
        return web.json_response(await app.remove_iu_link())

    r.static("/static", WWW)
    return r


async def main() -> None:
    app = App()
    await app.ha.start()
    try:
        tzname = (await app.ha.config()).get("time_zone")
        app.tz = ZoneInfo(tzname) if tzname else timezone.utc
    except Exception as err:  # noqa: BLE001
        log.warning("Could not read the time zone (%s), using UTC", err)
    app.engine = Engine(app.store, app.ha, app.tz)
    if app.engine.runs:
        app.engine.event(f"Add-on started: {len(app.engine.runs)} program(s) were running, carrying on where they were.")
    try:
        await app.neutralise_iu()
    except Exception as err:  # noqa: BLE001
        log.warning("Could not unhook Irrigation Unlimited: %s", err)
    web_app = web.Application(client_max_size=4 * 1024 * 1024)
    web_app.add_routes(routes(app))
    runner = web.AppRunner(web_app)
    await runner.setup()
    port = int(os.environ.get("IP_PORT", "8099"))
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("Irrigation Programs listening on %s", port)
    await app.loop()


if __name__ == "__main__":
    asyncio.run(main())
