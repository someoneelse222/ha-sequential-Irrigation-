"""Unit tests for the model and the background runner (no Home Assistant needed)."""
import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "irrigation_programs", "app"), HERE]
os.environ["IP_DATA"] = tempfile.mkdtemp()
os.environ["IP_CONFIG_DIR"] = tempfile.mkdtemp()

import model  # noqa: E402
import main  # noqa: E402
import sample_farm  # noqa: E402

TZ = ZoneInfo("Africa/Johannesburg")


def test_model():
    cfg = sample_farm.farm(True)
    ml = cfg["mainlines"][0]
    once, until, loop = cfg["programs"]
    assert model.run_length(once, ml) == 20 * 60 - 60          # 10+5+5 min minus two 30 s overlaps
    assert model.loop_period(until, ml) == 20 * 60 - 60        # both steps overlap (into next loop too)
    assert model.repeat_count(until, ml) == 4                  # ceil(60 / 19)
    assert model.repeat_count(loop, ml) == model.MAX_REPEAT
    assert model.hms(-30) == "-0:00:30" and model.hms(27 * 3600) == "27:00:00"
    s = model.step_at(until, ml, 10 * 60)                      # 10 min in: step 2 started at 9:30
    assert s["step"] == 2 and s["loop"] == 1 and s["step_left_s"] == 9 * 60 + 30
    s = model.step_at(until, ml, 19 * 60 + 10)                 # second loop, step 1
    assert s["step"] == 1 and s["loop"] == 2
    assert model.season_to_iu("09-01") == "01 Sep"
    errors, warnings = model.validate(cfg)
    assert not errors, errors

    bad = sample_farm.farm(True)
    bad["programs"][0]["steps"][0]["duration_min"] = 400       # longer than 5 h max runtime
    bad["programs"][0]["steps"][1]["blocks"] = []
    bad["programs"][1]["steps"][0]["overlap_s"] = 7            # not a multiple of 5
    errors, _ = model.validate(bad)
    assert any("max runtime" in e for e in errors)
    assert any("has no blocks" in e for e in errors)
    assert any("multiple of 5" in e for e in errors)

    clash = sample_farm.farm(True)
    clash["programs"][1]["window"]["start"] = "06:10"
    clash["programs"][1]["window"]["end"] = "07:00"
    _, warnings = model.validate(clash)
    assert any("overlap on the same mainline" in w for w in warnings), warnings
    print("model ok")


class FakeHA:
    def __init__(self):
        self.calls = []
        self.st = {}

    async def states(self):
        return self.st

    async def call(self, domain, service, data):
        self.calls.append((domain, service, data))


def make_app(now_box, clock_box):
    app = main.App()
    app.ha = FakeHA()
    app.tz = TZ
    app.now = lambda: now_box[0]
    main.time.time = lambda: clock_box[0]
    cfg = sample_farm.farm(True)
    _, mapping = model.generate(cfg)
    app.applied = {"config": cfg, "mapping": mapping, "at": "x"}
    app.rt = {"looping": {}, "stop_at": {}}
    return app


def test_runner():
    now = [datetime(2026, 11, 2, 18, 0, 10, tzinfo=TZ)]
    clock = [1000.0]
    app = make_app(now, clock)
    seq_until = "binary_sensor.irrigation_unlimited_c1_s2"
    start = datetime(2026, 11, 2, 18, 0, 0, tzinfo=TZ)
    app.ha.st = {
        seq_until: {"state": "on", "attributes": {"current_start": start.isoformat(), "current_zone": 1}},
        "binary_sensor.irrigation_unlimited_c1_z1": {"state": "on", "attributes": {"time_remaining": "0:04:00"}},
    }

    async def run():
        await app.tick()
        assert app.rt["stop_at"]["p_until"].startswith("2026-11-02T19:00"), app.rt
        assert not app.ha.calls
        # reach the stop time: finish the running step (4 min left) before cancelling
        now[0] = datetime(2026, 11, 2, 19, 0, 5, tzinfo=TZ)
        await app.tick()
        assert not app.ha.calls and abs(app.pending_cancel["p_until"] - (clock[0] + 240)) < 1
        clock[0] += 241
        await app.tick()
        assert app.ha.calls == [("irrigation_unlimited", "cancel", {"entity_id": seq_until})], app.ha.calls

        # loop program: restarts itself when it's not running
        app.ha.calls.clear()
        app.ha.st = {}
        app.rt["looping"]["p_loop"] = True
        await app.tick()
        assert app.ha.calls == [("irrigation_unlimited", "manual_run",
                                 {"entity_id": "binary_sensor.irrigation_unlimited_c1_m", "sequence_id": ["p_loop"]})]
        await app.tick()                       # not again straight away
        assert len(app.ha.calls) == 1
        clock[0] += 30
        await app.tick()
        assert len(app.ha.calls) == 2

        # stop clears looping and cancels running sequences on the mainline
        app.ha.calls.clear()
        app.ha.st = {"binary_sensor.irrigation_unlimited_c1_s3": {"state": "on", "attributes": {}}}
        r = await app.action({"action": "stop", "mainline": "ml_a"})
        assert r["ok"] and "p_loop" not in app.rt["looping"]
        assert app.ha.calls == [("irrigation_unlimited", "cancel", {"entity_id": "binary_sensor.irrigation_unlimited_c1_s3"})]

        # start while another program runs on the same mainline asks first
        r = await app.action({"action": "start", "program": "p_once"})
        assert r.get("confirm") and r["busy"] == ["Nonstop"]
        app.ha.calls.clear()
        r = await app.action({"action": "start", "program": "p_once", "force": True})
        assert r["ok"] and [c[1] for c in app.ha.calls] == ["cancel", "manual_run"]

    asyncio.run(run())
    print("runner ok")


def test_parse():
    assert main.parse_hms("1 day, 2:00:05") == 93605
    assert main.parse_hms("0:58:40") == 3520
    assert main.parse_hms(None) == 0


if __name__ == "__main__":
    test_model()
    test_runner()
    test_parse()
    print("all ok")
