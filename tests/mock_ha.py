"""A tiny fake Home Assistant REST API for testing the add-on UI locally.

It records service calls and fakes a program running on mainline 1 once the
config has been applied, so the Live view has something to show.
"""
import json
import sys
from datetime import datetime, timedelta, timezone

from aiohttp import web

CALLS = []
STATE = {"running": False}


def states():
    now = datetime.now(timezone.utc)
    s = {}
    for n in range(1, 9):
        s[f"switch.valve_{n}"] = {"entity_id": f"switch.valve_{n}", "state": "off",
                                 "attributes": {"friendly_name": f"Valve {n}"}}
    s["switch.pump"] = {"entity_id": "switch.pump", "state": "off", "attributes": {"friendly_name": "Borehole pump"}}
    s["switch.kitchen_light"] = {"entity_id": "switch.kitchen_light", "state": "on", "attributes": {"friendly_name": "Kitchen"}}
    for c in (1, 2):
        s[f"binary_sensor.irrigation_unlimited_c{c}_m"] = {"state": "off", "attributes": {}}
        for k in range(1, 4):
            s[f"binary_sensor.irrigation_unlimited_c{c}_s{k}"] = {
                "state": "off", "attributes": {"next_start": (now + timedelta(hours=10 + k)).isoformat()}}
    if STATE["running"]:
        s["switch.pump"]["state"] = "on"
        s["switch.valve_3"]["state"] = "on"
        s["switch.valve_4"]["state"] = "on"
        s["binary_sensor.irrigation_unlimited_c1_s1"] = {"state": "on", "attributes": {
            "status": STATE.get("status", "on"), "current_zone": 2, "repeat": "1/1",
            "current_start": (now - timedelta(minutes=95)).isoformat(),
            "time_remaining": "1:47:20", "next_start": (now + timedelta(hours=20)).isoformat()}}
        for z in (3, 4):
            s[f"binary_sensor.irrigation_unlimited_c1_z{z}"] = {"state": "on", "attributes": {
                "time_remaining": "0:58:40", "current_duration": "2:00:00"}}
    return s


async def get_states(_):
    return web.json_response(list(states().values()) if False else [dict(v, entity_id=k) for k, v in states().items()])


async def get_services(_):
    return web.json_response([{"domain": d, "services": {}} for d in ("homeassistant", "switch", "irrigation_unlimited")])


async def get_config(_):
    return web.json_response({"time_zone": "Africa/Johannesburg", "version": "2026.10.4"})


async def check(_):
    return web.json_response({"result": "valid", "errors": None})


async def call(request):
    body = await request.json() if request.can_read_body else {}
    domain, service = request.match_info["domain"], request.match_info["service"]
    CALLS.append((domain, service, body))
    print("CALL", domain, service, json.dumps(body), flush=True)
    if service == "reload":
        STATE["running"] = True
    if service == "pause":
        STATE["status"] = "paused"
    if service == "resume":
        STATE["status"] = "on"
    if service == "cancel":
        STATE["running"] = False
    if service == "manual_run":
        STATE["running"] = True
    return web.json_response([])


app = web.Application()
app.add_routes([
    web.get("/api/states", get_states),
    web.get("/api/services", get_services),
    web.get("/api/config", get_config),
    web.post("/api/config/core/check_config", check),
    web.post("/api/services/{domain}/{service}", call),
])

if __name__ == "__main__":
    web.run_app(app, port=int(sys.argv[1]) if len(sys.argv) > 1 else 8123, print=None)
