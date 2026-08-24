# ESPN Fantasy Football League Dashboard

A dashboard for your own ESPN fantasy football leagues — standings, matchups,
rosters, transactions, and power rankings — for any league you're in, including
private ones.

ESPN publishes no OAuth flow for third-party apps, so the data is fetched with
the two session cookies from a logged-in ESPN account. Those live in GitHub
Actions secrets, are used only inside the build, and never reach the published
site — the build refuses to publish if any of it leaks into the output.

```
GitHub Actions (every 15 min)  ──►  ESPN Fantasy API
        │
        ├──►  data/*.json  ──►  GitHub Pages   (the site)
        └──►  BigQuery                          (season history)
```

The site is a build artifact. A scheduled workflow fetches your league, writes
JSON, and publishes it to Pages — there is no server, nothing to keep running,
and nothing to pay for.

## Layout

| Path | What's in it |
| --- | --- |
| `frontend/` | The site: dashboard views, no build step and no framework. |
| `backend/src/espn_dashboard/build_site.py` | Fetches ESPN and writes the JSON the site reads. |
| `backend/src/espn_dashboard/snapshot_league.py` | Writes the daily BigQuery snapshot. |
| `backend/src/espn_dashboard/espn/` | ESPN client and the defensive transforms. |
| `backend/src/espn_dashboard/analytics.py` | Power rankings. |
| `backend/tests/` | pytest suite — 175 tests, no network. |
| `.github/workflows/site.yml` | The scheduled build. This is what runs the whole thing. |
| `infra/` | GCP bootstrap and deploy scripts, BigQuery table schemas. |
| `analysis/` | 2025 fantasy scoring by player and by NFL team — see [`analysis/README.md`](analysis/README.md). |
| `docs/` | Architecture and security notes. |

## Setting it up

Two repository **secrets** (Settings → Secrets and variables → Actions):

| Secret | Where to get it |
| --- | --- |
| `ESPN_SWID` | DevTools → Application → Cookies → `fantasy.espn.com` |
| `ESPN_S2` | Same place. Percent-encoded values are decoded automatically. |

And two **variables** in the same place:

| Variable | Value |
| --- | --- |
| `LEAGUE_ID` | The number in your ESPN league URL |
| `SEASON` | e.g. `2026` (optional; defaults to the current season) |

Then run **Build and publish the dashboard** from the Actions tab, or wait for
the next scheduled run. That is the whole setup.

## Freshness

The workflow runs every 15 minutes, and **the site is only as fresh as the last
build**. GitHub treats scheduled runs as best-effort and delays them under load —
especially at the top of the hour — so during Sunday scoring expect data to be
15–40 minutes old. The page states its own age (*"Data as of 3:42 PM · updated
12 min ago"*) and marks itself stale past 45 minutes, so nobody mistakes an old
number for a live one. **Refresh** reloads the newest published build;
`workflow_dispatch` forces a fresh one.

## The server path (optional)

A FastAPI backend for Cloud Run is still in `backend/` — it serves the same data
live on request, supports multiple leagues, and lets each viewer connect their
own ESPN account. It is no longer needed for the published site. See
[`infra/deploy.sh`](infra/deploy.sh) if you want live-on-load data instead of
scheduled builds.

## How the connect flow works

1. You paste `SWID` and `espn_s2` into the connect form (one time).
2. The backend calls ESPN with them. Credentials are only stored **after** ESPN
   accepts them — an expired cookie never gets persisted.
3. It asks ESPN's fan-preferences API which leagues that member belongs to, and
   links them all. If that lookup fails (it's the flakiest endpoint ESPN has),
   you can add leagues by ID instead.
4. `espn_s2` is encrypted with AES-256-GCM, with your SWID as additional
   authenticated data, and written to Firestore. The plaintext cookie exists
   in memory only for the duration of a single outbound ESPN request.
5. Your browser gets a JWT whose only claim is your SWID. It carries no cookie
   material, so it can't be replayed against ESPN.
6. **Disconnect** deletes the stored record outright and invalidates every
   session for that account.

Cookies are never returned to the frontend after the initial submission, never
logged, and never written to BigQuery.

## Data refresh

Views fetch live from ESPN on load, behind a 60-second per-user server cache —
so flipping between tabs doesn't re-hit ESPN, but what you see is current.
Separately, a daily Cloud Scheduler job snapshots every connected league into
BigQuery, which is what makes season-long trends and year-over-year comparisons
possible. Tune the cache with `ESPN_CACHE_TTL_SECONDS`; change the snapshot
cadence in `infra/deploy.sh`.

## Local development

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

cp ../.env.example .env
python -c "import os,base64;print('CREDENTIAL_ENCRYPTION_KEY='+base64.b64encode(os.urandom(32)).decode())" >> .env
python -c "import secrets;print('JWT_SECRET='+secrets.token_urlsafe(48))" >> .env

uvicorn espn_dashboard.main:app --reload --port 8080 --app-dir src
```

`CREDENTIAL_STORE=memory` (the default) keeps everything in process — nothing is
persisted, so a restart means reconnecting. Set `CREDENTIAL_STORE=firestore`
with a `GCP_PROJECT` to use real storage.

Then serve the frontend:

```bash
cd frontend && python -m http.server 8080
```

`frontend/config.js` points at `http://localhost:8080` by default; if you run
the API on a different port, update `API_BASE` (and add that origin to
`ALLOWED_ORIGINS`).

Run the checks:

```bash
cd backend && ruff check . && pytest -q
```

## Deploy

Everything below runs in [Cloud Shell](https://shell.cloud.google.com) — a
browser terminal with `gcloud`, `bq`, and `python3` preinstalled and already
authenticated. Nothing needs to be installed locally.

```bash
git clone https://github.com/nashstallings/espn_fantasy_dashboard.git
cd espn_fantasy_dashboard

./infra/bootstrap.sh          # APIs, service account, Firestore, secrets, BigQuery tables
./infra/deploy.sh             # Cloud Run + the daily snapshot schedule
./infra/setup-github-oidc.sh  # optional: let GitHub Actions deploy from then on
```

Both scripts default to the `ff-python-api` project, which this app shares with
Dynasty Tycoon. Override with `PROJECT=other-project ./infra/bootstrap.sh`.
`bootstrap.sh` checks that billing is enabled before it provisions anything, so
a missing billing link fails immediately rather than halfway through.

The BigQuery dataset is created in the `US` multi-region (`BQ_LOCATION`) to match
the other datasets in that project — BigQuery can't join across locations, so a
lone `us-central1` dataset would be permanently unjoinable to them.

Finally, put the printed Cloud Run URL into `frontend/config.js` as `API_BASE`
and commit. The Pages workflow republishes the site on its own.

### Deploying from GitHub Actions

`setup-github-oidc.sh` wires up Workload Identity Federation so
`.github/workflows/deploy.yml` can deploy on every push to `main` that touches
`backend/`. GitHub's OIDC token is exchanged for short-lived Google credentials
at run time — no service account key is ever stored in the repository.

It prints two values to paste into
**Settings → Secrets and variables → Actions → Variables**:

| Variable | Value |
| --- | --- |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | `projects/<number>/locations/global/workloadIdentityPools/github/providers/github-actions` |
| `GCP_DEPLOY_SERVICE_ACCOUNT` | `github-deployer@ff-python-api.iam.gserviceaccount.com` |

The workflow is inert until both exist, so pushes won't fail before you set
them. Two deliberate limits on what CI can do:

* The deploy account has **no Secret Manager access**. The workflow runs
  `deploy.sh` with `MANAGE_SCHEDULER=false`, so it never reads the sync token;
  the daily snapshot job stays with the human-run path, where it belongs — it
  changes far less often than the code does.
* The OIDC provider carries an attribute condition pinning it to this owner, and
  the account binding is narrowed to this one repository. Another repo, even
  under the same owner, cannot impersonate the deployer.

After deploying, the workflow polls `/healthz` until it answers 200. A green
Cloud Run deploy only means the revision was accepted; this is what proves the
app actually boots.

## Publishing one league

By default every viewer connects their own ESPN cookies. Setting
`PUBLIC_LEAGUE_ID` instead publishes a single league read-only — anyone with the
site URL sees standings, rosters, matchups, and transactions with no cookies, no
account, and no DevTools:

```bash
PUBLIC_LEAGUE_ID=123456 PUBLIC_LEAGUE_SEASON=2026 ./infra/deploy.sh
```

Someone in that league still has to connect once; the public view borrows their
credentials server-side to fetch from ESPN. Visitors get the dashboard
immediately, with an optional "Connect your ESPN account" for a personal view.

This makes a private league's data readable by anyone who has the link. Off
unless set, reversible by clearing the variable, and
[`docs/security.md`](docs/security.md) spells out exactly what is and is not
exposed.

## API

All routes except `/healthz`, `/api/healthz`, `/api/season`, and `/api/connect` require
`Authorization: Bearer <token>`.

| Method | Path | |
| --- | --- | --- |
| `GET` | `/api/healthz` | Liveness check — what the deploy workflow polls |
| `GET` | `/api/public/config` | Whether a league is published, and which |
| `GET` | `/api/public/*` | Read-only views of the published league; no auth, no league id |
| `POST` | `/api/connect` | Validate cookies, link leagues, return a session token |
| `GET` | `/api/me` | SWID, linked leagues, connection timestamps |
| `POST` | `/api/me/leagues` | Link a league by ID |
| `DELETE` | `/api/me/leagues/{season}/{league_id}` | Unlink a league |
| `DELETE` | `/api/me` | Disconnect and delete all stored data |
| `GET` | `/api/leagues/{id}/overview` | Standings, power rankings, this week's games |
| `GET` | `/api/leagues/{id}/standings` | Full standings |
| `GET` | `/api/leagues/{id}/matchups?week=` | Matchups for a week |
| `GET` | `/api/leagues/{id}/teams` | Teams and managers |
| `GET` | `/api/leagues/{id}/teams/{team_id}/roster?week=` | Starters and bench with points |
| `GET` | `/api/leagues/{id}/transactions` | Adds, drops, trades |
| `GET` | `/api/leagues/{id}/power-rankings` | Rankings plus the components behind them |
| `POST` | `/internal/sync` | Snapshot to BigQuery (Cloud Scheduler; shared-secret header) |

A session can only read leagues linked to its own account — a valid token plus a
guessed league ID gets a 403, not somebody else's private league.

## Power rankings

Records lie. A 7-3 team that squeaked past bad opponents isn't the same as a 6-4
team leading the league in scoring, so the ranking blends four things:

- **35% all-play win %** — for each completed week, how this team's score would
  have fared against every other team's. The best schedule-luck-free signal there is.
- **25% scoring** — points per game, normalized across the league.
- **25% recent form** — the same, over the last 3 completed weeks.
- **15% actual win %** — record still counts; it's what seeding runs on.

Weights live in `backend/src/espn_dashboard/analytics.py`. Every component is
returned with each team so the UI can show its work, and the `Δ` column compares
power rank to standings rank.

## Caveats

ESPN's fantasy API is unofficial and undocumented. It has no SLA, no versioning,
and can change shape without notice. The code is built for that: every parser is
total (missing fields become defaults, malformed sub-objects are skipped),
requests fail over between ESPN's two read hosts, and the API distinguishes
"your cookies died" from "ESPN is down" so the UI only sends you back to the
connect form when reconnecting would actually help.

`espn_s2` and `SWID` are live ESPN session credentials — treat them like a
password. See [`docs/security.md`](docs/security.md) for how they're handled.
