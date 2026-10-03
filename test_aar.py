"""Run: python test_aar.py"""
import csv
import io
from rooms import Room
from aar import build_aar, to_csv, to_pdf

P = "scenarios/ambush_01.json"


def played_room():
    r = Room(P); r.join("plt_cdr", "Alice"); r.start()
    for _ in range(10):
        r.tick()
    r.submit_decision("plt_cdr", "hold position", "enemy far away", 5)     # t=10, clean data
    for _ in range(25):
        r.tick()
    r.submit_decision("plt_cdr", "fire mission", "contact is close", 5)    # t=35, degraded
    while r.state != "finished":
        r.tick()
    return r


def test_analysis():
    a = build_aar(played_room().export_log())
    d1, d2 = a["decisions"]
    assert d1["active_degradation"] == [] or d1["t"] == 10        # injects start at t=10
    assert d2["active_degradation"], "t=35 should be under degradation"
    assert d2["latency"] == 5 and d1["latency"] == 0              # prompts opened at t=10 and t=30
    assert d2["mean_error_m"] is not None and d2["mean_error_m"] >= 0
    assert a["team"]["decisions"] == 2 and a["team"]["completed"] is True
    assert a["per_trainee"]["plt_cdr"]["name"] == "Alice"
    types = {x["type"] for x in a["timeline"]}
    assert {"inject", "decision", "join", "end"} <= types
    assert [x["t"] for x in a["timeline"]] == sorted(x["t"] for x in a["timeline"])


def test_overconfidence_detected():
    r = Room(P); r.join("plt_cdr", "A"); r.start()
    for _ in range(10):
        r.tick()
    r.live_inject({"kind": "corrupt", "channel": "company_net", "severity": 400, "duration": 50})
    for _ in range(20):          # wait out the scripted 8s delay so corrupted reports have arrived
        r.tick()
    r.submit_decision("plt_cdr", "attack", "sure of it", 5)
    d = build_aar(r.export_log())["decisions"][0]
    assert d["mean_error_m"] > 100 and d["calibration"].startswith("Overconfident"), d
    assert "corrupted" in d["bad_data_tags"]


def test_live_injects_not_double_counted():
    r = Room(P); r.join("plt_cdr", "A"); r.start()
    for _ in range(5):
        r.tick()
    r.live_inject({"kind": "delay", "channel": "company_net", "severity": 8, "duration": 20})
    r.live_inject({"kind": "corrupt", "channel": "company_net", "severity": 50, "duration": 20})
    while r.state != "finished":
        r.tick()
    a = build_aar(r.export_log())
    assert a["team"]["injects"] == 4 + 2, a["team"]          # 4 scripted + 2 live, not 4 + 2*2
    live = [x for x in a["timeline"] if "fired live" in x["text"]]
    assert len(live) == 2 and not any(x["text"].startswith("Instructor fired") for x in a["timeline"])


def test_caution_tier_for_midconfidence_bad_picture():
    r = Room(P); r.join("plt_cdr", "A"); r.start()
    for _ in range(10):
        r.tick()
    r.live_inject({"kind": "corrupt", "channel": "company_net", "severity": 400, "duration": 50})
    for _ in range(20):
        r.tick()
    r.submit_decision("plt_cdr", "fire mission", "guess", 3)
    d = build_aar(r.export_log())["decisions"][0]
    assert d["calibration"].startswith("Caution"), d


def test_no_decisions_and_midexercise():
    r = Room(P); r.join("plt_cdr", "A"); r.start()
    for _ in range(5):
        r.tick()
    a = build_aar(r.export_log())
    assert a["team"]["completed"] is False and a["decisions"] == []
    assert a["per_trainee"]["plt_cdr"]["avg_confidence"] is None
    assert to_pdf(a).startswith(b"%PDF")                          # empty report must still render


def test_decision_with_no_information():
    r = Room(P); r.join("plt_cdr", "A"); r.start()
    r.submit_decision("plt_cdr", "wait", "nothing yet", 1)
    d = build_aar(r.export_log())["decisions"][0]
    assert d["had_info"] is False and "No information" in d["calibration"]


def test_csv_injection_and_roundtrip():
    r = Room(P); r.join("plt_cdr", "A"); r.start()
    for _ in range(3):
        r.tick()
    r.submit_decision("plt_cdr", '=HYPERLINK("http://evil")', '+cmd|calc, "quoted", comma', 3)
    rows = list(csv.reader(io.StringIO(to_csv(build_aar(r.export_log())))))
    assert rows[1][3].startswith("'=") and rows[1][4].startswith("'+")     # neutralised
    assert "quoted" in rows[1][4]                                          # commas/quotes survive


def test_pdf_with_hostile_text():
    r = Room(P); r.join("plt_cdr", "<b>Al</b> & <i>ice"); r.start()
    for _ in range(12):
        r.tick()
    r.submit_decision("plt_cdr", "<para>unclosed <b>tag", "a < b & c </font>", 3)
    pdf = to_pdf(build_aar(r.export_log()))
    assert pdf.startswith(b"%PDF") and len(pdf) > 2000


def test_full_pdf_written():
    pdf = to_pdf(build_aar(played_room().export_log()))
    open("sample_AAR.pdf", "wb").write(pdf)
    assert pdf.startswith(b"%PDF")


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print("PASS", t.__name__)
    print(f"\n{len(tests)}/{len(tests)} passed  (wrote sample_AAR.pdf)")
