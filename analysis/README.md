# 2025 fantasy scoring

What every fantasy-relevant player scored in the 2025 NFL regular season, and
what each NFL team's skill positions produced — built for looking backwards
before projecting 2026.

This sits beside the dashboard rather than inside it. The dashboard reports
*your league*: who beat whom, what your roster scored. These files report *the
league*: where fantasy points came from across all 32 teams, regardless of who
happened to own the player.

## Regenerate

```bash
python analysis/build_fantasy_season.py --season 2025
```

Standard library only — no credentials, no GCP project, no dependencies. Source
data is pulled from [nflverse](https://github.com/nflverse/nflverse-data)'s
public releases and cached in `analysis/.cache/` (gitignored); delete that
directory to force a refresh. `--season 2024` writes the prior year alongside,
which is what you want for a two-year baseline rather than a one-year snapshot.

The kicking, D/ST, and roster-movement arithmetic has its own tests, which need
no network:

```bash
pytest analysis/tests -q
```

## The files

### `player_fantasy_2025.csv` — 658 rows, one per player

Sorted by PPR points. Covers QB, RB, WR, TE, K, and FB.

| Column group | Columns |
| --- | --- |
| Identity | `player_id`, `player`, `position`, `team`, `teams_played_for`, `games` |
| Scoring | `fantasy_points_std`, `fantasy_points_half_ppr`, `fantasy_points_ppr`, and a `ppg_` variant of each |
| Rank | `pos_rank_ppr`, `pos_rank_std` — rank within the player's own position |
| Consistency | `best_week_ppr`, `worst_week_ppr`, `stdev_ppr`, `boom_weeks` (≥20 PPR), `bust_weeks` (<5 PPR) |
| Volume | passing, rushing, and receiving counting stats, plus `target_share`, `air_yards_share`, `wopr` |
| Kicking | `fg_made`, `fg_att`, `fg_made_50_plus`, `pat_made` |
| 2026 | `age_next_season`, `years_exp`, `team_next_season`, `status_next_season`, `movement` |

`movement` is the column to sort on when projecting. It reads `same team`,
`new team`, or `not rostered`, against the 2026 rosters as they stand — so it
answers whether last year's production is still attached to the same offense.

Three scoring formats are given because leagues differ and the difference is
not cosmetic: full PPR moves a target-heavy back like De'Von Achane up and a
touchdown-dependent one down. `_std` is standard (4-point passing touchdowns),
`_ppr` adds a point per reception, `_half_ppr` splits them.

`stdev_ppr` alongside `ppg_ppr` is what separates a startable weekly floor from
a player whose season total was three big afternoons. Two players at 14 PPG are
not the same asset if one of them has a standard deviation of 5 and the other
of 13.

### `player_fantasy_2025_weekly.csv` — 6,651 rows

The same players, one row per game: `week`, `team`, `opponent`, and points in
all three formats. This is the file to load if you want to do your own variance
work, strength-of-schedule splits, or first-half/second-half comparisons.

### `team_fantasy_2025.csv` — 166 rows, one per NFL team and position

What each team's QB/RB/WR/TE/K room produced, with `share_of_team_ppr` for how
the offense divided its points, `players_used` and `top_producer_share` for how
concentrated the room was, and `returning_ppr` / `returning_pct` /
`departed_ppr` for how much of it is still on the roster in 2026.

Points are attributed to the team a player was on **that week**, not to
whichever team he finished the season with. A back traded at the deadline
produced for two offenses, and crediting all of it to one misrepresents both.

### `dst_fantasy_2025.csv` — 32 rows

Team defense and special teams, scored as a fantasy unit and ranked: sacks,
takeaways, defensive and return touchdowns, blocked kicks, points allowed per
game, and shutouts.

## Scoring assumptions

Skill-position points come from nflverse, which computes standard scoring
(0.04/passing yard, 4 per passing touchdown, −2 per interception, 0.1/rushing
and receiving yard, 6 per touchdown, −2 per fumble lost, 2 per two-point
conversion). Half and full PPR are derived from it.

Kicking and D/ST are **not** in nflverse's `fantasy_points` — it reports every
kicker at roughly zero — so both are computed in
`build_fantasy_season.py` on ESPN's default rules:

* **Kicking** — field goals 3 points inside 40, 4 from 40–49, 5 from 50+; extra
  points 1; missed or blocked field goals and missed extra points −1.
* **D/ST** — sack 1, interception 2, fumble recovery 2, safety 2, blocked kick
  2, defensive or return touchdown 6, plus points-allowed tiers (shutout 5,
  1–6 → 4, 7–13 → 3, 14–17 → 1, 18–27 → 0, 28–34 → −1, 35–45 → −3, 46+ → −5).

Both are constants at the top of the script. If your league scores field goals
by distance differently, or gives a point per first down, change them there and
re-run rather than adjusting the output by hand.

## Two things these files do not know

**Your league's scoring.** The three formats here are the common ones; a league
with 6-point passing touchdowns, tight-end premium, or return yardage will rank
players differently. `passing_tds` and the rest of the volume columns are in the
file precisely so you can re-score from them.

**Who owned whom.** The dashboard's BigQuery snapshot records team scores, not
rosters, so there is no record of which fantasy manager started which player in
2025. Attribution here is to NFL teams only.
