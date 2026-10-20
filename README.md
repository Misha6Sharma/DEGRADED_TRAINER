# Dhurandhar: Degraded-Comms Decision Trainer

A web-based trainer for small-team commanders. The simulation holds the ground truth and sends each
trainee only a degraded copy (delayed, missing, corrupted, conflicting or spoofed reports). Every
decision is logged with what the trainee believed vs. what was true, and exported as an AAR (PDF/CSV/JSON).

## Setup (once)

Requires Python 3.10+.

    python -m venv .venv
    .venv\Scripts\Activate.ps1          # Windows PowerShell   (Mac/Linux: source .venv/bin/activate)
    pip install -r requirements.txt

## Run

    $env:INSTRUCTOR_CODE="choose-a-code"      # Mac/Linux: export INSTRUCTOR_CODE=choose-a-code
    uvicorn server:app --host 0.0.0.0 --port 8000

Open http://127.0.0.1:8000/ . If INSTRUCTOR_CODE is not set, a random code is printed in the terminal.
Stop any old server first (Ctrl+C). Two servers on one port is the most common cause of "wrong access code".

## Play

1. Instructor: open `/`, log in with the code, pick a scenario, click **Create new room**.
2. Trainee (same Wi-Fi, other laptop): open `http://<instructor-laptop-ip>:8000/`, enter the room code and name.
3. Instructor clicks **Start**, fires injects, and watches decisions. Download the AAR (PDF/CSV/JSON) at the end.

Find the IP with `ipconfig` (IPv4 address). Allow Python through Windows Firewall on private networks.

## Tests

    python test_auth.py
    python test_rooms.py
    python test_aar.py

## Layout

    server.py      FastAPI + WebSockets, login, locked routes
    auth.py        instructor access code, sessions, lockout
    rooms.py       rooms, roles, per-player views, decisions
    engine.py      ground truth, reports, degradation rules, event log
    scenario.py    JSON scenario loader and inject timeline
    aar.py         After Action Review: metrics, CSV, PDF
    scenarios/     scenario files (JSON): units, sensors, injects, decision points
    static/        index.html (landing), trainee.html, instructor.html

## Rules for the team

- Never commit the instructor code. Pass it in the environment variable only.
- Use fictional, unclassified scenarios only.
- Run all three test files before pushing.
- The map loads Leaflet from a CDN, so it needs internet.

## Known limits

Orders do not yet change the simulation. Rooms live in memory (download the AAR before restarting).
Scenarios currently have one trainee role. Assessment thresholds (aar.py) are starting values.
