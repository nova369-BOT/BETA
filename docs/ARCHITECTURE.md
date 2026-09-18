# Architecture

What LSE Terminal is, how the pieces fit, and what a new engineer should know
before changing anything. This describes the system as it exists, not as it is
planned to be.

## Shape

A local Python engine serves an HTTP/WebSocket API and the bundled web UI. The
desktop app is that same engine, frozen, with an Electron shell around it.

```
lset (cli.py)
  └── engine/server.py :: create_app()
        ├── 128 HTTP routes + 8 WebSocket routes
        ├── serves lse_terminal/ui/static (prebuilt React bundle)
        └── talks to: providers, backtest runner, ML scripts,
                      broker hub (brue-connect), agent CLIs over MCP
```

Entry point is `lset` (`pyproject.toml` → `lse_terminal.cli:main`). `main()` is
mostly a dispatch shim, because the frozen desktop build ships no `python.exe`
and has to re-enter itself for `-m`, `-c`, `--run-script` and `--repl`.

## Python engine (`lse_terminal/`)

| Area | Files | Responsibility |
| --- | --- | --- |
| `engine/server.py` | 1 (≈7.6k lines) | Every route, the access guard, hosted-mode policy, AI/agent orchestration |
| `engine/broker_hub.py` | 1 | Broker sessions over the brue-connect wire protocol; orders, fills, accounts |
| `engine/quant_fit.py` | 1 | Model fitting (GARCH, Kalman, HMM, LSTM, autoencoder) |
| `engine/approve_bridge.py` | 1 | MCP stdio bridge for agent permission prompts |
| `providers/` | 5 | Market-data providers behind one contract, plus decoders |
| `contracts/` | 3 | Provider/indicator/type contracts — the stable interfaces |
| `backtest/` | 4 | Strategy runner, walk-forward, Monte Carlo, starters |
| `ml/scripts/` | ~30 | Standalone training scripts run as child processes |
| `indicators/` | — | Built-in indicators, plus Brue-language indicator packs |
| `ui/static/` | — | The committed frontend build (see below) |

**Provider contract.** `contracts/provider.py` defines the interface;
`lse_terminal/testing.py::check_provider` is a compliance harness, and
`tests/test_demo_provider.py` runs it. A new provider is added by implementing
the contract and registering it — not by editing the server.

The bundled `demo` provider is fully offline and is what makes the app usable
with no key, no imports and no network. It is the correct target for tests and
demos; it is not a substitute for live data and does not pretend to be.

## Frontend — and its unusual source split

There are **two** frontend artifacts, and only one of them has source here.
This is the single most surprising thing about the repository, so read it before
planning UI work.

| Artifact | Size | Owns | Source in this repo? |
| --- | --- | --- | --- |
| `ui/static/app.js` | 17,054 lines | The terminal **shell**: MARKETS / BACKTEST / MY DATA, sidebar, keybar | **No — committed artifact** |
| `ui/static/chart/chart.js` | 4.3 MB | The **chart engine**, exposed as `window.LSEChart` | **Yes — `frontend/src`** |

`frontend/vite.config.ts` builds in library mode with
`entry: src/mount.tsx`, `name: 'LSEChart'`, `fileName: 'chart.js'`, emitted into
`lse_terminal/ui/static/chart/`. `mount.tsx` says so directly:

> The terminal's shell (`ui/static/app.js`) is plain JavaScript and owns the
> MARKETS / BACKTEST / MY DATA sections, the sidebar and the keybar. This entry
> point exposes an imperative API on `window.LSEChart` so the shell can mount
> the React chart into its existing `#chart` element, without the shell needing
> React.

So:

- **The Python engine is fully editable.**
- **The chart is fully editable** and rebuilds into `chart/chart.js`.
- **The shell is not buildable here.** `app.js` is readable, commented,
  unminified JavaScript, so it can be patched by hand — but `npm run build` does
  not regenerate it, and a hand patch is a fork you must re-apply on every
  upstream merge. Anything that needs durable shell changes has to happen
  upstream, or the shell has to be replaced with one we own.

The artifacts are committed so a clone runs without a Node toolchain, and both
are included in the built wheel — a non-editable `pip install .` ships the whole
UI, which is what makes containerised deployment work. Sourcemaps and the large
`ts_truth.json` parity fixture are gitignored and regenerated.

## Packaging (`desktop/`)

PyInstaller builds the engine into a sidecar (`desktop/pyi-spec/`), wrapped by
electron-builder. `.github/workflows/release.yml` builds, signs and notarises
both platforms on a `v*` tag.

## Dependencies and their import names

Three packages come from the same organisation. **The distribution name and the
import name differ**, which is a repeated source of confusion:

| Distribution | Import | Source |
| --- | --- | --- |
| `lse-data` | `lse` | PyPI |
| `brue-language` | `brue` | PyPI |
| `brue-connect` | `brueconnect` | **git only — not on PyPI** |

`brue-connect` being git-only and unpinned is a reproducibility defect; see
Known debt.

## Testing

`pytest tests/`. The suite runs against `demo` and `userdata` providers and
never touches the developer's real config (`LSE_TERMINAL_CONFIG_DIR` is pointed
at `tmp_path`). `tools/chart_smoke.mjs` is a render-level check for the chart
bundle and needs a Chrome binary via `puppeteer-core`.

## Known debt

Measured, not estimated. These are the things most likely to hurt next.

1. **God files.** `frontend/src/components/chart/ProChart.tsx` is 9,774 lines;
   `BTChart.tsx` 8,562; `lse_terminal/engine/server.py` 7,588;
   `ChartDrawingOverlay.tsx` 5,131. Any change to chart behaviour currently
   requires holding a very large file in your head. Splitting these along the
   seams that already exist (renderers, interaction, settings) is the highest
   value structural work available.
2. **Thin test coverage.** ~1,800 lines of tests against ~115,000 lines of
   source (27k Python, 88k frontend). The API surface is tested; the chart and
   visualisation layer is not.
3. **`brue-connect` is unpinned and git-only.** Builds are not reproducible from
   published artifacts, and `release.yml` checks out whatever `main` is at build
   time. There is no lockfile. This has already caused a visible failure: two
   tests in `tests/test_api.py` reference a broker named `paper-fast` that no
   installed component provides, so they fail in any environment without that
   broker provisioned. See `docs/DEVELOPMENT.md`.
4. **The `frontend` package is not a workspace member, and only builds half the
   UI.** `pyproject.toml` requires Python only, so a Node toolchain is needed
   separately, and nothing enforces that `chart/chart.js` matches
   `frontend/src`. More seriously, the shell (`app.js`) has no build source at
   all — see the frontend section above. Nothing detects drift between a rebuilt
   chart and the shell that consumes it.
5. **Hosted/lockdown policy is expressed two ways.** Most endpoints call
   `deny_hosted()`, but `/api/term/pty` checks `hosted` inline. That
   inconsistency is why `access.LOCAL_ONLY_EXTRA` has to exist. Normalising on
   the helpers would remove the exception.

## Repository relationship to upstream

This repository tracks `https://github.com/londonstrategicedge/lse-terminal`.
Upstream history is present, so upstream releases are merged rather than
re-vendored:

```sh
git fetch upstream && git merge upstream/main
```

Files are kept at the upstream paths for that reason. Prefer adding new files
over restructuring upstream ones; a large local diff in a hot file makes every
future merge harder.
