"""Step 1: core engine for the degraded-communications decision trainer.

Flow:  ground truth -> sensors make Reports -> DegradationLayer (rules) -> trainee inbox
Every delivered/dropped/decision event goes to an append-only log (the future AAR).
"""
import heapq
import random
from dataclasses import dataclass, field, asdict


# ---------- Ground truth ----------
@dataclass
class Unit:
    id: str
    side: str            # "blue" or "red"
    x: float
    y: float
    vx: float = 0.0
    vy: float = 0.0
    strength: int = 10


class World:
    """The one true state. Never sent to trainees directly."""
    def __init__(self, seed=1):
        self.rng = random.Random(seed)
        self.units = {}
        self.t = 0

    def add(self, u):
        self.units[u.id] = u

    def step(self):
        self.t += 1
        for u in self.units.values():
            u.x += u.vx
            u.y += u.vy


# ---------- Reports and degradation ----------
@dataclass
class Report:
    src: str             # reporting unit
    subject: str         # unit being reported
    x: float
    y: float
    strength: int
    created: int         # sim time of observation
    channel: str         # e.g. "company_net"
    flags: list = field(default_factory=list)  # e.g. ["corrupted"] (instructor/AAR only)


@dataclass
class Rule:
    kind: str            # delay | dropout | corrupt | conflict | spoof
    channel: str
    start: int = 0
    end: int = 10**9
    severity: float = 1.0   # delay: seconds; dropout: probability; corrupt: metres error
    target: str = None      # optional subject filter

    def active(self, r, t):
        return (self.channel == r.channel and self.start <= t < self.end
                and (self.target is None or self.target == r.subject))


class DegradationLayer:
    def __init__(self, rng, log):
        self.rng, self.log = rng, log
        self.rules = []
        self.queue = []      # heap of (deliver_time, seq, trainee, report)
        self.seq = 0

    def add_rule(self, rule, t=None):
        """Instructor can call this mid-exercise."""
        self.rules.append(rule)
        d = asdict(rule)
        d["rule"] = d.pop("kind")
        self.log.add(t or 0, "rule_added", **d)

    def submit(self, r, trainee, t):
        delay, copies = 0, [r]
        for rule in [x for x in self.rules if x.active(r, t)]:
            if rule.kind == "dropout" and self.rng.random() < rule.severity:
                self.log.add(t, "dropped", trainee=trainee, subject=r.subject)
                return
            if rule.kind == "delay":
                delay += int(rule.severity)
            if rule.kind == "corrupt":
                e = rule.severity
                r = Report(**{**asdict(r), "x": r.x + self.rng.uniform(-e, e),
                              "y": r.y + self.rng.uniform(-e, e),
                              "flags": r.flags + ["corrupted"]})
                copies = [r]
            if rule.kind == "conflict":   # second, contradicting report
                e = rule.severity
                fake = Report(**{**asdict(r), "src": "alt_source",
                                 "x": r.x + e, "y": r.y - e,
                                 "strength": max(0, r.strength - 5),
                                 "flags": r.flags + ["conflicting"]})
                copies = copies + [fake]
            if rule.kind == "spoof":      # adversary-injected false report
                # adversary impersonates the real source: same src name, only the flag (instructor-only) differs
                copies = [Report(**{**asdict(r),
                                    "x": r.x + rule.severity, "y": r.y + rule.severity,
                                    "flags": ["spoofed"]})]
        for c in copies:
            self.seq += 1
            heapq.heappush(self.queue, (t + delay, self.seq, trainee, c))

    def poll(self, t):
        """Return {trainee: [(report, age)]} for everything due now."""
        out = {}
        while self.queue and self.queue[0][0] <= t:
            _, _, trainee, rep = heapq.heappop(self.queue)
            out.setdefault(trainee, []).append((rep, t - rep.created))
            self.log.add(t, "delivered", trainee=trainee, subject=rep.subject,
                         age=t - rep.created, flags=rep.flags,
                         x=round(rep.x, 1), y=round(rep.y, 1), strength=rep.strength)
        return out


# ---------- Event log (becomes the AAR) ----------
class EventLog:
    def __init__(self):
        self.events = []

    def add(self, t, kind, **data):
        self.events.append({"t": t, "kind": kind, **data})


# ---------- Trainee + session ----------
class Session:
    def __init__(self, seed=1):
        self.log = EventLog()
        self.world = World(seed)
        self.layer = DegradationLayer(self.world.rng, self.log)
        self.picture = {}     # trainee -> {subject: (report, age)}  what they believe
        self.sensors = []     # (observer_id, subject_id, trainee, channel)

    def add_sensor(self, observer, subject, trainee, channel):
        self.sensors.append((observer, subject, trainee, channel))

    def tick(self):
        w = self.world
        w.step()
        for obs, subj, trainee, ch in self.sensors:
            u = w.units[subj]
            self.layer.submit(Report(obs, subj, u.x, u.y, u.strength, w.t, ch), trainee, w.t)
        delivered = self.layer.poll(w.t)
        for trainee, items in delivered.items():
            for rep, age in items:
                self.picture.setdefault(trainee, {})[rep.subject] = (rep, age)
        return delivered   # {trainee: [(report, age)]} for this tick

    def decide(self, trainee, order, rationale="", confidence=3):
        """Log a decision plus the exact picture the trainee had, next to ground truth."""
        w = self.world
        believed = {s: {"x": round(r.x, 1), "y": round(r.y, 1), "age": w.t - r.created,
                        "flags": r.flags}
                    for s, (r, _) in self.picture.get(trainee, {}).items()}
        truth = {s: {"x": round(u.x, 1), "y": round(u.y, 1)} for s, u in w.units.items()}
        self.log.add(w.t, "decision", trainee=trainee, order=order,
                     rationale=rationale, confidence=confidence,
                     believed=believed, truth=truth)
