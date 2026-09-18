# Hosting LSE Terminal on Render

A step-by-step guide. Read [Before you start](#0-before-you-start) first — it
covers what hosting actually changes, and two things that will surprise you.

---

## 0. Before you start

### This is a hosted *terminal*, not a hosted *desktop app*

LSE Terminal's engine has **no login**. It is built on the assumption that it
runs on the machine of the person using it. Serving it publicly means every
visitor shares one instance, one filesystem, and one rate-limit budget.

The project already handles this, and you should use its answer rather than
inventing one: **hosted mode** (`LSE_TERMINAL_HOSTED=1`). It:

- refuses the endpoints that execute code, write files or move money
  (`deny_hosted()` raises 403 inside their own handlers),
- replaces the loopback guard with a per-client token-bucket rate limit,
- hides the corresponding UI controls.

What it does **not** do is authenticate anyone. That is deliberate upstream and
it is a product decision, not an oversight — but it means **do not put private
data on a hosted instance.** Read [Limitations](#7-limitations-and-risks) before
going further.

### Two things to know before you invest time

1. **The terminal shell UI has no source in this repository.**
   `lse_terminal/ui/static/app.js` (17,054 lines, readable vanilla JS with
   comments) is a committed build artifact. It owns the MARKETS / BACKTEST /
   MY DATA sections, the sidebar and the keybar. The React source under
   `frontend/src` builds **only the chart** (`chart/chart.js`, exposing
   `window.LSEChart`). So you can change the Python engine and the chart freely;
   changing the shell means editing a generated artifact, which will be
   overwritten by the next upstream release.

2. **Hosted mode is single-tenant anyway.** Render runs one container. Every
   visitor shares that filesystem. Hosted mode stops visitors *writing* to it,
   but they can still read the workspace (see Limitations). Fine for a demo or a
   public chart viewer; not a multi-user product.

---

## 1. Push the repository

Render deploys from a Git remote. This repo's session branch is
`arena/01a0b4ba-beta`; `render.yaml` is configured with `branch: main`, so
either merge to `main` first or change that line.

```sh
git push origin arena/01a0b4ba-beta:main     # or open a PR and merge
```

## 2. Confirm the two non-obvious build requirements

Render installs non-editable, so the wheel must ship the UI. It does — verified
by building it and listing the contents, all 15 `ui/static` files are present,
including `app.js`, `chart.js`, `index.html` and the vendor bundles.

The one dependency that is **not on PyPI** is `brue-connect`. It has to come
from git, and it is **pinned to a commit** in `render.yaml`:

```
pip install "git+https://github.com/londonstrategicedge/brue-connect.git@671d94bf6abbecfa7407902ec2cedd5630a8726f"
```

That pin is deliberate. Upstream's own release workflow checks out whatever
`main` is at build time, which means the same source can build differently on
different days. Pinning makes your deploys reproducible; raise the pin
deliberately when you want a newer connector.

`PYTHON_VERSION` is pinned to `3.11.9` because that is the interpreter the
dependency set has actually been tested against here. If the build log reports
the version unavailable, pick another `3.11.x` — do not jump to 3.13, which has
not been verified.

## 3. Deploy with the blueprint

1. Sign in at <https://dashboard.render.com> with GitHub.
2. **New → Blueprint**.
3. Pick the repository (`nova369-BOT/BETA`, or wherever you pushed it).
4. Render reads `render.yaml` from the branch and shows the service it will
   create. Confirm the plan and region, and note that it will prompt you for
   `LSE_API_KEY` — leave it blank if you do not have a key; the demo provider
   and bundled sample data work without one.
5. **Apply**. The first build takes several minutes: `pandas`, `numpy` and
   `pyarrow` are large wheels.
6. Watch the logs. You are looking for the line the engine prints on startup:

```
LSE Terminal WARNING: hosted mode: public, no login, code execution and the
shell disabled, visitors rate limited per client
```

If you see that, the posture is what you think it is. When the deploy reports
live, open the `https://<name>.onrender.com` URL.

### If the build fails

| Symptom | Cause |
| --- | --- |
| `No matching distribution found for brue-connect` | The git install line was dropped or the pin edited |
| Build log warns the Python version is unavailable | Change `PYTHON_VERSION` to another `3.11.x` |
| `lset: refusing to bind '0.0.0.0' without --trusted-host` | `LSE_TERMINAL_HOSTED` is missing or not `"1"` |
| Deploy fails with "no open ports detected" | `$PORT` was replaced with a hardcoded port |

## 4. Verify the deployment

Do these in order; each rules out a whole class of problem.

```sh
BASE=https://<your-service>.onrender.com

# 1. health — Render's own check uses this, so it should be 200
curl -s "$BASE/api/health"

# 2. the shell page and its assets (a missing asset is a blank page)
for p in / /app.js /style.css /chart/chart.js; do
  printf '%s  %s\n' "$(curl -s -o /dev/null -w '%{http_code}' "$BASE$p")" "$p"
done

# 3. data actually flows
curl -s "$BASE/api/candles?provider=demo&symbol=DEMO:BTC&timeframe=1h&limit=3" | head -c 200

# 4. hosted mode is on
curl -s "$BASE/api/config"
```

`/api/config` must report `"hosted": true`. If it says `false`, the env var did
not take effect and the code-executing endpoints are reachable by the public —
stop and fix that before sharing the URL.

Then confirm the lockdown holds from the outside. **Send valid bodies.** An
empty `{}` fails request validation and returns `422` *before* the handler's
refusal runs — nothing executed, but it is a misleading number, so use these:

```sh
# each of these must be 403
printf '%s  ml/run-code\n' "$(curl -s -o /dev/null -w '%{http_code}' -X POST \
  -H 'content-type: application/json' -d '{"code":"print(1)"}' "$BASE/api/ml/run-code")"

printf '%s  ws-files/write\n' "$(curl -s -o /dev/null -w '%{http_code}' -X POST \
  -H 'content-type: application/json' -d '{"path":"x.py","content":"hi"}' "$BASE/api/ws-files/write")"

printf '%s  backtest\n' "$(curl -s -o /dev/null -w '%{http_code}' -X POST \
  -H 'content-type: application/json' \
  -d '{"engine":"python","provider":"userdata","symbol":"GOLD","timeframe":"1h","script":"trades=[]"}' \
  "$BASE/api/backtest")"
```

A refusal reads:

```json
{"detail":"not available in the hosted terminal; download the app from GitHub to use this"}
```

`422` means the request never reached the refusal because it was malformed;
`200` means the endpoint ran and you should stop and investigate.

## 5. Make it yours (optional)

### A custom domain

Dashboard → your service → **Settings → Custom Domains**. Add the domain and
create the `CNAME` Render shows you. Render issues the TLS certificate.

You do **not** need to add the domain to a trusted-host list: in hosted mode the
loopback guard is not installed, so the public domain is accepted as-is. (That
guard only applies to the desktop and self-hosted-proxy modes.)

### A persistent disk (paid plans only)

By default the filesystem is **ephemeral**: every deploy, restart and free-tier
wake starts from a clean image, and anything the engine wrote under
`~/.config/lse-terminal/` is gone. That includes imported data, the workspace,
notebooks and any saved API key.

Free instances cannot attach disks. On a paid instance, add this to the service
in `render.yaml`:

```yaml
    disk:
      name: lse-state
      mountPath: /var/data
      sizeGB: 1
    envVars:
      # ...keep the existing ones, and add:
      - key: LSE_TERMINAL_CONFIG_DIR
        value: /var/data/lse-terminal
```

Two constraints worth knowing before you rely on it: a disk is bound to one
instance, so the service cannot scale beyond `numInstances: 1`; and Render
restarts the service when the disk or its mount path changes.

## 6. Redeploys and updates

Every push to the deployed branch triggers a rebuild. To take an upstream
release, merge it and push:

```sh
git fetch upstream && git merge upstream/main && git push origin main
```

Expect to resolve conflicts in the files listed in
[`ARCHITECTURE.md`](ARCHITECTURE.md#repository-relationship-to-upstream) — keep
local changes to upstream files small for exactly this reason.

## 7. Limitations and risks

Read this list rather than discovering it in production.

1. **No authentication, by design.** Anyone with the URL gets the read and data
   surface. There is no account, no key, no per-user isolation. If you need
   that, it does not exist yet and the work starts with a real auth layer.

2. **Visitors can read the workspace.** `POST /api/ws-files/write` and
   `/rename` and `/delete` are refused in hosted mode, but
   **`GET /api/ws-files` and `GET /api/ws-files/read` are not.** Measured
   against a hosted-mode instance: both return `200`, the listing exposes the
   absolute `root` and `data_root` paths, and the read returns file contents.

   On a fresh deploy the workspace holds only the starter strategies that are
   already public in this repository, so the immediate exposure is small. It
   becomes real the moment anything private is placed there. The fix is one
   line each (`deny_hosted()` at the top of those two handlers), but it changes
   upstream behaviour and has not been applied here — it needs a browser check
   that this environment cannot perform. **Do not seed private files.**

3. **The rate limit is keyed on a spoofable header.** `_HostedRateLimit` reads
   `cf-connecting-ip`, then the first `X-Forwarded-For` hop, then the socket
   peer. Since it is the only thing limiting abuse on a public instance, and
   `X-Forwarded-For` is a client-supplied header, confirm how your edge handles
   it: **Render must overwrite `X-Forwarded-For` with the real client IP**, not
   append to a client-supplied one. If it appends, a client can rotate the value
   and get a fresh bucket per request. Test it before you rely on the limit.

4. **Heavy per-visitor payload.** A cold page load pulls roughly 5.7 MB
   (`app.js` 769 KB + `chart/chart.js` 4.3 MB + CSS). Bandwidth is metered on
   paid plans and slow on free-tier cold starts. A CDN in front is sensible if
   this gets traffic.

5. **Free tier sleeps after ~15 minutes idle** and takes 30–60s to wake. The
   first visitor after idle gets that delay.

6. **Trading is unavailable.** Every `/api/broker/*` endpoint is refused in
   hosted mode, so there is no order entry, no positions and no P&L. That is
   correct for a shared instance.

7. **One instance only, if you attach a disk.** And the workspace is shared
   state across visitors regardless.

8. **`brue-connect` is deployed from a git pin, not a released artifact.**
   Reproducible now, but it is your job to raise the pin. There is no lockfile,
   so transitive Python dependencies can still drift between builds.
