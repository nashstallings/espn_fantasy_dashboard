# Architecture

## Shape

```
                    ┌──────────────────────────────┐
   Browser  ───────►│  GitHub Pages (frontend/)    │
                    │  static HTML/CSS/ES modules  │
                    └──────────────┬───────────────┘
                                   │  fetch + Bearer token
                                   ▼
                    ┌──────────────────────────────┐
                    │  Cloud Run (FastAPI)         │
                    │  auth · proxy · normalize    │
                    └───┬───────────┬──────────┬───┘
                        │           │          │
              cookies   │           │ live     │ daily
              (encrypted)          reads      snapshots
                        ▼           ▼          ▼
                  Firestore     ESPN API    BigQuery
```

## Why each piece

**Static frontend.** No build step, no framework, no bundler. The whole site is
files a browser can run, which is the cheapest thing GitHub Pages can host and
the least that can rot. Views are ES modules under `frontend/js/views/`, each a
pure function from an API response to DOM nodes.

**Cloud Run proxy.** ESPN cookies can't go to the browser, so every ESPN call is
server-side. It also means the frontend deals with one stable, normalized shape
instead of ESPN's raw payloads.

**Firestore for credentials.** Low-volume, per-user, mutable, point-read data —
the opposite of what BigQuery is for.

**BigQuery for history.** ESPN only shows you now. Daily snapshots are what make
"how has this team trended since week 3" or "how does this season compare to
last" answerable at all. Tables are partitioned by `snapshot_date` and clustered
by `league_key`.

## Request path

A dashboard view is one call:

1. `current_swid` verifies the bearer token → a SWID.
2. `DashboardService` loads that SWID's record and checks the requested league
   is linked to it (403 otherwise).
3. The 60-second cache is checked, keyed `swid|season|league|views|week`.
4. On a miss, `espn_s2` is decrypted, one request goes to ESPN, and the raw
   payload is cached.
5. `espn/transform.py` normalizes it; `analytics.py` computes rankings.

Multi-view bundling matters: `mSettings + mTeam + mMatchup` in a single request
is what ESPN's own web app does, and it means the overview, standings, matchups,
and power-rankings tabs all share one cached upstream call.

## Defensive parsing

ESPN's API is undocumented and unversioned, so `transform.py` is written to be
total:

- Every accessor coerces (`_num`, `_int`, `_text`, `_obj`, `_list`) — a null
  where a float belongs becomes `0.0`, not a `TypeError`.
- Both team-naming shapes are handled (`name`, and `location` + `nickname`).
- Unknown enum values render as `SLOT_77` / `POS_31` rather than disappearing,
  so a new ESPN concept shows up as an oddity instead of silently vanishing.
- A malformed sub-object is skipped; only a league with no teams at all is an
  error.
- `client.py` fails over between `lm-api-reads.espn.com` and
  `fantasy.espn.com`, and separates auth failures (don't retry the other host)
  from host failures (do).

Errors are typed (`ESPNAuthError`, `ESPNNotFoundError`, `ESPNUnavailableError`,
`ESPNSchemaError`) and map to distinct HTTP statuses, because the frontend needs
to tell "re-paste your cookies" apart from "ESPN is down".

## Testing

129 tests, no network. `FakeESPNClient` in `tests/conftest.py` answers from a
fixture league (4 teams, 3 completed weeks, 1 live week) built so the expected
answers are checkable by hand — team 3 scores the most and loses, team 1 wins
games it shouldn't, and the power rankings have to prefer team 3.

## Things deliberately left out

- **In-game polling.** On-demand + a 60s cache covers it; polling can be added
  in the frontend later without touching the backend.
- **A players/free-agent view.** Needs the `kona_player_info` view and its own
  filter header. The transaction log resolves names from rosters instead.
- **Draft recaps and keeper values.** `mDraftDetail` is available; nobody has
  asked for it yet.
- **Cross-instance caching.** The TTL cache is per-container. At a 60s TTL and a
  handful of instances that's fine; Memorystore is the answer if it isn't.
