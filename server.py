"""FastAPI server.

Run (PowerShell):   $env:INSTRUCTOR_CODE="choose-a-code"; uvicorn server:app --host 0.0.0.0 --port 8000
If INSTRUCTOR_CODE is not set, a random code is generated and printed below.
Pages:  /  (landing)   /instructor (needs login)   /trainee
"""
import asyncio
import glob
import os
from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel
from aar import build_aar, to_csv, to_pdf
from auth import Auth, BadCode, Locked, make_code
from rooms import Room

HERE = os.path.dirname(os.path.abspath(__file__))
app = FastAPI(title="Degraded Comms Trainer")
rooms = {}          # code -> Room
sockets = {}        # code -> {"trainees": {role: ws}, "instructors": [ws]}
TICK_SECONDS = 1.0  # real seconds per sim second (lower = faster)
COOKIE = "session"

_code = (os.environ.get("INSTRUCTOR_CODE") or "").strip()
auth = Auth(_code or make_code())
print("=" * 60)
if _code:
    print(" Instructor access code: taken from INSTRUCTOR_CODE")
    if len(_code) < 6:
        print(" WARNING: that code is short. Use 6+ characters.")
else:
    print(f" INSTRUCTOR ACCESS CODE (generated): {auth.code}")
    print(" It changes on every restart/reload. Set INSTRUCTOR_CODE to keep it fixed.")
print("=" * 60)


# ---------- access control ----------
def is_instructor(conn):
    """conn is a Request or WebSocket; both expose .cookies."""
    return auth.valid(conn.cookies.get(COOKIE))


def require_instructor(request: Request):
    if not is_instructor(request):
        raise HTTPException(401, "Instructor login required")


class LoginBody(BaseModel):
    code: str


@app.post("/api/login")
def login(body: LoginBody, request: Request, response: Response):
    ip = request.client.host if request.client else "unknown"
    try:
        token = auth.login(ip, body.code)
    except Locked as e:
        raise HTTPException(429, f"Too many wrong attempts. Try again in {e.retry_after}s.")
    except BadCode:
        raise HTTPException(401, "Wrong access code.")
    # HttpOnly: page scripts can't read it. SameSite=Strict: other websites can't use it.
    response.set_cookie(COOKIE, token, httponly=True, samesite="strict", max_age=auth.ttl, path="/")
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    auth.logout(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    return {"instructor": is_instructor(request)}


# ---------- pages ----------
def page(name):
    return FileResponse(os.path.join(HERE, "static", name))


@app.get("/", include_in_schema=False)
def landing():
    return page("index.html")


@app.get("/trainee", include_in_schema=False)
def trainee_page():
    return page("trainee.html")


@app.get("/instructor", include_in_schema=False)
def instructor_page(request: Request):
    if not is_instructor(request):
        return RedirectResponse("/")
    return page("instructor.html")


@app.get("/test", include_in_schema=False)
def test_page():
    return FileResponse(os.path.join(HERE, "test_page.html"))


# ---------- instructor-only REST ----------
def scenario_path(sid):
    p = os.path.join(HERE, "scenarios", f"{sid}.json")
    if os.path.basename(sid) != sid or not os.path.isfile(p):      # also blocks ../ tricks
        raise HTTPException(404, f"no scenario '{sid}'")
    return p


@app.get("/scenarios", dependencies=[Depends(require_instructor)])
def list_scenarios():
    return sorted(os.path.basename(p)[:-5] for p in glob.glob(os.path.join(HERE, "scenarios", "*.json")))


@app.post("/rooms", dependencies=[Depends(require_instructor)])
async def create_room(scenario: str = "ambush_01"):
    room = Room(scenario_path(scenario))
    rooms[room.code] = room
    sockets[room.code] = {"trainees": {}, "instructors": []}
    asyncio.create_task(run_clock(room.code))
    return {"room": room.code, "roles": room.roles}


def _aar(code):
    if code not in rooms:
        raise HTTPException(404, "no such room")
    return build_aar(rooms[code].export_log())


@app.get("/rooms/{code}/log", dependencies=[Depends(require_instructor)])
def get_log(code: str):
    if code not in rooms:
        raise HTTPException(404, "no such room")
    return rooms[code].export_log()


@app.get("/rooms/{code}/aar", dependencies=[Depends(require_instructor)])
def aar_json(code: str):
    return _aar(code)


@app.get("/rooms/{code}/aar.csv", dependencies=[Depends(require_instructor)])
def aar_csv(code: str):
    return Response(to_csv(_aar(code)), media_type="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=AAR_{code}.csv"})


@app.get("/rooms/{code}/aar.pdf", dependencies=[Depends(require_instructor)])
def aar_pdf(code: str):
    try:
        data = to_pdf(_aar(code))
    except ImportError:
        raise HTTPException(501, "PDF export needs reportlab: pip install reportlab")
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename=AAR_{code}.pdf"})


# ---------- real-time ----------
async def send(ws, payload):
    try:
        await ws.send_json(payload)
    except Exception:
        pass


async def broadcast(code):
    room, socks = rooms[code], sockets[code]
    for role, ws in list(socks["trainees"].items()):
        await send(ws, room.trainee_view(role))      # each player gets ONLY their own view
    for ws in socks["instructors"]:
        await send(ws, room.instructor_view())


async def run_clock(code):
    room = rooms[code]
    while room.state != "finished":
        await asyncio.sleep(TICK_SECONDS)
        if room.state == "running":
            room.tick()
        await broadcast(code)
    await broadcast(code)


@app.websocket("/ws/{code}/trainee/{role}")
async def trainee_ws(ws: WebSocket, code: str, role: str, name: str = "anon"):
    await ws.accept()
    if code not in rooms:
        await ws.send_json({"type": "error", "msg": "no such room"}); await ws.close(); return
    room = rooms[code]
    try:
        room.join(role, name[:40])
    except ValueError as e:
        await ws.send_json({"type": "error", "msg": str(e)}); await ws.close(); return
    sockets[code]["trainees"][role] = ws
    await broadcast(code)
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("type") == "decision":
                try:
                    room.submit_decision(role, str(msg.get("order", ""))[:300],
                                         str(msg.get("rationale", ""))[:1000], msg.get("confidence", 3))
                except (ValueError, TypeError) as e:
                    await send(ws, {"type": "error", "msg": str(e)})
            await broadcast(code)
    except WebSocketDisconnect:
        sockets[code]["trainees"].pop(role, None)
        room.leave(role)


@app.websocket("/ws/{code}/instructor")
async def instructor_ws(ws: WebSocket, code: str):
    if not is_instructor(ws):                 # refuse the handshake: no session cookie, no dashboard
        await ws.close(code=4401)
        return
    await ws.accept()
    if code not in rooms:
        await ws.send_json({"type": "error", "msg": "no such room"}); await ws.close(); return
    room = rooms[code]
    sockets[code]["instructors"].append(ws)
    await send(ws, room.instructor_view())
    try:
        while True:
            msg = await ws.receive_json()
            kind = msg.get("type")
            if kind == "start":
                room.start()
            elif kind == "pause":
                room.pause()
            elif kind == "inject":
                try:
                    room.live_inject(msg["inject"])
                except (KeyError, TypeError, ValueError) as e:
                    await send(ws, {"type": "error", "msg": f"bad inject: {e}"})
            await broadcast(code)
    except WebSocketDisconnect:
        sockets[code]["instructors"].remove(ws)
