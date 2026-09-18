# Security model

LSE Terminal runs a local engine (`lset`) that serves an HTTP/WebSocket API and
the bundled web UI. This document describes who the engine trusts, what it
serves, and how to expose it beyond the machine without handing away a shell.

## The base assumption

The engine binds loopback by default and has **no per-request authentication**.
It trusts the machine it runs on. That is a deliberate trade: the alternative is
a login for a program the user just launched on their own laptop.

Everything else follows from that assumption. It stops being true the moment the
engine serves anything other than loopback, and most of what it serves is not
read-only:

| Endpoint | Capability |
| --- | --- |
| `/api/ml/run-code`, `/api/backtest` | executes Python as the user |
| `/api/ws-files/write`, `/api/ws-files/delete` | writes and deletes files in the workspace |
| `/api/term/pty`, `/api/ai/pty` (WebSocket) | interactive shell |
| `/api/ml/install`, `/api/ai/install` (WebSocket) | installs packages |
| `/api/broker/*` | places orders, holds broker credentials |
| `/api/config/lse_key` | writes the user's API key |

Reachable by the wrong client, the engine is remote code execution.

## What changed, and why

The engine has always installed a guard that requires the `Host` header to be a
loopback name and, when present, `Origin` to be a loopback origin. That guard is
correct for what it was written to stop: localhost CSRF and DNS rebinding, where
a *browser* is the attacker's tool and cannot forge either header.

It was never an access control, because `Host` is just a header. A non-browser
client sets it to anything:

```
curl -H 'Host: 127.0.0.1' http://<host>:7787/api/ml/run-code
```

Before this change, `lset --host 0.0.0.0` therefore published all of the
endpoints above to anyone who could reach the port, and the guard waved the
request through. Binding beyond loopback was described in the source as needing
to be "an explicit decision", but nothing enforced that decision.

## The policy now

Implemented in `lse_terminal/engine/access.py`, applied by `_AccessGuard` in
`lse_terminal/engine/server.py`.

1. **Loopback is always trusted.** Unchanged, and no configuration can alter it.
2. **A non-loopback host must be named.** Set `LSE_TERMINAL_TRUSTED_HOSTS`
   (comma-separated) or pass `--trusted-host HOST`. Names are exact; there is
   no wildcard, because a wildcard makes "trusted" mean "whatever DNS resolves
   today".
3. **A named host gets read and data endpoints only.** The endpoints in the
   table above stay refused unless `LSE_TERMINAL_ALLOW_REMOTE_EXEC=1` (or
   `--allow-remote-exec`). A proxy that serves charts has no need to serve a
   shell, so the common case is safe without the operator reasoning about it.
4. **`Origin`, when a browser sends one, must name a host of the same standing.**
   This is the anti-DNS-rebinding half and it is unchanged. `null` is refused.
5. **Binding beyond loopback without naming a host is refused at startup**, with
   an explanation. The failure is closed, not open.

The set of endpoints held back is not a hand-written opinion: it is exactly the
routes whose handlers already call `deny_hosted()` / `deny_hosted_ws()`, plus
`/api/term/pty` (which guards hosted mode inline). See `LOCAL_ONLY_ROUTES` and
`LOCAL_ONLY_EXTRA` in `access.py`. `tests/test_access.py` re-derives the set
from `server.py`'s AST and fails if the copy drifts, so the two cannot disagree
about what is dangerous.

## Serving the terminal beyond loopback

For a reverse proxy or tunnel, name the host clients will use:

```sh
lset --host 0.0.0.0 --no-browser \
     --trusted-host terminal.example.com
```

The engine prints a warning to stderr stating what it is serving. Charts, data,
instruments and indicators work through the proxy; code execution, the shell,
workspace writes and broker actions are refused with an explanatory 403.

Only add `--allow-remote-exec` if those clients genuinely need to run code. Doing
so means anyone who can reach that hostname can execute code as the user running
the engine.

## Known limitations

- **There is no authentication.** Disclosure to a trusted host is controlled,
  not authenticated. Anyone who can reach the port and set a trusted (or
  loopback) `Host` header can reach the read/data surface. A shared secret or
  per-session token for non-loopback binds is the next piece of work; it is not
  implemented.
- **`--allow-remote-exec` is all-or-nothing.** There is no scoping to a subnet,
  a user, or a path.
- **The workspace is the user's home-adjacent directory**, so file endpoints are
  as sensitive as the user's files.
- **Hosted mode (`LSE_TERMINAL_HOSTED=1`) is a different code path** and does
  not install this guard; it assumes nginx fronts it. Its own lockout of
  host-mutating endpoints is covered by tests in `tests/test_api.py` and
  `tests/test_userdata_and_quant.py`.

## Reporting

Security issues should go to the maintainers privately rather than into a public
issue. There is no bug-bounty programme.
