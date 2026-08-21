# ESPN Fantasy Football League Dashboard

A dashboard for your own ESPN fantasy football leagues — standings, matchups,
rosters, transactions, and power rankings — for any league you're in, including
private ones.

There is no signup. ESPN publishes no OAuth flow for third-party apps, so you
connect by pasting the two session cookies your browser already holds
(`SWID` and `espn_s2`). Those are validated against ESPN, encrypted, and stored
server-side; your browser gets back a signed session token so you only do it
once.

```
GitHub Pages (static)  ──►  Cloud Run (FastAPI)  ──►  ESPN Fantasy API
                                   │
                                   ├──►  Firestore   (encrypted cookies, league list)
                                   └──►  BigQuery    (daily snapshots for history/trends)
```

## Layout

| Path | What's in it |
| --- | --- |
| `frontend/` | The static site: connect form, league switcher, dashboard views. No build step. |
| `backend/src/espn_dashboard/` | FastAPI app, ESPN client, normalization, power rankings, BigQuery sync. |
| `backend/tests/` | pytest suite — 129 tests, no network. |
| `infra/` | GCP bootstrap and deploy scripts, BigQuery table schemas. |
| `docs/` | Architecture and security notes. |

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

```bash
./infra/bootstrap.sh   # APIs, service account, Firestore, secrets, BigQuery tables
./infra/deploy.sh      # Cloud Run + the daily snapshot schedule
```

Both scripts default to the `ff-python-api` project, which this app shares with
Dynasty Tycoon. Override with `PROJECT=other-project ./infra/bootstrap.sh`.
`bootstrap.sh` checks that billing is enabled before it provisions anything, so
a missing billing link fails immediately rather than halfway through.

The BigQuery dataset is created in the `US` multi-region (`BQ_LOCATION`) to match
the other datasets in that project — BigQuery can't join across locations, so a
lone `us-central1` dataset would be permanently unjoinable to them.

`bootstrap.sh` generates the encryption key, JWT secret, and sync token into
Secret Manager — they are never in the repo. Then put the Cloud Run URL into
`frontend/config.js`, commit, and the Pages workflow publishes the site.

## API

All routes except `/healthz`, `/api/season`, and `/api/connect` require
`Authorization: Bearer <token>`.

| Method | Path | |
| --- | --- | --- |
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
