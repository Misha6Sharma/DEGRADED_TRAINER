"""Step 6: After Action Review. Turns the event log into analysis + CSV + PDF.

build_aar() needs only Room.export_log() output, so it also works on saved logs.
"""
import csv
import io
import math
from statistics import mean
from xml.sax.saxutils import escape

OVERCONFIDENT_CONF, OVERCONFIDENT_ERR = 4, 100     # rated >=4/5 yet picture >100 m off
UNDERCONFIDENT_CONF, UNDERCONFIDENT_ERR, UNDERCONFIDENT_AGE = 2, 50, 5
CAUTION_CONF, CAUTION_ERR = 3, 150                 # middling confidence with a badly wrong picture


def build_aar(log):
    events, meta = log["events"], log["scenario"]
    rules = [e for e in events if e["kind"] == "rule_added"]
    players = {e["trainee"]: e["name"] for e in events if e["kind"] == "player_joined"}

    decisions, timeline = [], []
    for e in events:
        k, t = e["kind"], e["t"]
        if k == "rule_added":
            timeline.append({"t": t, "type": "inject", "rule": e["rule"], "channel": e["channel"],
                             "text": f"{e['rule']} on {e['channel']} (severity {e['severity']}, "
                                     f"{e['end'] - t if e['end'] < 10**8 else 'open-ended'}s)"})
        elif k == "inject_note":
            timeline.append({"t": t, "type": "note", "text": e["note"]})
        elif k == "live_inject":
            # the rule itself was already logged as an inject; mark it as instructor-fired, don't count twice
            for x in reversed(timeline):
                if (x["type"] == "inject" and x["t"] == t and x.get("rule") == e["inject"]
                        and x.get("channel") == e["channel"] and "[fired live" not in x["text"]):
                    x["text"] += " [fired live by instructor]"
                    break
            else:
                timeline.append({"t": t, "type": "inject", "text": f"Instructor fired {e['inject']} on {e['channel']}"})
        elif k == "player_joined":
            timeline.append({"t": t, "type": "join", "text": f"{e['name']} joined as {e['trainee']}"})
        elif k == "scenario_finished":
            timeline.append({"t": t, "type": "end", "text": "Scenario finished"})
        elif k == "decision":
            errs, ages = [], []
            for sid, b in e["believed"].items():
                if sid in e["truth"]:
                    errs.append(math.hypot(b["x"] - e["truth"][sid]["x"], b["y"] - e["truth"][sid]["y"]))
                    ages.append(b["age"])
            flags = sorted({f for b in e["believed"].values() for f in b["flags"]})
            active = sorted({f"{r['rule']}" for r in rules if r["start"] <= t < r["end"]})
            d = {"t": t, "trainee": e["trainee"], "name": players.get(e["trainee"], ""),
                 "order": e["order"], "rationale": e["rationale"], "confidence": e["confidence"],
                 "latency": None, "prompt_id": None,
                 "mean_error_m": round(mean(errs)) if errs else None,
                 "max_info_age_s": max(ages) if ages else None,
                 "had_info": bool(errs), "bad_data_tags": flags, "active_degradation": active,
                 "calibration": ""}
            d["calibration"] = _calibration(d)
            decisions.append(d)
            timeline.append({"t": t, "type": "decision", "text": f"{e['trainee']}: \"{e['order']}\" (confidence {e['confidence']}/5)"})
        elif k == "decision_meta":      # logged right after its decision
            for d in reversed(decisions):
                if d["trainee"] == e["trainee"] and d["t"] == t and d["prompt_id"] is None:
                    d["prompt_id"] = e["prompt_id"]
                    d["latency"] = t - e["prompt_id"] if e["prompt_id"] is not None else None
                    break
    timeline.sort(key=lambda x: x["t"])      # stable: keeps log order within the same second

    per_trainee = {}
    for role in sorted({d["trainee"] for d in decisions} | set(players)):
        ds = [d for d in decisions if d["trainee"] == role]
        errs = [d["mean_error_m"] for d in ds if d["mean_error_m"] is not None]
        lat = [d["latency"] for d in ds if d["latency"] is not None]
        per_trainee[role] = {
            "name": players.get(role, ""), "decisions": len(ds),
            "avg_confidence": round(mean(d["confidence"] for d in ds), 1) if ds else None,
            "avg_error_m": round(mean(errs)) if errs else None,
            "avg_latency_s": round(mean(lat), 1) if lat else None,
            "under_degradation": sum(1 for d in ds if d["active_degradation"]),
            "overconfident": sum(1 for d in ds if d["calibration"].startswith("Overconfident")),
            "underconfident": sum(1 for d in ds if d["calibration"].startswith("Underconfident"))}
    all_errs = [d["mean_error_m"] for d in decisions if d["mean_error_m"] is not None]
    team = {"decisions": len(decisions),
            "under_degradation": sum(1 for d in decisions if d["active_degradation"]),
            "avg_error_m": round(mean(all_errs)) if all_errs else None,
            "injects": sum(1 for x in timeline if x["type"] == "inject"),
            "duration": meta["duration"],
            "completed": any(x["type"] == "end" for x in timeline)}
    return {"room": log["room"], "title": meta["title"], "description": meta.get("description", ""),
            "team": team, "per_trainee": per_trainee, "decisions": decisions, "timeline": timeline}


def _calibration(d):
    if not d["had_info"]:
        return "No information was available when this decision was made"
    if d["confidence"] >= OVERCONFIDENT_CONF and d["mean_error_m"] > OVERCONFIDENT_ERR:
        return f"Overconfident: rated {d['confidence']}/5 while the picture was ~{d['mean_error_m']} m off"
    if d["confidence"] >= CAUTION_CONF and d["mean_error_m"] > CAUTION_ERR:
        return f"Caution: confidence {d['confidence']}/5 while the picture was ~{d['mean_error_m']} m off"
    if (d["confidence"] <= UNDERCONFIDENT_CONF and d["mean_error_m"] <= UNDERCONFIDENT_ERR
            and d["max_info_age_s"] <= UNDERCONFIDENT_AGE):
        return "Underconfident: information was fresh and accurate"
    return ""


# ---------- CSV (decisions) ----------
def _safe(v):
    """Stop spreadsheet formula injection from free-text rationale."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@") else s


def to_csv(aar):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["time_s", "trainee", "name", "order", "rationale", "confidence", "latency_s",
                "mean_error_m", "max_info_age_s", "bad_data_tags", "active_degradation", "calibration"])
    for d in aar["decisions"]:
        w.writerow([_safe(x) for x in (d["t"], d["trainee"], d["name"], d["order"], d["rationale"],
                    d["confidence"], d["latency"], d["mean_error_m"], d["max_info_age_s"],
                    ";".join(d["bad_data_tags"]), ";".join(d["active_degradation"]), d["calibration"])])
    return out.getvalue()


# ---------- PDF ----------
def to_pdf(aar):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

    ss = getSampleStyleSheet()
    cell = ParagraphStyle("cell", parent=ss["BodyText"], fontSize=8, leading=10)
    P = lambda v: Paragraph(escape("" if v is None else str(v)), cell)   # escape: text is user-supplied
    dash = lambda v, suf="": "n/a" if v is None else f"{v}{suf}"

    def table(rows, widths):
        t = Table(rows, colWidths=[w * mm for w in widths], repeatRows=1)
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2d3a2a")),
                               ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("FONTSIZE", (0, 0), (-1, -1), 8),
                               ("GRID", (0, 0), (-1, -1), 0.3, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        return t

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=15 * mm, bottomMargin=15 * mm, title=f"AAR {aar['title']}")
    tm, S = aar["team"], []
    S += [Paragraph(escape(f"After Action Review: {aar['title']}"), ss["Title"]),
          Paragraph(escape(f"Room {aar['room']} | {tm['duration']}s scenario | "
                          f"{'completed' if tm['completed'] else 'INCOMPLETE (exported mid-exercise)'}"), ss["Normal"]),
          Paragraph(escape(aar["description"]), ss["Italic"]), Spacer(1, 6 * mm)]

    S.append(Paragraph("Team summary", ss["Heading2"]))
    S.append(table([["Decisions", "Made under degradation", "Injects fired", "Avg picture error"],
                    [tm["decisions"], tm["under_degradation"], tm["injects"], dash(tm["avg_error_m"], " m")]], [40, 50, 40, 50]))
    S.append(Paragraph("Individual summary", ss["Heading2"]))
    rows = [["Role (name)", "Decisions", "Avg confidence", "Avg error", "Avg latency", "Overconf.", "Underconf."]]
    for role, p in aar["per_trainee"].items():
        rows.append([P(f"{role} ({p['name']})"), p["decisions"], dash(p["avg_confidence"], "/5"),
                     dash(p["avg_error_m"], " m"), dash(p["avg_latency_s"], " s"), p["overconfident"], p["underconfident"]])
    S.append(table(rows, [44, 20, 28, 22, 24, 20, 22]))

    S.append(Paragraph("Event timeline", ss["Heading2"]))
    S.append(table([["T+ (s)", "Type", "Event"]] + [[x["t"], x["type"], P(x["text"])] for x in aar["timeline"]], [18, 22, 140]))

    S.append(Paragraph("Decision log: what they knew vs. what was true", ss["Heading2"]))
    rows = [["T+", "Who", "Order and rationale", "Conf.", "Information quality", "Assessment"]]
    for d in aar["decisions"]:
        q = ("No information available" if not d["had_info"] else
             f"Picture ~{d['mean_error_m']} m off; oldest info {d['max_info_age_s']}s; "
             f"degradation: {', '.join(d['active_degradation']) or 'none'}; "
             f"data issues: {', '.join(d['bad_data_tags']) or 'none'}")
        order_why = Paragraph(escape(d["order"]) + "<br/>Why: " + escape(d["rationale"] or "(none given)"), cell)
        rows.append([d["t"], P(d["trainee"]), order_why,
                     f"{d['confidence']}/5", P(q), P(d["calibration"] or "No issue flagged")])
    S.append(table(rows, [10, 20, 52, 12, 50, 36]))
    doc.build(S)
    return buf.getvalue()
