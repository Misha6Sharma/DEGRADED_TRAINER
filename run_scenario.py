"""Run: python run_scenario.py [scenarios/ambush_01.json]"""
import sys
from scenario import ScenarioRunner

path = sys.argv[1] if len(sys.argv) > 1 else "scenarios/ambush_01.json"
run = ScenarioRunner(path)
print(f"== {run.meta['title']} ({run.meta['duration']}s) ==")

# Stand-in for a real trainee: auto-answers each decision point.
canned = {10: ("hold position", 4), 30: ("request fire support", 2), 50: ("withdraw to bridge", 3)}

while not run.finished():
    fired, due = run.tick()
    for inj in fired:
        print(f"[t={run.t:>2}] INJECT {inj['kind']:<8} {inj.get('note', '')}")
    for dp in due:
        order, conf = canned.get(dp["at"], ("hold position", 3))   # default answer for any scenario
        run.session.decide("plt_cdr", order, rationale=dp["prompt"], confidence=conf)
        print(f"[t={run.t:>2}] DECISION plt_cdr -> {order} (confidence {conf})")

log = run.session.log.events
count = lambda k: sum(1 for e in log if e["kind"] == k)
print(f"\nDelivered: {count('delivered')}  Dropped: {count('dropped')}  "
      f"Decisions: {count('decision')}  Total events: {len(log)}")
