# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup

Requires Python 3.10+ (uses `X | Y` union type syntax). Install dependencies:

```bash
pip install -r requirements.txt
```

Copy `config_example.py` to `config_private.py` and fill in `LEAGUE_ID`, `YEAR`, `ESPN_S2`, `SWID`, `TEAM_NAME`. `config_private.py` is gitignored.

## Running

```bash
python main.py                        # ESPN + FanGraphs + overlap (default)
python main.py --source espn          # ESPN projections only
python main.py --source fangraphs
python main.py --source both          # overlap only (top of both lists)
python main.py --no-savant            # skip Savant xStats only (schedule/L14/discipline still run)
python main.py --scout "Team Name"    # scout any roster by name (partial match supported)
python main.py --no-cache             # bypass disk cache, fetch all data fresh
```

## Architecture

The data flow is: `LeagueClient` fetches ESPN data → `Recommender` ranks players and filters pickups → `main.py` formats and prints everything.

**`league_client.py` — ESPN API wrapper**
- `LeagueClient` wraps `espn_api.baseball.League` and exposes scoring categories, stat weights, matchup data, roster slots, and free agents.
- Fetches weekly projections separately (using `filterStatsForCurrentMatchupPeriod`) from season projections (scoringPeriod 0).
- Key constants: `BATTING_STATS`, `PITCHING_STATS`, `RATE_STATS`, `INVERSE_STATS` — these determine how stats are categorized and scored.

**`roster.py` — Lineup feasibility**
- Uses bipartite matching (augmenting-path algorithm) to verify every starting slot can be legally filled after a proposed add/drop.
- `set_league_slots()` must be called once at startup with slots from the ESPN API before feasibility checks run.
- `find_best_drop()` selects the weakest feasible drop for a given add, preferring same-position replacements and deprioritizing scarce-position players.

**`recommender.py` — Rankings and filtering**
- `Recommender` takes categories, matchup, roster, and free agents; produces ranked add suggestions.
- Net-positive swap filter: a pickup is only recommended if `Σ(weight × (add_stat − drop_stat))` > 0 over the relevant target categories. Inverse stats (ERA, WHIP, etc.) have their sign flipped.
- Weekly mode weights losing categories more heavily based on current score margin.
- `INJURED_STATUSES` filters out non-active free agents from all recommendations.
- `consensus_pickups()` finds players appearing in both ESPN and FanGraphs top lists.
- `project_week_end(opp_roster)` estimates projected end-of-week category totals for both rosters using per-game/per-start rates × schedule context. Rate stats (AVG, ERA, WHIP) are shown at current value only; counting stats get a projected-remaining column.

**`pybaseball_stats.py` — FanGraphs data**
- Calls the FanGraphs JSON API directly and maps column names to ESPN stat keys.
- Only players who appear in the ESPN free-agent list are scored, so FG data is filtered to the relevant pool.

**`cache.py` — Disk cache**
- Simple pickle-based cache stored in `~/.cache/fantasy-baseball-helper/`.
- TTLs: schedule context 2h, FanGraphs YTD / Savant / recent splits / plate discipline 4h, ROS projections 8h.
- Bypass with `--no-cache`. Cache key includes year and system name so stale season data never leaks between years.

**`savant_stats.py` — Statcast signals**
- `_PIT_ERA_MAX = 15.0` gates buy-low pitcher signals: pitchers with ERA > 15 are excluded as small-sample artifacts regardless of xERA gap.

**`main.py` — CLI and display**
- `CLOSE_THRESHOLDS` defines per-stat margins for flagging a matchup category as "close" or "flippable."
- `RATE_3DEC` / `RATE_2DEC` control decimal formatting for rate stats in all tables.
- Stat weights from ESPN (`scoringItems`) default to 1.0 per category when ESPN reports 0 points (pure category leagues).
- `--no-savant` skips **only** Savant xStats. Schedule context, recent splits, and plate discipline are independent of that flag.
- `--scout TEAM` shows any team's roster with projections (partial name match); useful for trade targets or upcoming opponents.
- **Output flow:** all recommendations are computed first (analysis, weekly/season recs for all sources, savant signals), then displayed. The first section printed is always `show_summary` — a TL;DR showing match score + projected outcome, losing/flippable categories, cross-source consensus pickups, and sell-high alerts. Sections with no content are suppressed rather than printing empty banners.
- `_cross_source_consensus` aggregates the top-10 of each weekly source and surfaces players appearing in 2+ lists. `_has_recs` guards all `show_weekly`, `show_season`, and `show_consensus_*` calls.
