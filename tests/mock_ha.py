"""A tiny fake Home Assistant REST API for testing the add-on locally. Switch calls change state."""
import sys

from aiohttp import web

ST = {f"switch.valve_{n}": "off" for n in range(1, 9)}
ST["switch.pump"] = "off"
NAMES = {"switch.pump": "Borehole pump"}


async def get_states(_):
    return web.json_response([{"entity_id": k, "state": v, "attributes": {"friendly_name": NAMES.get(k, k.split(".")[1].replace("_", " ").title())}}
                              for k, v in ST.items()])


async def get_services(_):
    return web.json_response([{"domain": d, "services": {}} for d in ("homeassistant", "switch")])


async def get_config(_):
    return web.json_response({"time_zone": "Africa/Johannesburg", "version": "2026.10.4"})


async def call(request):
    body = await request.json() if request.can_read_body else {}
    service = request.match_info["service"]
    ent = body.get("entity_id")
    if ent in ST:
        ST[ent] = "on" if service in ("turn_on", "open_valve") else "off"
    print("CALL", request.match_info["domain"], service, ent, flush=True)
    return web.json_response([])


async def set_state(request):   # test hook: simulate a valve drifting out of sync
    body = await request.json()
    ST[body["entity_id"]] = body["state"]
    return web.json_response({"ok": True})


app = web.Application()
app.add_routes([web.get("/api/states", get_states), web.get("/api/services", get_services), web.get("/api/config", get_config),
                web.post("/api/services/{domain}/{service}", call), web.post("/test/set", set_state)])

if __name__ == "__main__":
    web.run_app(app, port=int(sys.argv[1]) if len(sys.argv) > 1 else 8123, print=None)
