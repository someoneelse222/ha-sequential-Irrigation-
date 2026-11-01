"""Screenshots of the orange closing / held-closed states using short steps."""
import json, sys, time, urllib.request
from playwright.sync_api import sync_playwright
BASE, OUT = sys.argv[1], sys.argv[2]

def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"}, method=method)
    return json.loads(urllib.request.urlopen(req).read())

cfg = call("GET", "api/data")["config"]
ml = cfg["mainlines"][0]["id"]
bl = sorted(cfg["blocks"], key=lambda b: b["number"])
cfg["programs"].append({"id": "p_quick", "name": "Quick test", "mainline": ml, "enabled": True, "mode": "loop",
                        "season": {"from": "", "until": ""},
                        "steps": [{"blocks": [bl[2]["id"], bl[3]["id"]], "duration_min": 1, "overlap_s": 30},
                                  {"blocks": [bl[4]["id"]], "duration_min": 1, "overlap_s": 30}]})
print(call("PUT", "api/config", {"config": cfg})["ok"])
call("POST", "api/action", {"action": "stop_all"})
time.sleep(8)
call("POST", "api/action", {"action": "start", "program": "p_quick"})
time.sleep(4)
print(call("POST", "api/action", {"action": "block_close", "block": bl[3]["id"]}))
time.sleep(28)   # in the overlap now (30-60 s)
with sync_playwright() as pw:
    b = pw.chromium.launch()
    p = b.new_page(viewport={"width": 1280, "height": 900})
    p.goto(BASE); time.sleep(2.5); p.screenshot(path=f"{OUT}/30-live-closing.png", full_page=True)
    p.click("#tabs button[data-tab=blocks]"); time.sleep(2.5); p.screenshot(path=f"{OUT}/31-blocks-closing.png", full_page=True)
    p.click("#tabs button[data-tab=programs]"); time.sleep(2.5); p.screenshot(path=f"{OUT}/32-programs-closing.png", full_page=True)
    b.close()
