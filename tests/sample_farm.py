"""A small sample farm used by the tests."""
ALL = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

def farm(entities=False):
    blocks = []
    for n in range(1, 6):
        b = {"id": f"b_{n}", "number": n, "name": f"Field {n}", "mainline": "ml_a",
             "max_min": 300, "enabled": True, "entity": f"switch.valve_{n}" if entities else ""}
        blocks.append(b)
    return {
        "version": 1,
        "mainlines": [{"id": "ml_a", "name": "Mainline A", "pump_entities": ["switch.pump"] if entities else [],
                        "pump_start_s": 5, "pump_stop_s": 5, "overlap_s": 30}],
        "blocks": blocks,
        "programs": [
            {"id": "p_once", "name": "Morning", "mainline": "ml_a", "enabled": True, "mode": "once",
             "starts": [{"time": "06:00", "days": ALL}], "season": {"from": "", "until": ""},
             "steps": [{"blocks": ["b_1", "b_2"], "duration_min": 10, "overlap_s": None},
                       {"blocks": ["b_3"], "duration_min": 5, "overlap_s": None},
                       {"blocks": ["b_4"], "duration_min": 5, "overlap_s": None}]},
            {"id": "p_until", "name": "Evening loop", "mainline": "ml_a", "enabled": True, "mode": "until",
             "window": {"start": "18:00", "end": "19:00", "days": ALL}, "end_behavior": "finish_step",
             "season": {"from": "", "until": ""},
             "steps": [{"blocks": ["b_1"], "duration_min": 10, "overlap_s": None},
                       {"blocks": ["b_2"], "duration_min": 10, "overlap_s": None}]},
            {"id": "p_loop", "name": "Nonstop", "mainline": "ml_a", "enabled": True, "mode": "loop",
             "season": {"from": "", "until": ""},
             "steps": [{"blocks": ["b_4"], "duration_min": 2, "overlap_s": None},
                       {"blocks": ["b_5"], "duration_min": 2, "overlap_s": None}]},
        ],
    }
