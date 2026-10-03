"""Run: python test_auth.py"""
from auth import Auth, BadCode, Locked, make_code


class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t


def mk(**kw):
    c = Clock()
    return Auth("SECRET42", clock=c, **kw), c


def raises(exc, fn):
    try:
        fn()
    except exc:
        return True
    return False


def test_right_and_wrong_code():
    a, _ = mk()
    assert raises(BadCode, lambda: a.login("1.1.1.1", "nope"))
    assert raises(BadCode, lambda: a.login("1.1.1.1", ""))
    assert raises(BadCode, lambda: a.login("1.1.1.1", None))
    tok = a.login("1.1.1.1", "  SECRET42 ")          # surrounding whitespace tolerated
    assert a.valid(tok)
    assert raises(BadCode, lambda: a.login("1.1.1.1", "secret42"))   # case-sensitive


def test_garbage_tokens_invalid():
    a, _ = mk()
    for t in (None, "", "abc", "x" * 100):
        assert not a.valid(t)


def test_lockout_then_recovery():
    a, c = mk(max_fail=5, window=60)
    for _ in range(5):
        assert raises(BadCode, lambda: a.login("9.9.9.9", "wrong"))
    assert raises(Locked, lambda: a.login("9.9.9.9", "wrong"))
    assert raises(Locked, lambda: a.login("9.9.9.9", "SECRET42"))    # right code still refused while locked
    c.t += 61
    assert a.valid(a.login("9.9.9.9", "SECRET42"))                    # window passed: works again


def test_lockout_is_per_ip():
    a, _ = mk(max_fail=3)
    for _ in range(3):
        assert raises(BadCode, lambda: a.login("A", "bad"))
    assert raises(Locked, lambda: a.login("A", "SECRET42"))
    assert a.valid(a.login("B", "SECRET42"))                          # attacker on A doesn't lock out B


def test_success_clears_failures():
    a, _ = mk(max_fail=3)
    for _ in range(2):
        assert raises(BadCode, lambda: a.login("A", "bad"))
    a.login("A", "SECRET42")
    for _ in range(2):                                                # counter reset, so 2 more fails allowed
        assert raises(BadCode, lambda: a.login("A", "bad"))


def test_session_expiry_and_logout():
    a, c = mk(ttl=100)
    t1, t2 = a.login("A", "SECRET42"), a.login("A", "SECRET42")
    assert t1 != t2 and a.valid(t1) and a.valid(t2)
    a.logout(t1)
    assert not a.valid(t1) and a.valid(t2)
    c.t += 101
    assert not a.valid(t2)


def test_purge_keeps_memory_bounded():
    a, c = mk(ttl=10, window=10)
    for i in range(50):
        assert raises(BadCode, lambda: a.login(f"ip{i}", "bad"))
    c.t += 20
    a.login("fresh", "SECRET42")
    assert len(a.fails) == 0 and len(a.sessions) == 1


def test_code_generation():
    codes = {make_code() for _ in range(50)}
    assert len(codes) == 50 and all(len(c) == 8 and c.isalnum() and c == c.upper() for c in codes)
    assert not any(ch in "01OI" for c in codes for ch in c)
    assert raises(ValueError, lambda: Auth("   "))


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print("PASS", t.__name__)
    print(f"\n{len(tests)}/{len(tests)} passed")
