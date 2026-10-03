"""Run: python test_rooms.py   (no dependencies)"""
import json
from rooms import Room

P = "scenarios/ambush_01.json"


def test_roles_and_join():
    r = Room(P)
    r.join("plt_cdr", "Alice")
    for bad in (lambda: r.join("plt_cdr", "Bob"), lambda: r.join("nonsense", "Bob")):
        try:
            bad(); assert False, "should have raised"
        except ValueError:
            pass


def test_no_leak_to_trainee():
    r = Room(P)
    r.join("plt_cdr", "Alice"); r.start()
    for _ in range(45):
        r.tick()
    blob = json.dumps(r.trainee_view("plt_cdr")).lower()          # case-insensitive on purpose
    # Ground truth, rules and degradation flags must never reach a trainee
    for secret in ("truth", "flags", "corrupted", "conflicting", "spoofed", "active_rules", "side", "unknown"):
        assert secret not in blob, f"LEAK: '{secret}' in trainee view"
    assert "red_armor" in blob  # but they do get their delivered picture


def test_trainee_never_sees_degradation_in_source_names():
    import glob
    for path in sorted(glob.glob("scenarios/*.json")):
        r = Room(path); r.join(r.roles[0], "T"); r.start()
        observers = {s["observer"] for s in r.runner.data["sensors"]} | {"alt_source"}
        seen = set()
        while r.state != "finished":
            r.tick()
            seen |= {m["src"] for m in r.trainee_view(r.roles[0])["feed"]}
        assert seen <= observers, f"{path}: suspicious source names {seen - observers}"


def test_instructor_sees_truth():
    r = Room(P); r.start()
    for _ in range(12):
        r.tick()
    v = r.instructor_view()
    assert "red_armor" in v["truth"] and v["active_rules"]


def test_instructor_sees_beliefs_and_decisions():
    r = Room(P); r.join("plt_cdr", "A"); r.start()
    for _ in range(10):
        r.tick()
    r.submit_decision("plt_cdr", "hold", "x", 3)
    v = r.instructor_view()
    assert "red_armor" in v["beliefs"]["plt_cdr"] and len(v["decisions"]) == 1
    assert v["channels"] == ["company_net"]


def test_last_known_persists_after_silence():
    r = Room(P); r.start()
    for _ in range(5):
        r.tick()
    r.live_inject({"kind": "dropout", "channel": "company_net", "severity": 1.0, "duration": 100})
    for _ in range(40):
        r.tick()
    assert any(p["subject"] == "red_armor" for p in r.trainee_view("plt_cdr")["picture"])


def test_all_scenarios_load_and_complete():
    import glob
    paths = sorted(glob.glob("scenarios/*.json"))
    assert len(paths) >= 2
    for path in paths:
        r = Room(path); r.join(r.roles[0], "T"); r.start()
        ticks = 0
        while r.state != "finished":
            r.tick(); ticks += 1
            assert ticks <= r.runner.meta["duration"] + 1
            for u in r.runner.session.world.units.values():      # truth stays on the 1400x700 map
                assert 0 <= u.x <= 1400 and 0 <= u.y <= 700, (path, u.id, u.x, u.y)
        assert ticks == r.runner.meta["duration"], path
        assert not r.runner.pending_injects, f"{path}: injects scheduled after the end never fired"
        dps = [d["at"] for d in r.runner.decision_points]
        assert all(0 < a < r.runner.meta["duration"] for a in dps), path


def test_lobby_does_not_tick():
    r = Room(P)
    r.tick()
    assert r.runner.t == 0


def test_decision_flow():
    r = Room(P); r.join("plt_cdr", "Alice"); r.start()
    for _ in range(10):
        r.tick()
    assert r.trainee_view("plt_cdr")["prompt"] is not None   # prompt opened at t=10
    r.submit_decision("plt_cdr", "hold", "far away", 9)       # confidence clamped to 5
    assert r.trainee_view("plt_cdr")["prompt"] is None
    d = [e for e in r.runner.session.log.events if e["kind"] == "decision"][-1]
    assert d["confidence"] == 5 and "truth" in d and "believed" in d
    try:
        r.submit_decision("plt_cdr", "  "); assert False
    except ValueError:
        pass


def test_live_inject():
    r = Room(P); r.start()
    for _ in range(3):
        r.tick()
    r.live_inject({"kind": "dropout", "channel": "company_net", "severity": 1.0, "duration": 20})
    before = sum(1 for e in r.runner.session.log.events if e["kind"] == "dropped")
    for _ in range(5):
        r.tick()
    after = sum(1 for e in r.runner.session.log.events if e["kind"] == "dropped")
    assert after > before
    try:
        r.live_inject({"kind": "explode", "channel": "x"}); assert False
    except ValueError:
        pass


def test_finishes_and_exports():
    r = Room(P); r.start()
    while r.state != "finished":
        r.tick()
    assert r.export_log()["events"][-1]["kind"] == "scenario_finished"
    json.dumps(r.export_log())  # must be JSON-serializable for the API


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print("PASS", t.__name__)
    print(f"\n{len(tests)}/{len(tests)} passed")
