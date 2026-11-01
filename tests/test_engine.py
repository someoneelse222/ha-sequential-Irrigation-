"""Engine tests with a fake clock and a fake Home Assistant (run: python3 tests/test_engine.py)."""
import asyncio
import os
import sys
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "irrigation_programs", "app"), HERE]
os.environ["IP_DATA"] = tempfile.mkdtemp()

import engine as E  # noqa: E402
import main  # noqa: E402
from sample_farm import farm  # noqa: E402

TZ = ZoneInfo("Africa/Johannesburg")
T0 = datetime(2026, 11, 2, 5, 59, 50, tzinfo=TZ).timestamp()


class FakeHA:
    def __init__(self, clock):
        self.clock = clock
        self.st = {}
        self.log = []          # (t, entity, on)
        self.down = False

    async def states(self):
        if self.down:
            raise RuntimeError("down")
        return {k: {"state": v} for k, v in self.st.items()}

    async def call(self, domain, service, data):
        if self.down:
            raise RuntimeError("down")
        on = service in ("turn_on", "open_valve")
        self.st[data["entity_id"]] = "on" if on else "off"
        self.log.append((round(self.clock[0] - T0, 1), data["entity_id"], on))


def mk(cfg, clock, ha, data_dir):
    os.environ["IP_DATA"] = data_dir
    main.DATA = data_dir
    eng = E.Engine(main.Store(), ha, TZ)
    eng.wall = lambda: clock[0]
    eng.cfg = cfg
    return eng


async def run_for(eng, cfg, clock, seconds, step=1.0):
    end = clock[0] + seconds
    while clock[0] < end:
        clock[0] += step
        await eng.tick(cfg)


def changes(ha, since=-1e9):
    return [(t, e.split(".")[1], "ON" if on else "off") for t, e, on in ha.log if t >= since]


def test_once_with_overlap_and_pump_order():
    cfg = farm(True)
    clock = [T0]
    ha = FakeHA(clock)
    eng = mk(cfg, clock, ha, tempfile.mkdtemp())

    async def go():
        await run_for(eng, cfg, clock, 20 * 60 + 60)   # schedule fires 06:00, program 19 min
    asyncio.run(go())
    ch = changes(ha)
    # 06:00:00 is t=10. Blocks first, pump 5 s later.
    assert ch[0] == (10, "valve_1", "ON") and ch[1] == (10, "valve_2", "ON"), ch[:3]
    assert (15, "pump", "ON") in ch
    # step 2 opens 30 s before step 1 closes
    assert (10 + 570, "valve_3", "ON") in ch and (10 + 600, "valve_1", "off") in ch
    # end: pump off 5 s before the last block closes
    assert (10 + 1140 - 5, "pump", "off") in ch and (10 + 1140, "valve_4", "off") in ch
    assert not eng.runs
    print("once ok")


def test_pause_resume_restart_and_stop():
    cfg = farm(True)
    clock = [T0]
    ha = FakeHA(clock)
    d = tempfile.mkdtemp()
    eng = mk(cfg, clock, ha, d)

    async def go():
        nonlocal eng
        await run_for(eng, cfg, clock, 130)               # running 2 min
        run = eng.runs[0]
        e_before = run["e"]
        t_pause = clock[0] - T0
        eng.pause_run(run)
        await run_for(eng, cfg, clock, 20)
        ch = changes(ha, t_pause)
        assert ch[0][1] == "pump" and ch[0][2] == "off", ch     # main FIRST
        assert all(c[2] == "off" for c in ch) and ch[1][0] - ch[0][0] >= 5, ch
        assert abs(run["e"] - e_before) < 1.01 and run["state"] == "paused"
        t_res = clock[0] - T0
        eng.resume_run(run)
        await run_for(eng, cfg, clock, 10)
        ch = changes(ha, t_res)
        assert [c[1] for c in ch][:2] == ["valve_1", "valve_2"] and ch[2][1] == "pump" and ch[2][0] - ch[0][0] >= 5, ch

        # crash: add-on gone for 5 minutes, then a new engine loads the saved state
        eng.save(force=True)
        e_at_crash = eng.runs[0]["e"]
        clock[0] += 300
        eng = mk(cfg, clock, ha, d)
        ha.st["switch.valve_1"] = "off"                      # valve dropped while we were away
        await run_for(eng, cfg, clock, 3)
        assert ha.st["switch.pump"] == "off"                 # a block was closed: pump off until blocks are open
        assert abs(eng.runs[0]["e"] - e_at_crash) < 3.5, (eng.runs[0]["e"], e_at_crash)
        await run_for(eng, cfg, clock, 25)                   # resync re-opens valve_1
        assert ha.st["switch.valve_1"] == "on"

        # Home Assistant down for 2 minutes: position held
        e1 = eng.runs[0]["e"]
        ha.down = True
        await run_for(eng, cfg, clock, 120)
        ha.down = False
        await run_for(eng, cfg, clock, 2)
        assert abs(eng.runs[0]["e"] - e1) < 3, (eng.runs[0]["e"], e1)
        assert ha.st["switch.pump"] == "on"  # (still on from before: HA kept it)
        await run_for(eng, cfg, clock, 8)

        # stop: pump first, blocks after
        t_stop = clock[0] - T0
        eng.stop_run(eng.runs[0])
        await run_for(eng, cfg, clock, 10)
        ch = changes(ha, t_stop)
        assert ch[0][1] == "pump" and ch[0][2] == "off" and all(c[2] == "off" for c in ch), ch
        assert not eng.runs
    asyncio.run(go())
    print("pause/resume/restart/stop ok")


def test_two_programs_share_mainline_and_block_manual():
    cfg = farm(True)
    for p in cfg["programs"]:
        p["enabled"] = False
    clock = [T0 + 3600]
    ha = FakeHA(clock)
    eng = mk(cfg, clock, ha, tempfile.mkdtemp())
    progs = {p["id"]: p for p in cfg["programs"]}

    async def go():
        assert eng.start_program(progs["p_loop"])[0]
        await run_for(eng, cfg, clock, 30)
        assert eng.start_program(progs["p_once"])[0]
        await run_for(eng, cfg, clock, 30)
        assert ha.st["switch.pump"] == "on" and ha.st["switch.valve_4"] == "on" and ha.st["switch.valve_1"] == "on"
        # finishing the loop program keeps the pump on for the other one
        eng.stop_run(eng.program_run("p_loop"))
        await run_for(eng, cfg, clock, 10)
        assert ha.st["switch.pump"] == "on" and ha.st["switch.valve_4"] == "off"
        # manual block open uses its default runtime, close goes pump first
        b5 = next(b for b in cfg["blocks"] if b["number"] == 5)
        b5["default_min"] = 3
        ok, msg = await eng.block_command(b5, True)
        await run_for(eng, cfg, clock, 5)
        assert ha.st["switch.valve_5"] == "on" and "3m" in msg
        # close a block that a program has open: held closed, program carries on
        b1 = next(b for b in cfg["blocks"] if b["number"] == 1)
        await eng.block_command(b1, False)
        await run_for(eng, cfg, clock, 5)
        assert ha.st["switch.valve_1"] == "off" and ha.st["switch.valve_2"] == "on"
        # resend open to an open block
        n = len(ha.log)
        await eng.block_command(next(b for b in cfg["blocks"] if b["number"] == 2), True)
        assert len(ha.log) == n + 1 and ha.log[-1][1] == "switch.valve_2"
        view = eng.run_view(eng.program_run("p_once"))
        assert view["steps"][0]["status"] == "running" and view["steps"][1]["status"] == "next"
    asyncio.run(go())
    print("shared mainline / manual ok")


def test_until_finishes_step():
    cfg = farm(True)
    cfg["programs"][0]["enabled"] = False
    clock = [datetime(2026, 11, 2, 17, 59, 55, tzinfo=TZ).timestamp()]
    ha = FakeHA(clock)
    eng = mk(cfg, clock, ha, tempfile.mkdtemp())

    async def go():
        await run_for(eng, cfg, clock, 60 * 60 + 30, step=5)   # to 19:00:25
        run = eng.program_run("p_until")
        assert run and run["last_g"] is not None, run
        v = eng.run_view(run)
        assert v["finishing"]
        await run_for(eng, cfg, clock, 20 * 60, step=5)
        assert not eng.program_run("p_until")
        assert ha.st["switch.pump"] == "off" and ha.st["switch.valve_1"] == "off" and ha.st["switch.valve_2"] == "off"
    asyncio.run(go())
    print("until ok")


def test_held_closed_reopens_next_step():
    cfg = farm(True)
    for p in cfg["programs"]:
        p["enabled"] = False
    clock = [T0 + 3600]
    ha = FakeHA(clock)
    eng = mk(cfg, clock, ha, tempfile.mkdtemp())
    loop = next(p for p in cfg["programs"] if p["id"] == "p_loop")   # b4 2 min, b5 2 min, 30 s overlaps

    async def go():
        eng.start_program(loop)
        await run_for(eng, cfg, clock, 20)
        b4 = next(b for b in cfg["blocks"] if b["number"] == 4)
        await eng.block_command(b4, False)
        await run_for(eng, cfg, clock, 5)
        assert ha.st["switch.valve_4"] == "off" and ha.st["switch.pump"] == "on"
        v = eng.run_view(eng.runs[0])
        assert v["steps"][0]["blocks"][0]["held"]
        await run_for(eng, cfg, clock, 120)          # step 2 (b5) running, b4 still not needed
        assert ha.st["switch.valve_4"] == "off" and ha.st["switch.valve_5"] == "on"
        await run_for(eng, cfg, clock, 90)           # next loop: step 1 needs b4 again
        assert ha.st["switch.valve_4"] == "on", ha.st
        assert not eng.overrides
        # overlap: step 2 of loop 2 opening while step 1 closes -> step 1 shows "closing"
        await run_for(eng, cfg, clock, 45)
        v = eng.run_view(eng.runs[0])
        assert [s["status"] for s in v["steps"]] == ["closing", "running"], [s["status"] for s in v["steps"]]
        assert v["closing"]
    asyncio.run(go())
    print("held closed ok")


if __name__ == "__main__":
    test_held_closed_reopens_next_step()
    test_once_with_overlap_and_pump_order()
    test_pause_resume_restart_and_stop()
    test_two_programs_share_mainline_and_block_manual()
    test_until_finishes_step()
    print("all ok")
