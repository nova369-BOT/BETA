"""Who may talk to the engine, and what they are allowed to reach.

The engine binds loopback by default and has no per-request authentication: it
trusts the machine it runs on. That assumption is what keeps the rest of the
design simple, and it stops being true the moment the engine is asked to serve
anything other than loopback -- a reverse proxy, a tunnel, a hosted preview.

Those endpoints are not read-only. `/api/ml/run-code` and `/api/backtest`
execute Python, `/api/ws-files/write` writes files and `/api/term/pty` is an
interactive shell. Reachable by the wrong client, the engine is remote code
execution.

So exposure is explicit and layered rather than one switch:

- Loopback is always trusted. Nothing here changes that.
- A non-loopback host must be named in ``LSE_TERMINAL_TRUSTED_HOSTS``. Names
  are exact hostnames and there is no wildcard, because a wildcard would make
  "trusted" mean "whatever DNS resolves today".
- Trusted hosts still do NOT get the local-only endpoints unless
  ``LSE_TERMINAL_ALLOW_REMOTE_EXEC=1``. A proxy that serves charts has no need
  to serve a shell, so the common case -- showing the terminal through a tunnel
  -- stays safe without the operator having to reason about it.
- ``Origin``, when a browser sends one, must name a host of the same standing.
  That is the anti-DNS-rebinding half: the browser stamps the true page origin
  and a page cannot forge it.

The Host check alone was never an access control. ``Host`` is a header, and any
non-browser client sets it freely, so it only ever stopped the browser-based
attacks (localhost CSRF, DNS rebinding) it was written for. The layers above
are what make non-loopback exposure survivable.
"""

from __future__ import annotations

import os

#: Names that mean "this machine". Kept as an explicit set rather than a
#: substring test so that a host called ``localhost.evil.com`` cannot pass.
LOOPBACK: frozenset[str] = frozenset({"127.0.0.1", "localhost", "::1"})

#: (method, path) pairs the engine keeps off a shared or untrusted client.
#:
#: NOT an opinion invented here. These are exactly the routes whose handlers
#: already call ``deny_hosted()`` / ``deny_hosted_ws()`` in engine/server.py --
#: the project's own statement about what must not be reachable by anyone but
#: the machine's user. Reusing that decision means the two cannot disagree
#: about what is dangerous, and routes are added to it by the same edit that
#: adds the guard to a new handler.
#:
#: The METHOD is part of the key, not just the path, because the project gates
#: per handler rather than per path. ``GET /api/workspace/{section}`` is how the
#: UI loads the user's saved layouts and settings and is deliberately open;
#: ``PUT`` on the same path rewrites them and is not. Matching on path alone
#: refused the reads too, which blanked the workspace in any browser that was
#: not on loopback.
#:
#: ``tests/test_access.py::test_local_only_list_stays_in_sync`` re-derives the
#: set from server.py's AST and fails if this list drifts, so the copy cannot
#: go stale silently.
LOCAL_ONLY_ROUTES: tuple[tuple[str, str], ...] = (
    ("POST", "/api/ai/approve-request"),
    ("WEBSOCKET", "/api/ai/chat"),
    ("GET", "/api/ai/chats"),
    ("DELETE", "/api/ai/chats/{cid}"),
    ("GET", "/api/ai/chats/{cid}"),
    ("PUT", "/api/ai/chats/{cid}"),
    ("WEBSOCKET", "/api/ai/install"),
    ("GET", "/api/ai/instructions"),
    ("POST", "/api/ai/instructions"),
    ("POST", "/api/ai/key"),
    ("WEBSOCKET", "/api/ai/login"),
    ("POST", "/api/ai/logout"),
    ("POST", "/api/ai/paste-image"),
    ("WEBSOCKET", "/api/ai/pty"),
    ("POST", "/api/ai/revert"),
    ("POST", "/api/ai/settings"),
    ("GET", "/api/ai/strategy"),
    ("POST", "/api/ai/tool-run"),
    ("GET", "/api/ai/tools"),
    ("POST", "/api/ai/workspace"),
    ("POST", "/api/algo/killswitch"),
    ("POST", "/api/algo/start"),
    ("POST", "/api/algo/stop"),
    ("POST", "/api/assistant"),
    ("POST", "/api/assistant/stamp"),
    ("GET", "/api/assistant/usage"),
    ("POST", "/api/backtest"),
    ("POST", "/api/backtest/montecarlo"),
    ("POST", "/api/backtest/walkforward"),
    ("POST", "/api/broker/account"),
    ("POST", "/api/broker/arm"),
    ("POST", "/api/broker/auth/open"),
    ("POST", "/api/broker/close"),
    ("POST", "/api/broker/connect"),
    ("POST", "/api/broker/credentials"),
    ("POST", "/api/broker/disconnect"),
    ("GET", "/api/broker/fills"),
    ("GET", "/api/broker/list"),
    ("POST", "/api/broker/modify"),
    ("POST", "/api/broker/order"),
    ("POST", "/api/broker/order/cancel"),
    ("POST", "/api/broker/order/pending"),
    ("GET", "/api/broker/orders"),
    ("POST", "/api/broker/probe"),
    ("POST", "/api/broker/subscribe"),
    ("POST", "/api/config/lse_key"),
    ("DELETE", "/api/data/folders"),
    ("PATCH", "/api/data/folders"),
    ("POST", "/api/data/folders"),
    ("POST", "/api/data/import"),
    ("GET", "/api/data/location"),
    ("POST", "/api/data/open-location"),
    ("POST", "/api/data/preview"),
    ("POST", "/api/data/upload"),
    ("DELETE", "/api/data/{symbol}"),
    ("PATCH", "/api/data/{symbol}"),
    ("POST", "/api/dataviz/parse"),
    ("POST", "/api/lse/databank/import"),
    ("POST", "/api/ml/build-dataset"),
    ("WEBSOCKET", "/api/ml/install"),
    ("POST", "/api/ml/run-code"),
    ("POST", "/api/ml/train"),
    ("POST", "/api/notebooks"),
    ("POST", "/api/notebooks/asset"),
    ("DELETE", "/api/notebooks/{nid}"),
    ("PUT", "/api/notebooks/{nid}"),
    ("POST", "/api/quant/add-sample"),
    ("POST", "/api/quant/fit"),
    ("GET", "/api/research/pdf"),
    ("POST", "/api/reveal"),
    ("GET", "/api/ui/events"),
    ("POST", "/api/user-indicators/preview"),
    ("DELETE", "/api/user-indicators/{filename}"),
    ("POST", "/api/user-indicators/{filename}"),
    ("PUT", "/api/workspace/{section}"),
    ("POST", "/api/ws-files/delete"),
    ("POST", "/api/ws-files/rename"),
    ("POST", "/api/ws-files/write"),
    ("GET", "/mcp"),
    ("POST", "/mcp"),
)


#: Local-only endpoints that do NOT route through the deny_hosted() helpers.
#:
#: /api/term/pty refuses hosted mode with an inline `if hosted:` instead of
#: calling deny_hosted_ws(), so it is invisible to the derivation above. It is
#: an interactive shell and has to be listed anyway.
#:
#: Kept as a separate, explicitly-justified tuple rather than by loosening the
#: derivation to "any handler that mentions `hosted`". That looser test also
#: catches the routes that merely *report* the flag -- /api/config returns
#: ``hosted`` as a field, /api/candles varies its limits -- and locking those
#: out would break exactly the read endpoints a proxy exists to serve.
#: ``tests/test_access.py`` asserts each entry here is still guarded in
#: server.py, so an exception cannot outlive its reason.
LOCAL_ONLY_EXTRA: tuple[tuple[str, str], ...] = (
    ("WEBSOCKET", "/api/term/pty"),
)


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def trusted_hosts() -> frozenset[str]:
    """Explicitly named non-loopback hosts this engine may serve.

    Read from ``LSE_TERMINAL_TRUSTED_HOSTS`` as a comma-separated list of
    hostnames (no scheme, no port -- the port is not part of identity here).
    Empty by default, which is what keeps a plain ``lset`` a loopback-only
    process.
    """
    raw = os.environ.get("LSE_TERMINAL_TRUSTED_HOSTS", "")
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


def allow_remote_exec() -> bool:
    """Whether trusted non-loopback hosts may reach the local-only endpoints.

    Off by default, and deliberately a second switch: an operator who only
    wants to look at charts through a proxy should never have to hold a shell
    exposure in mind to do it.
    """
    return _truthy(os.environ.get("LSE_TERMINAL_ALLOW_REMOTE_EXEC", ""))


def hostname(netloc: str) -> str:
    """The host part of a netloc, lowercased, with the port dropped.

    Handles the three shapes that actually turn up: ``host:port``, a bracketed
    IPv6 literal ``[::1]:7787``, and a bare ``host``. Kept identical in
    behaviour to the check it has always backed, so the loopback decision does
    not change.
    """
    netloc = netloc.strip().lower()
    if netloc.startswith("["):          # [::1]:7799
        return netloc[1:].split("]", 1)[0]
    return netloc.rsplit(":", 1)[0] if ":" in netloc else netloc


def is_loopback(name: str) -> bool:
    return name in LOOPBACK


def is_trusted(name: str, trusted: frozenset[str]) -> bool:
    """Loopback, or a host the operator named. No wildcards by design."""
    return is_loopback(name) or name in trusted


def route_matches(pattern: str, path: str) -> bool:
    """Whether a route pattern matches a request path, segment by segment.

    Segment-wise rather than a string prefix, because the patterns carry
    placeholders: treating ``/api/data/{symbol}`` as the prefix ``/api/data/``
    would also swallow every read endpoint under that tree, which would lock a
    proxy out of the data it is meant to serve.
    """
    pattern_parts = pattern.strip("/").split("/")
    path_parts = path.strip("/").split("/")
    if len(pattern_parts) != len(path_parts):
        return False
    return all(p.startswith("{") or p == q
               for p, q in zip(pattern_parts, path_parts))


def is_local_only(method: str, path: str) -> bool:
    """True when this method+path is one the engine keeps for the machine's user.

    Method is required rather than optional: defaulting it would reintroduce
    the bug this signature exists to prevent, where a read on a path whose
    *write* is gated was refused along with it.
    """
    return any(route_matches(pattern, path) and m == method.upper()
               for m, pattern in LOCAL_ONLY_ROUTES + LOCAL_ONLY_EXTRA)


def describe_exposure(host: str, trusted: frozenset[str]) -> str:
    """A one-line, operator-facing statement of what this process is serving.

    Returned rather than printed so the CLI owns the output stream, and so
    tests can assert on the wording without capturing stdout.

    Hosted mode is described separately because it is a genuinely different
    posture rather than a variant of the trusted-host one: the loopback guard
    is not installed there (the public domain IS the legitimate Host), so what
    holds the code-executing endpoints back is deny_hosted() plus the rate
    limit, not the host allowlist. Reporting "trusted host(s) []" for a public
    deployment would understate what is actually running.
    """
    if _truthy(os.environ.get("LSE_TERMINAL_HOSTED", "")):
        return ("hosted mode: public, no login, code execution and the shell "
                "disabled, visitors rate limited per client")
    if is_loopback(host):
        return "serving on loopback only; remote clients are refused"
    exec_note = ("including code execution and the shell"
                 if allow_remote_exec() else
                 "read/data endpoints only; code execution and the shell stay refused")
    return (f"serving beyond loopback for trusted host(s) {sorted(trusted)}: "
            f"{exec_note}")
