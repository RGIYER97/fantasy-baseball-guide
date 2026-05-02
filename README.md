# Fantasy Baseball - H2H Categories Helper

Command-line assistant for ESPN Head-to-Head Each Category leagues. It connects to your league, analyzes your live matchup, and recommends add/drop moves that are lineup-feasible and category-positive.

## Setup

1. Use Python 3.10+.
2. Install deps:

```bash
pip install -r requirements.txt
```

3. Copy `config_example.py` to `config_private.py` and set:
   - `LEAGUE_ID`
   - `YEAR`
   - `ESPN_S2`
   - `SWID`
   - `TEAM_NAME`
4. Optional: set `FANGRAPHS_SEASON` in `config_private.py` to use a different MLB season for FanGraphs/Savant helpers.

## Run

```bash
python main.py
# default: --source all, minimal output

python main.py --source espn
python main.py --source fangraphs
python main.py --source steamer

python main.py --proj-system steamer   # steamer (default), zips, thebat, depthcharts
python main.py --scout "Team Name"     # exact or partial team-name match
python main.py --no-cache              # bypass disk cache
python main.py --verbose               # show detailed output (rosters, loading messages, plate discipline, Savant signals)
```

`--source` values:
- `all` (default): ESPN + FanGraphs YTD + ROS projections + consensus + schedule context + L14 + plate discipline + Savant signals.
- `espn`: ESPN projections only.
- `fangraphs`: FanGraphs YTD only.
- `steamer`: ROS projections only (projection model selected by `--proj-system`).

## What it outputs

- Your roster table with eligibility and projections.
- Matchup category board (WIN/LOSS/TIE by category).
- Opponent roster and projected week-end outcome.
- Weekly pickup recommendations (category-gap focused).
- Streaming SP queue (when starts/schedule context is available).
- Closer targets (only when saves categories are currently losing).
- Drop candidates with scarcity-aware logic.
- Season-long pickup recommendations.
- Consensus sections across data sources when overlap exists.
- Plate-discipline and Savant signal sections when data loads.
- Final TL;DR summary printed at the end.

## Recommendation logic (high level)

- Add/drop feasibility is enforced using lineup-slot matching.
- Picks are filtered by a weighted net-positive swap rule: `(add - drop)` must be positive over target categories.
- Inverse categories (e.g., ERA/WHIP) are sign-corrected before scoring.
- Weekly mode emphasizes currently losing/close categories.
- Non-active free agents are excluded.

## Cache

External calls are cached in `~/.cache/fantasy-baseball-helper/`.

- MLB schedule context: 2 hours
- FanGraphs YTD / Savant / recent splits / plate discipline: 4 hours
- ROS projections: 8 hours

Use `--no-cache` to fetch everything fresh.

## File overview

- `main.py`: CLI orchestration and output sections
- `league_client.py`: ESPN data access
- `recommender.py`: ranking, filtering, and consensus logic
- `roster.py`: lineup feasibility and drop selection
- `pybaseball_stats.py`: FanGraphs + recent/discipline helpers
- `projections_stats.py`: ROS projection systems
- `savant_stats.py`: Savant signal helpers
- `mlb_stats.py`: schedule context
- `cache.py`: JSON disk cache
- `config_example.py`: template for private settings
- `requirements.txt`: Python dependencies
