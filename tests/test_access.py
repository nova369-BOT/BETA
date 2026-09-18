"""The access policy: who the engine answers, and what they may reach.

Two things are being protected here and they fail differently.

The first is the browser-driven attack class the guard was written for:
localhost CSRF and DNS rebinding. That one is about the Origin a browser
stamps, and it must keep working exactly as before, because a regression there
silently reopens a hole for every page the user visits.

The second is exposure beyond loopback. Before this policy existed, `lset
--host 0.0.0.0` served `/api/ml/run-code`, `/api/ws-files/write` and the PTY
websockets to anything that could reach the port, and the only thing standing
in the way was the Host header -- which a non-browser client sets freely.
These tests pin the replacement: named hosts only, and read/data endpoints
only unless code execution is opted into separately.
"""

import ast
import pathlib

import pytest
from fastapi.testclient import TestClient

from lse_terminal.engine import access
from lse_terminal.engine.server import _AccessGuard, create_app

# A host that is not loopback and is not trusted in any of these tests.
FOREIGN = "evil.example.com"
TRUSTED = "preview.example.com"


def scope(host: str, path: str = "/api/health", origin: str | None = None,
          kind: str = "http", method: str = "GET") -> dict:
    """The minimum ASGI scope the guard reads.

    Method is explicit because the policy keys on it: a test that sent every
    path as a GET would pass whether or not the write on that path is gated.
    """
    headers = [(b"host", host.encode())]
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    out = {"type": kind, "path": path, "headers": headers}
    if kind == "http":
        out["method"] = method
    return out


def guard(*trusted: str) -> _AccessGuard:
    return _AccessGuard(app=None, trusted=frozenset(trusted))


# ── hostname parsing ─────────────────────────────────────────────────────

@pytest.mark.parametrize("netloc,expected", [
    ("127.0.0.1:7787", "127.0.0.1"),
    ("127.0.0.1", "127.0.0.1"),
    ("LOCALHOST:80", "localhost"),
    ("[::1]:7799", "::1"),
    ("[::1]", "::1"),
    ("preview.example.com:443", "preview.example.com"),
])
def test_hostname_strips_port_and_lowercases(netloc, expected):
    assert access.hostname(netloc) == expected


def test_loopback_membership_is_exact_not_substring():
    """A name that merely contains 'localhost' must not be treated as local."""
    assert access.is_loopback("localhost")
    assert not access.is_loopback("localhost.evil.com")
    assert not access.is_loopback("notlocalhost")


# ── the local-only route matcher ─────────────────────────────────────────

def test_route_matches_placeholders_by_segment():
    assert access.route_matches("/api/ai/chats/{cid}", "/api/ai/chats/abc")
    assert not access.route_matches("/api/ai/chats/{cid}", "/api/ai/chats/a/b")
    assert not access.route_matches("/api/ai/chats/{cid}", "/api/ai/chats")


def test_placeholder_route_does_not_swallow_sibling_tree():
    """'/api/data/{symbol}' is one segment, not the whole '/api/data/' tree.

    Read endpoints under the same tree must stay reachable through a proxy;
    a prefix test here would lock them out.
    """
    assert access.is_local_only("DELETE", "/api/data/GOLD")
    assert not access.is_local_only("DELETE", "/api/data")
    assert not access.is_local_only("DELETE", "/api/data/anything/else")


def test_a_gated_write_does_not_gate_the_read_on_the_same_path():
    """The bug this matching exists to prevent.

    ``GET /api/workspace/{section}`` is how the UI loads the user's saved
    layouts, settings and shell, and upstream deliberately leaves it open;
    only PUT rewrites them. Matching on path alone refused the reads too, so
    the workspace came up blank for any browser that was not on loopback.
    The same split applies to the user-indicator files.
    """
    assert access.is_local_only("PUT", "/api/workspace/layouts")
    assert not access.is_local_only("GET", "/api/workspace/layouts")
    assert not access.is_local_only("GET", "/api/workspace/settings")
    assert not access.is_local_only("GET", "/api/workspace/shell")
    assert access.is_local_only("POST", "/api/user-indicators/foo.py")
    assert not access.is_local_only("GET", "/api/user-indicators/foo.py")
    assert not access.is_local_only("GET", "/api/user-indicators/template")


def test_local_only_covers_the_code_execution_paths():
    for method, path in (("POST", "/api/backtest"), ("POST", "/api/ml/run-code"),
                         ("WEBSOCKET", "/api/term/pty"),
                         ("POST", "/api/ws-files/write"),
                         ("POST", "/api/ai/tool-run"),
                         ("POST", "/api/broker/order")):
        assert access.is_local_only(method, path), f"{method} {path}"


def test_read_endpoints_stay_remote_reachable():
    for method, path in (("GET", "/api/health"), ("GET", "/api/candles"),
                         ("GET", "/api/instruments"), ("GET", "/api/indicators"),
                         ("GET", "/api/providers"), ("GET", "/api/config"),
                         ("WEBSOCKET", "/api/ws")):
        assert not access.is_local_only(method, path), f"{method} {path}"


def test_local_only_list_stays_in_sync():
    """The list is a copy of what server.py gates, so prove it is not stale.

    Regenerating from the AST is the only honest way to check a hand-copied
    set. If a handler gains deny_hosted() without gaining a row here, the two
    would disagree about what is dangerous and the stricter one would be the
    one that is wrong.
    """
    src = pathlib.Path(access.__file__).with_name("server.py").read_text()
    tree = ast.parse(src)
    deny = {"deny_hosted", "deny_hosted_ws"}
    derived = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = ast.get_source_segment(src, node) or ""
        if not any(d in body for d in deny):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call) or len(dec.args) != 1:
                continue
            f = dec.func
            if not (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                    and f.value.id == "app"):
                continue
            arg = dec.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                derived.add((f.attr.upper(), arg.value))
    assert derived, "derivation found nothing: the check itself is broken"
    assert derived == set(access.LOCAL_ONLY_ROUTES)


def test_documented_exceptions_are_still_guarded():
    """Every LOCAL_ONLY_EXTRA entry must still be refused in hosted mode.

    The tuple exists for endpoints guarded inline rather than through the
    deny_hosted() helpers. If one of them is later fixed to use the helpers it
    would show up in the derivation too, and this test failing is the signal to
    drop the exception rather than carry a stale one.
    """
    src = pathlib.Path(access.__file__).with_name("server.py").read_text()
    tree = ast.parse(src)
    guarded = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = ast.get_source_segment(src, node) or ""
        if "hosted" not in body and "deny_hosted" not in body:
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call) or len(dec.args) != 1:
                continue
            f = dec.func
            if not (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                    and f.value.id == "app"):
                continue
            arg = dec.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                guarded.add(arg.value)
    for _method, pattern in access.LOCAL_ONLY_EXTRA:
        assert pattern in guarded, (
            f"{pattern} is listed as a local-only exception but no handler for "
            "it checks hosted mode; drop it from LOCAL_ONLY_EXTRA")


# ── the guard's decision ─────────────────────────────────────────────────

def test_loopback_is_allowed_without_configuration():
    assert guard()._refusal(scope("127.0.0.1:7787")) == ""


def test_loopback_origin_for_loopback_request_is_allowed():
    assert guard()._refusal(
        scope("127.0.0.1:7787", origin="http://127.0.0.1:7787")) == ""


def test_foreign_origin_is_refused_even_from_loopback():
    """The original control: a page on another site must not drive the engine.

    The request reaches 127.0.0.1 (that is what a browser does for a CSRF
    attempt), so only the Origin distinguishes it. This must keep failing.
    """
    assert guard()._refusal(
        scope("127.0.0.1:7787", origin=f"http://{FOREIGN}")) != ""


def test_null_origin_is_refused():
    """Sandboxed iframes and file:// pages send 'null'; it must not pass."""
    assert guard()._refusal(scope("127.0.0.1:7787", origin="null")) != ""


def test_untrusted_non_loopback_host_is_refused():
    assert guard()._refusal(scope(FOREIGN)) != ""


def test_trusted_host_may_reach_read_endpoints():
    assert guard(TRUSTED)._refusal(scope(TRUSTED, "/api/candles")) == ""


def test_trusted_host_may_reach_read_endpoints_with_matching_origin():
    assert guard(TRUSTED)._refusal(
        scope(TRUSTED, "/api/candles", origin=f"https://{TRUSTED}")) == ""


def test_trusted_host_is_refused_for_local_only_endpoints():
    """Trusting a host must not hand it a shell."""
    for method, path in (("POST", "/api/ml/run-code"),
                         ("POST", "/api/ws-files/write"),
                         ("POST", "/api/backtest"),
                         ("POST", "/api/broker/order"),
                         ("PUT", "/api/workspace/layouts")):
        assert guard(TRUSTED)._refusal(scope(TRUSTED, path, method=method)) != "", path


def test_trusted_host_reaches_local_only_endpoints_only_with_opt_in(monkeypatch):
    monkeypatch.setenv("LSE_TERMINAL_ALLOW_REMOTE_EXEC", "1")
    assert access.allow_remote_exec() is True
    assert guard(TRUSTED)._refusal(
        scope(TRUSTED, "/api/ml/run-code", method="POST")) == ""


def test_remote_exec_opt_in_does_not_trust_unnamed_hosts(monkeypatch):
    """The exec switch is not a bypass of the host allowlist."""
    monkeypatch.setenv("LSE_TERMINAL_ALLOW_REMOTE_EXEC", "1")
    assert guard(TRUSTED)._refusal(
        scope(FOREIGN, "/api/ml/run-code", method="POST")) != ""


def test_loopback_keeps_local_only_endpoints_without_any_env(monkeypatch):
    """The local user is the one who installed the app; no gate for them."""
    monkeypatch.delenv("LSE_TERMINAL_ALLOW_REMOTE_EXEC", raising=False)
    assert guard()._refusal(
        scope("127.0.0.1:7787", "/api/ml/run-code", method="POST")) == ""


def test_websocket_scope_is_judged_by_the_same_rules():
    assert guard(TRUSTED)._refusal(
        scope(TRUSTED, "/api/terms", kind="websocket")) == ""
    assert guard(TRUSTED)._refusal(
        scope(TRUSTED, "/api/term/pty", kind="websocket")) != ""
    assert guard()._refusal(scope(FOREIGN, kind="websocket")) != ""


def test_lifespan_scope_passes_through():
    """Only http/websocket are policed; startup must not be blocked."""
    g = guard()
    assert g._refusal({"type": "lifespan", "headers": []}) == ""


# ── environment parsing ──────────────────────────────────────────────────

def test_trusted_hosts_parses_a_list(monkeypatch):
    monkeypatch.setenv("LSE_TERMINAL_TRUSTED_HOSTS", " A.example.com , b.example.com ")
    assert access.trusted_hosts() == frozenset({"a.example.com", "b.example.com"})


def test_trusted_hosts_is_empty_by_default(monkeypatch):
    monkeypatch.delenv("LSE_TERMINAL_TRUSTED_HOSTS", raising=False)
    assert access.trusted_hosts() == frozenset()


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_allow_remote_exec_accepts_the_usual_truthy_spellings(monkeypatch, value):
    monkeypatch.setenv("LSE_TERMINAL_ALLOW_REMOTE_EXEC", value)
    assert access.allow_remote_exec() is True


@pytest.mark.parametrize("value", ["", "0", "no", "off", "false"])
def test_allow_remote_exec_is_off_otherwise(monkeypatch, value):
    monkeypatch.setenv("LSE_TERMINAL_ALLOW_REMOTE_EXEC", value)
    assert access.allow_remote_exec() is False


def test_describe_exposure_names_the_hosts_and_the_limits(monkeypatch):
    monkeypatch.delenv("LSE_TERMINAL_ALLOW_REMOTE_EXEC", raising=False)
    assert "loopback only" in access.describe_exposure("127.0.0.1", frozenset())
    text = access.describe_exposure("0.0.0.0", frozenset({TRUSTED}))
    assert TRUSTED in text and "read/data endpoints only" in text
    monkeypatch.setenv("LSE_TERMINAL_ALLOW_REMOTE_EXEC", "1")
    assert "code execution" in access.describe_exposure("0.0.0.0", frozenset({TRUSTED}))


# ── wiring: the guard is actually installed, and configured from the env ──

@pytest.fixture()
def app_for(tmp_path, monkeypatch):
    def build(**env):
        monkeypatch.setenv("LSE_TERMINAL_CONFIG_DIR", str(tmp_path))
        monkeypatch.delenv("LSE_TERMINAL_HOSTED", raising=False)
        monkeypatch.delenv("LSE_TERMINAL_TRUSTED_HOSTS", raising=False)
        monkeypatch.delenv("LSE_TERMINAL_ALLOW_REMOTE_EXEC", raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        return create_app()
    return build


def test_app_serves_loopback_and_refuses_a_foreign_host(app_for):
    c = TestClient(app_for(), base_url="http://127.0.0.1")
    assert c.get("/api/health").status_code == 200
    assert TestClient(app_for(), base_url=f"http://{FOREIGN}").get(
        "/api/health").status_code == 403


def test_app_serves_a_trusted_host_end_to_end(app_for):
    """The case a proxy or tunnel needs: the preview host is answered."""
    app = app_for(LSE_TERMINAL_TRUSTED_HOSTS=TRUSTED)
    c = TestClient(app, base_url=f"http://{TRUSTED}")
    assert c.get("/api/health").json()["ok"] is True
    assert c.get("/api/health", headers={"origin": f"https://{TRUSTED}"}).status_code == 200


def test_app_refuses_local_only_routes_for_a_trusted_host(app_for):
    app = app_for(LSE_TERMINAL_TRUSTED_HOSTS=TRUSTED)
    c = TestClient(app, base_url=f"http://{TRUSTED}")
    assert c.post("/api/ml/run-code", json={"source": "import os"}).status_code == 403
    assert c.post("/api/ws-files/write", json={"path": "x", "text": "y"}).status_code == 403


def test_local_only_routes_still_work_on_loopback(app_for):
    """Locking down remote callers must not break the local user."""
    c = TestClient(app_for(), base_url="http://127.0.0.1")
    # A missing/invalid body is a 4xx from the handler, which proves the
    # request reached it rather than being refused by the guard.
    assert c.post("/api/ml/run-code", json={}).status_code != 403


# ── the CLI refuses to publish a shell by accident ───────────────────────

def test_cli_refuses_a_non_loopback_bind_without_a_trusted_host(monkeypatch):
    from lse_terminal import cli
    monkeypatch.delenv("LSE_TERMINAL_TRUSTED_HOSTS", raising=False)
    assert cli.main(["--host", "0.0.0.0", "--no-browser"]) == 2


def test_cli_starts_when_the_host_is_named(monkeypatch, tmp_path, capsys):
    from lse_terminal import cli
    import uvicorn
    started = {}

    def fake_run(app, **kw):
        started.update(kw)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setenv("LSE_TERMINAL_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("LSE_TERMINAL_TRUSTED_HOSTS", raising=False)
    rc = cli.main(["--host", "0.0.0.0", "--port", "7999", "--no-browser",
                   "--trusted-host", TRUSTED])
    assert rc == 0
    assert started["host"] == "0.0.0.0"
    # The exposure is announced, not discovered later.
    assert TRUSTED in capsys.readouterr().err


def test_cli_loopback_start_is_quiet_about_exposure(monkeypatch, tmp_path, capsys):
    from lse_terminal import cli
    import uvicorn
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: None)
    monkeypatch.setenv("LSE_TERMINAL_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("LSE_TERMINAL_TRUSTED_HOSTS", raising=False)
    assert cli.main(["--no-browser", "--port", "7998"]) == 0
    assert "WARNING" not in capsys.readouterr().err
