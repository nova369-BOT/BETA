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

#: Endpoints the engine keeps off a shared or untrusted client.
#:
#: NOT an opinion invented here. These are exactly the routes whose handlers
#: already call ``deny_hosted()`` / ``deny_hosted_ws()`` in engine/server.py --
#: the project's own statement about what must not be reachable by anyone but
#: the machine's user. Reusing that decision means the two cannot disagree
#: about what is dangerous, and routes are added to it by the same edit that
#: adds the guard to a new handler.
#:
#: ``tests/test_access.py::test_local_only_list_stays_in_sync`` re-derives the
#: set from server.py's AST and fails if this list drifts, so the copy cannot
#: go stale silently.
LOCAL_ONLY_ROUTES: tuple[str, ...] = (
    "/api/ai/approve-request",
    "/api/ai/chat",
    "/api/ai/chats",
    "/api/ai/chats/{cid}",
    "/api/ai/install",
    "/api/ai/instructions",
    "/api/ai/key",
    "/api/ai/login",
    "/api/ai/logout",
    "/api/ai/paste-image",
    "/api/ai/pty",
    "/api/ai/revert",
    "/api/ai/settings",
    "/api/ai/strategy",
    "/api/ai/tool-run",
    "/api/ai/tools",
    "/api/ai/workspace",
    "/api/algo/killswitch",
    "/api/algo/start",
    "/api/algo/stop",
    "/api/assistant",
    "/api/assistant/stamp",
    "/api/assistant/usage",
    "/api/backtest",
    "/api/backtest/montecarlo",
    "/api/backtest/walkforward",
    "/api/broker/account",
    "/api/broker/arm",
    "/api/broker/auth/open",
    "/api/broker/close",
    "/api/broker/connect",
    "/api/broker/credentials",
    "/api/broker/disconnect",
    "/api/broker/fills",
    "/api/broker/list",
    "/api/broker/modify",
    "/api/broker/order",
    "/api/broker/order/cancel",
    "/api/broker/order/pending",
    "/api/broker/orders",
    "/api/broker/probe",
    "/api/broker/subscribe",
    "/api/config/lse_key",
    "/api/data/folders",
    "/api/data/import",
    "/api/data/location",
    "/api/data/open-location",
    "/api/data/preview",
    "/api/data/upload",
    "/api/data/{symbol}",
    "/api/dataviz/parse",
    "/api/lse/databank/import",
    "/api/ml/build-dataset",
    "/api/ml/install",
    "/api/ml/run-code",
    "/api/ml/train",
    "/api/notebooks",
    "/api/notebooks/asset",
    "/api/notebooks/{nid}",
    "/api/quant/add-sample",
    "/api/quant/fit",
    "/api/research/pdf",
    "/api/reveal",
    "/api/ui/events",
    "/api/user-indicators/preview",
    "/api/user-indicators/{filename}",
    "/api/workspace/{section}",
    "/api/ws-files/delete",
    "/api/ws-files/rename",
    "/api/ws-files/write",
    "/mcp",
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
LOCAL_ONLY_EXTRA: tuple[str, ...] = (
    "/api/term/pty",
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


def is_local_only(path: str) -> bool:
    """True when this path is one the engine keeps for the machine's user."""
    return any(route_matches(pattern, path)
               for pattern in LOCAL_ONLY_ROUTES + LOCAL_ONLY_EXTRA)


def describe_exposure(host: str, trusted: frozenset[str]) -> str:
    """A one-line, operator-facing statement of what this process is serving.

    Returned rather than printed so the CLI owns the output stream, and so
    tests can assert on the wording without capturing stdout.
    """
    if is_loopback(host):
        return "serving on loopback only; remote clients are refused"
    exec_note = ("including code execution and the shell"
                 if allow_remote_exec() else
                 "read/data endpoints only; code execution and the shell stay refused")
    return (f"serving beyond loopback for trusted host(s) {sorted(trusted)}: "
            f"{exec_note}")
