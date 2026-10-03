"""Step 3a: multiplayer room logic (pure Python, no web framework, fully testable).

Key security rule: trainee views are built ONLY from delivered reports.
Ground truth, rules and degradation flags are visible to the instructor only.
"""
import random
import string
from collections import deque
from scenario import ScenarioRunner, RULE_KINDS


def new_code():
    return "".join(random.choices(string.ascii_uppercase, k=4))


class Room:
    def __init__(self, scenario_path, code=None):
        self.code = code or new_code()
        self.runner = ScenarioRunner(scenario_path)
        self.roles = list(self.runner.data["roles"])
        self.players = {}                       # role -> display name
        self.state = "lobby"                    # lobby | running | paused | finished
        self.feeds = {r: deque(maxlen=30) for r in self.roles}   # recent messages per role
        self.latest = {r: {} for r in self.roles}                # last delivered report per subject
        self.prompts = {r: None for r in self.roles}             # open decision prompt per role
        self.answered = {r: set() for r in self.roles}

    # ---- lobby ----
    def join(self, role, name):
        if role not in self.roles:
            raise ValueError(f"unknown role '{role}'")
        if role in self.players:
            raise ValueError(f"role '{role}' already taken")
        self.players[role] = name
        self.runner.session.log.add(self.runner.t, "player_joined", trainee=role, name=name)

    def leave(self, role):
        self.players.pop(role, None)

    def start(self):
        if self.state in ("lobby", "paused"):
            self.state = "running"

    def pause(self):
        if self.state == "running":
            self.state = "paused"

    # ---- simulation ----
    def tick(self):
        """Advance one second if running. Returns list of inject dicts that fired."""
        if self.state != "running":
            return []
        fired, due = self.runner.tick()
        for role, items in self.runner.last_delivered.items():
            for rep, age in items:
                # NOTE: rep.flags deliberately NOT copied: trainees can't see "corrupted"
                msg = {"t": self.runner.t, "src": rep.src, "subject": rep.subject,
                       "x": round(rep.x), "y": round(rep.y), "strength": rep.strength,
                       "observed_at": rep.created, "age": age}
                self.feeds[role].append(msg)
                self.latest[role][rep.subject] = msg
        for dp in due:
            for role in self.roles:
                self.prompts[role] = {"id": dp["at"], "prompt": dp["prompt"], "opened": self.runner.t}
        if self.runner.finished():
            self.state = "finished"
            self.runner.session.log.add(self.runner.t, "scenario_finished")
        return fired

    def live_inject(self, inj):
        """Instructor-triggered degradation, same code path as scripted ones."""
        if inj["kind"] not in RULE_KINDS:
            raise ValueError(f"unknown inject kind '{inj['kind']}'")
        inj.setdefault("duration", 20)
        self.runner.apply_inject(inj)
        self.runner.session.log.add(self.runner.t, "live_inject", **{
            "inject": inj["kind"], "channel": inj["channel"]})

    # ---- decisions ----
    def submit_decision(self, role, order, rationale="", confidence=3):
        if role not in self.roles:
            raise ValueError("unknown role")
        if not order.strip():
            raise ValueError("order is empty")
        confidence = max(1, min(5, int(confidence)))
        pid = self.prompts[role]["id"] if self.prompts[role] else None
        self.runner.session.decide(role, order.strip(), rationale, confidence)
        self.runner.session.log.add(self.runner.t, "decision_meta", trainee=role, prompt_id=pid)
        self.prompts[role] = None

    # ---- views ----
    def trainee_view(self, role):
        """What this player is allowed to see. Nothing else leaves the server."""
        t = self.runner.t
        stale = [dict(m, silent_for=t - m["t"]) for m in self.latest[role].values()]
        return {"type": "state", "room": self.code, "role": role, "t": t,
                "state": self.state, "duration": self.runner.meta["duration"],
                "picture": stale, "feed": list(self.feeds[role])[-10:],
                "prompt": self.prompts[role], "teammates": self.players}

    def instructor_view(self):
        s = self.runner.session
        return {"type": "instructor_state", "room": self.code, "t": self.runner.t,
                "state": self.state, "players": self.players, "roles": self.roles,
                "truth": {k: {"side": u.side, "x": round(u.x), "y": round(u.y),
                              "strength": u.strength} for k, u in s.world.units.items()},
                "active_rules": [r.__dict__ for r in s.layer.rules
                                 if r.start <= self.runner.t < r.end],
                "recent_events": s.log.events[-15:],
                "pending_injects": self.runner.pending_injects,
                "title": self.runner.meta["title"], "duration": self.runner.meta["duration"],
                "channels": sorted({x["channel"] for x in self.runner.data["sensors"]}),
                "beliefs": {role: {sub: {"x": m["x"], "y": m["y"], "stale": self.runner.t - m["observed_at"]}
                                   for sub, m in self.latest[role].items()} for role in self.roles},
                "decisions": [e for e in s.log.events if e["kind"] == "decision"]}

    def export_log(self):
        return {"room": self.code, "scenario": self.runner.meta,
                "events": self.runner.session.log.events}
