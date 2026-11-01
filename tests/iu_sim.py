"""Run generated configs inside the real Irrigation Unlimited code (its own test harness).

Copied into the Irrigation Unlimited source tree and run with pytest. It prints a timeline of
every valve/controller change and the events, so we can check overlaps, loops, skip, pause,
cancel and reload behave the way the add-on expects.
"""
import os
import sys

import homeassistant.core as ha
import yaml
from homeassistant.const import EVENT_STATE_CHANGED

from tests.iu_test_support import IUExam

HERE = os.path.dirname(__file__)
sys.path[:0] = [os.environ["IP_APP"], os.environ["IP_TESTS"]]
import model  # noqa: E402
import sample_farm  # noqa: E402

IUExam.quiet_mode()

TESTS = [
    {"name": "1-once", "start": "2021-01-04 05:58", "end": "2021-01-04 06:30"},
    {"name": "2-until", "start": "2021-01-04 17:58", "end": "2021-01-04 19:40"},
    {"name": "3-manual loop skip pause cancel", "start": "2021-01-04 10:00", "end": "2021-01-04 10:40"},
    {"name": "4-reload while running", "start": "2021-01-04 05:58", "end": "2021-01-04 06:30"},
]


def write_config(name: str, cfg: dict) -> str:
    iu, _ = model.generate(cfg)
    iu["testing"] = {"enabled": True, "speed": 1.0, "output_events": False, "show_log": False,
                     "autoplay": False, "times": [dict(t, results=[]) for t in TESTS]}
    path = os.path.join(HERE, "configs", name)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump({"irrigation_unlimited": iu}, f, sort_keys=False)
    return name


async def test_ip_timeline(hass: ha.HomeAssistant, skip_dependencies, skip_history):
    cfg = sample_farm.farm(False)
    fname = write_config("ip_farm.yaml", cfg)
    log = []

    async with IUExam(hass, fname) as exam:
        exam.no_check()

        def on_state(event):
            eid = event.data["entity_id"]
            if not eid.startswith("binary_sensor.irrigation_unlimited_c1_"):
                return
            old, new = event.data.get("old_state"), event.data.get("new_state")
            if old is None or new is None or old.state == new.state:
                return
            log.append(f"  {exam.virtual_time:%H:%M:%S}  {eid[34:]:6s} {new.state}")

        def on_event(event):
            d = event.data
            log.append(f"  {exam.virtual_time:%H:%M:%S}  EVENT {event.event_type[21:]} seq={d['sequence']['sequence_id']} "
                       f"ctrl={d['controller']['controller_id']} sched={d['schedule']['name'] if d.get('schedule') else None}")

        hass.bus.async_listen(EVENT_STATE_CHANGED, on_state)
        hass.bus.async_listen("irrigation_unlimited_start", on_event)
        hass.bus.async_listen("irrigation_unlimited_finish", on_event)

        def snap(label, eid="binary_sensor.irrigation_unlimited_c1_s2"):
            st = hass.states.get(eid)
            a = st.attributes
            log.append(f"  SNAP {label} {eid[34:]} state={st.state} status={a.get('status')} current_zone={a.get('current_zone')} "
                       f"repeat={a.get('repeat')} start={a.get('current_start')} dur={a.get('current_duration')} "
                       f"left={a.get('time_remaining')} next={a.get('next_start')}")

        # 1 once
        log.append("TEST 1 once")
        await exam.begin_test(1)
        await exam.finish_test()

        # 2 until (window 18:00-19:00, repeat computed)
        log.append("TEST 2 until")
        await exam.begin_test(2)
        await exam.run_until("2021-01-04 18:25:00")
        snap("18:25")
        await exam.finish_test()

        # 3 manual run of the loop program by sequence_id string, then skip / pause / resume / cancel
        log.append("TEST 3 manual loop")
        await exam.begin_test(3)
        await exam.run_until("2021-01-04 10:01:00")
        await exam.call("manual_run", {"entity_id": "binary_sensor.irrigation_unlimited_c1_m", "sequence_id": ["p_loop"]})
        await exam.run_until("2021-01-04 10:06:00")
        snap("10:06 before skip", "binary_sensor.irrigation_unlimited_c1_s3")
        log.append("  -- skip")
        await exam.call("skip", {"entity_id": "binary_sensor.irrigation_unlimited_c1_s3"})
        await exam.run_until("2021-01-04 10:10:00")
        log.append("  -- pause all (controller, sequence_id 0)")
        await exam.call("pause", {"entity_id": "binary_sensor.irrigation_unlimited_c1_m", "sequence_id": [0]})
        await exam.run_until("2021-01-04 10:15:00")
        snap("10:15 paused", "binary_sensor.irrigation_unlimited_c1_s3")
        log.append("  -- resume all")
        await exam.call("resume", {"entity_id": "binary_sensor.irrigation_unlimited_c1_m", "sequence_id": [0]})
        await exam.run_until("2021-01-04 10:20:00")
        log.append("  -- cancel sequence entity")
        await exam.call("cancel", {"entity_id": "binary_sensor.irrigation_unlimited_c1_s3"})
        await exam.run_until("2021-01-04 10:25:00")
        snap("10:25 after cancel", "binary_sensor.irrigation_unlimited_c1_s3")
        await exam.finish_test()

        # 4 reload in the middle of a run
        log.append("TEST 4 reload while running")
        await exam.begin_test(4)
        await exam.run_until("2021-01-04 06:04:00")
        log.append("  -- reload (same file)")
        await exam.reload(fname)
        snap("after reload", "binary_sensor.irrigation_unlimited_c1_s1")

    with open(os.path.join(HERE, "ip_timeline.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(log) + "\n")
