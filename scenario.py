"""Step 2: load scenarios from JSON and run their scripted inject timeline."""
import json
from engine import Session, Unit, Rule

RULE_KINDS = {"delay", "dropout", "corrupt", "conflict", "spoof"}


def validate(data):
    """Fail early with a clear message instead of a mid-exercise crash."""
    for key in ("meta", "units", "roles", "sensors"):
        if key not in data:
            raise ValueError(f"scenario missing '{key}'")
    ids = {u["id"] for u in data["units"]}
    for s in data["sensors"]:
        for f in ("observer", "subject"):
            if s[f] not in ids:
                raise ValueError(f"sensor references unknown unit '{s[f]}'")
        if s["trainee"] not in data["roles"]:
            raise ValueError(f"sensor references unknown role '{s['trainee']}'")
    for i in data.get("injects", []):
        if i["kind"] not in RULE_KINDS:
            raise ValueError(f"unknown inject kind '{i['kind']}'")
        if i.get("target") and i["target"] not in ids:
            raise ValueError(f"inject targets unknown unit '{i['target']}'")


class ScenarioRunner:
    """Wraps a Session and fires injects/decision points at their scheduled time."""

    def __init__(self, path):
        with open(path) as f:
            self.data = json.load(f)
        validate(self.data)
        self.meta = self.data["meta"]
        self.session = Session(seed=self.meta.get("seed", 1))
        for u in self.data["units"]:
            self.session.world.add(Unit(**u))
        for s in self.data["sensors"]:
            self.session.add_sensor(s["observer"], s["subject"], s["trainee"], s["channel"])
        self.pending_injects = sorted(self.data.get("injects", []), key=lambda i: i["at"])
        self.decision_points = sorted(self.data.get("decision_points", []), key=lambda d: d["at"])
        self.last_delivered = {}

    @property
    def t(self):
        return self.session.world.t

    def tick(self):
        """Advance one second. Returns (injects_fired, decision_points_due)."""
        self.last_delivered = self.session.tick()
        fired = []
        while self.pending_injects and self.pending_injects[0]["at"] <= self.t:
            inj = self.pending_injects.pop(0)
            self.apply_inject(inj)
            fired.append(inj)
        due = [d for d in self.decision_points if d["at"] == self.t]
        return fired, due

    def apply_inject(self, inj):
        """Used for scripted injects AND live instructor injects (same code path)."""
        rule = Rule(inj["kind"], inj["channel"], start=self.t,
                    end=self.t + inj.get("duration", 10**9),
                    severity=inj.get("severity", 1.0), target=inj.get("target"))
        self.session.layer.add_rule(rule, t=self.t)
        if inj.get("note"):
            self.session.log.add(self.t, "inject_note", note=inj["note"])

    def finished(self):
        return self.t >= self.meta["duration"]
