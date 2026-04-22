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
python main.py                  # ESPN + FanGraphs + overlap (default)
python main.py --source espn    # ESPN projections only
python main.py --source fangraphs
python main.py --source both    # overlap only (top of both lists)
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
- `consensus_pickups()` in `recommender.py` finds players appearing in both ESPN and FanGraphs top lists.

**`pybaseball_stats.py` — FanGraphs data**
- Calls `pybaseball.fg_batting_data` / `fg_pitching_data` and maps FanGraphs column names to ESPN stat keys.
- Only players who appear in the ESPN free-agent list are scored, so FG data is filtered to the relevant pool.

**`main.py` — CLI and display**
- `CLOSE_THRESHOLDS` defines per-stat margins for flagging a matchup category as "close" or "flippable."
- `RATE_3DEC` / `RATE_2DEC` control decimal formatting for rate stats in all tables.
- Stat weights from ESPN (`scoringItems`) default to 1.0 per category when ESPN reports 0 points (pure category leagues).
