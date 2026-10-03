"""Run: python demo.py   -- a tiny scenario showing degradation mid-exercise."""
from engine import Session, Unit, Rule

s = Session(seed=42)
s.world.add(Unit("red_armor", "red", x=1000, y=500, vx=-5, vy=0, strength=8))
s.world.add(Unit("blue_recon", "blue", x=0, y=0))
s.add_sensor("blue_recon", "red_armor", trainee="plt_cdr", channel="company_net")

for _ in range(10):
    s.tick()
s.decide("plt_cdr", "hold position", "enemy still far, clean reports", 4)

# Instructor injects degradation mid-exercise
s.layer.add_rule(Rule("delay", "company_net", start=10, end=40, severity=8), t=10)
s.layer.add_rule(Rule("dropout", "company_net", start=10, end=40, severity=0.4), t=10)
s.layer.add_rule(Rule("conflict", "company_net", start=20, end=40, severity=150), t=20)

for _ in range(20):
    s.tick()
s.decide("plt_cdr", "engage with fire support", "contact seems close", 2)

print(f"{'t':>3}  {'kind':<10} detail")
for e in s.log.events:
    if e["kind"] in ("rule_added", "dropped", "decision") or e.get("flags"):
        extra = {k: v for k, v in e.items() if k not in ("t", "kind", "truth")}
        print(f"{e['t']:>3}  {e['kind']:<10} {extra}")

d = [e for e in s.log.events if e["kind"] == "decision"][-1]
print("\nLast decision: believed vs truth for red_armor")
print("  believed:", d["believed"]["red_armor"])
print("  truth:   ", d["truth"]["red_armor"])
