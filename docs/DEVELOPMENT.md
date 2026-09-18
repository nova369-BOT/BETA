# Development

How to get a working checkout, run it, and test it.

## Requirements

- Python **3.10+** (3.11 or 3.12 recommended)
- Node **20+**, only if you are rebuilding the frontend
- No network access is required to *run* the app: the bundled `demo` provider
  and the seeded sample datasets are offline

## Setup

```sh
python -m venv .venv

# brue-connect is not on PyPI; install it from git first.
.venv/bin/pip install "git+https://github.com/londonstrategicedge/brue-connect.git"

.venv/bin/pip install -e .
```

`pip install -e .` pulls `lse-data` (imports as `lse`), `brue-language`
(imports as `brue`), FastAPI, uvicorn, pandas, numpy and pyarrow.

> **Watch the import names.** `pip show lse-data` and `import lse_data` do not
> agree — the module is `lse`. The same applies to `brue-language`/`brue`. If an
> import fails, check the distribution-to-module mapping in
> [`ARCHITECTURE.md`](ARCHITECTURE.md) before assuming the install failed.

## Run

```sh
.venv/bin/lset                      # serves http://127.0.0.1:7787 and opens a browser
.venv/bin/lset --no-browser         # headless
```

Loopback only, by default and on purpose. To serve beyond loopback you must name
the host you are serving; see [`../SECURITY.md`](../SECURITY.md).

## Test

```sh
.venv/bin/python -m pytest tests/ -q
```

The suite points `LSE_TERMINAL_CONFIG_DIR` at a temporary directory, so it never
reads or writes your real `~/.config/lse-terminal`.

### Two tests fail on a clean checkout

`tests/test_api.py::test_broker_list_names_each_broker_from_its_own_handshake`
and `::test_probe_does_not_open_a_session` both expect a broker named
`paper-fast`. Neither `brue-connect` nor this repository defines that name
anywhere — brokers are discovered at runtime from the brue-connect registry,
which is populated per account. It is the LSE demo account, and it is
provisioned from `api.londonstrategicedge.com` against a user key.

So these two tests are **not hermetic**: they pass for a developer with the demo
account provisioned and fail everywhere else. The honest fixes are either to
seed the broker in a fixture or to mark them as requiring the demo account.
Neither has been done yet; do not "fix" them by renaming the broker without
confirming which name the demo account actually registers.

Expected baseline otherwise: `2 failed, 154 passed, 1 skipped`.

### Chart render smoke test

```sh
node tools/chart_smoke.mjs
```

Loads the terminal page, mounts `window.LSEChart` against synthetic candles, and
fails unless real pixels are painted. It needs `puppeteer-core` and a Chrome
binary. Upstream calls this mandatory before any commit touching
`frontend/src` or the built chart bundle.

## Frontend

```sh
cd frontend
npm ci
npm run build      # writes into ../lse_terminal/ui/static/
npm run typecheck
```

`npm run dev` is `vite build --watch`, not a dev server: the engine serves the
built bundle, so there is no HMR. After a build, reload the terminal page.

The built bundle is committed. If you change `frontend/src` and do not rebuild,
the running app will not reflect your change — and nothing will tell you.

## Layout

```
lse_terminal/       Python engine, providers, contracts, backtest, ML, UI bundle
frontend/src/       React/TypeScript source for the UI bundle
desktop/            PyInstaller spec + electron-builder packaging
tests/              pytest suite
tools/              chart_smoke.mjs
docs/               architecture and development notes
```

## House style

The codebase is written in a distinctive register and new code should match it:

- Docstrings explain **why**, usually by naming the failure that motivated the
  code. They frequently reference decisions with a commit hash.
- Comments are used to record non-obvious constraints, not to restate the code.
- `from __future__ import annotations` and `X | None` type syntax throughout.
- Module-private helpers are `_`-prefixed; imports inside functions are used
  deliberately to keep heavy or optional dependencies out of import paths
  (`# noqa: PLC0415` marks them).
