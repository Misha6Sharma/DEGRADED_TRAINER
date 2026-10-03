"""Step 1: instructor access control (pure Python, no web framework, fully testable).

Instructor logs in with a shared access code and receives a random session token
(stored in an HttpOnly cookie by server.py). Failed attempts are rate-limited per IP.
"""
import hmac
import secrets
import time

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"      # no 0/O/1/I to avoid misreading


def make_code(n=8):
    return "".join(secrets.choice(ALPHABET) for _ in range(n))


class BadCode(Exception):
    pass


class Locked(Exception):
    def __init__(self, retry_after):
        super().__init__(f"locked for {retry_after}s")
        self.retry_after = retry_after


class Auth:
    def __init__(self, code, ttl=8 * 3600, max_fail=5, window=60, clock=time.monotonic):
        if not code or not code.strip():
            raise ValueError("access code must not be empty")
        self.code, self.ttl = code.strip(), ttl
        self.max_fail, self.window, self.clock = max_fail, window, clock
        self.sessions = {}      # token -> expiry time
        self.fails = {}         # ip -> [failure times]

    def login(self, ip, code):
        now = self.clock()
        recent = [t for t in self.fails.get(ip, []) if now - t < self.window]
        if len(recent) >= self.max_fail:               # locked out: even the right code is refused
            self.fails[ip] = recent
            raise Locked(int(self.window - (now - recent[0])) + 1)
        if hmac.compare_digest((code or "").strip().encode(), self.code.encode()):
            self.fails.pop(ip, None)
            token = secrets.token_urlsafe(32)
            self.sessions[token] = now + self.ttl
            self._purge(now)
            return token
        recent.append(now)
        self.fails[ip] = recent
        raise BadCode()

    def valid(self, token):
        if not token:
            return False
        exp = self.sessions.get(token)
        if exp is None:
            return False
        if self.clock() >= exp:
            del self.sessions[token]
            return False
        return True

    def logout(self, token):
        self.sessions.pop(token, None)

    def _purge(self, now):
        self.sessions = {k: v for k, v in self.sessions.items() if v > now}
        self.fails = {ip: [t for t in ts if now - t < self.window] for ip, ts in self.fails.items()}
        self.fails = {ip: ts for ip, ts in self.fails.items() if ts}
