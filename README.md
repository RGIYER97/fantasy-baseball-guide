# Fantasy Baseball — H2H Categories Helper

A command-line tool that connects to your ESPN Fantasy Baseball league and recommends roster moves for **Head-to-Head Each Category** leagues.

## What it does

1. **Matchup dashboard** — shows your current weekly H2H category scores vs. your opponent, flags categories that are close or flippable.
2. **Weekly recommendations** — ranks **ESPN waiver/free agents** by how well they address the categories you are *currently losing*, with tighter margins weighted more heavily. A pairing is shown only if the projected **add minus drop** is a **net positive** in those losing categories (weighted like the ranker); otherwise the pickup is omitted so you are not told to trade down in the stats that matter this week.
3. **Season recommendations** — ranks the same free-agent pool across **all** league batting or pitching categories. Pairings use the same **net-positive swap** rule: if the free agent is projected worse than the suggested drop across those categories (again using league stat weights), that recommendation is dropped.
4. **FanGraphs via pybaseball** — optional ranking using **FanGraphs leader stats** (`fg_batting_data` / `fg_pitching_data`) for the chosen MLB season, mapped to ESPN stat names. Only players who appear in your ESPN free-agent list are scored; stats are blended with your league’s **ESPN scoring weights** (`scoringItems` points, defaulting to 1 per category when points are zero).
5. **FanGraphs ROS projections** — optional rankings from the public FanGraphs projections API (**Steamer**, **ZiPS DC**, **THE BAT X**, or **FanGraphs Depth Charts**). Same free-agent pool and ESPN weighting as other non-ESPN tables.
6. **Overlap / consensus** — players who land in the top of **both** ESPN and FanGraphs rankings, and (in full **all** mode) ESPN plus your chosen ROS system.
7. **Drop candidates** — roster players with the lowest ESPN projected composite value (bench/IL first), with **position scarcity** warnings for players who are the only one on the roster eligible for a starting slot.
8. **Full default run (`--source all`)** — additionally loads **MLB schedule context** (probable starts and team games left in the scoring period), **Baseball Savant** xStats for signal-style tables, **last-14-days** FanGraphs splits for a short-term weekly lens, and **plate discipline** (e.g. SwStr%, Contact%) for free agents. Use **`--no-savant`** to skip those network-heavy blocks (Savant, L14, discipline, and schedule fetches).

### Roster rules & league lineup settings

- **Lineup slots** come from ESPN (`rosterSettings.lineupSlotCounts`), not from guessing from your current roster. That matches real league requirements (e.g. how many OF, SP, RP slots).
- **Add/drop feasibility** uses bipartite matching: every **starting** slot must still be fillable after a proposed swap. You won’t get “drop your only catcher” for an outfield-only pickup unless another pickup/player covers catcher eligibility.
- The **your roster** table includes an **Eligible** column (ESPN `eligibleSlots`) and warns on **thin positions** (only one roster player eligible for a slot).

### Projection basis

| Section | ESPN stats used |
|--------|------------------|
| **Weekly** | Prefers **current matchup period** projected stats when ESPN returns them (`filterStatsForCurrentMatchupPeriod`); otherwise falls back to **full-season** projections. The output labels the active basis (`weekly (matchup period)` vs `full-season`). |
| **Season** | Full-season ESPN projections (`scoringPeriod` 0). |

FanGraphs **leader** rows are **season-to-date (or full-season) leader stats** for the year you set—not weekly matchup math. **ROS projection** tables use rest-of-season models from FanGraphs, not ESPN’s weekly period. Early in a season, YTD numbers may be small-sample.

### Add/drop pairings, unique drops, and move plan

- Each pickup gets its own **feasible** drop: weakest-value roster players are preferred, with scarce-position players sorted later. **Same-position** replacement is preferred; if only a **UTIL** path works, the UI tags that (`[add for UTIL]` / `[for UTIL]`).
- **Net-positive swap filter:** after a drop is chosen, projected category deltas (add − drop) are scored only over the categories that drive that recommendation (**categories you are losing** for weekly; **all** batting or pitching categories for season). Inverse counting stats (e.g. ERA, WHIP — lower is better) flip the sign so “better” counts as positive. If the weighted total is ≤ 0, the row is removed — you will not get a move that is projected to hurt you in those categories on balance.
- **No duplicate drops** across the combined hitter + pitcher recommendation lists—each roster player is suggested as a drop at most once. Drop candidates can be any roster player (hit or pitch) so a bench arm can pair with a bat add when that’s the right move.
- After each weekly/season table, a **Recommended roster moves** block lists every **Drop → Add** pair and a total move count. If every candidate fails the net-positive check, tables and move lists can be empty even when waiver wire players look decent in isolation.
- Pickup rows still show a **`swap Δ`** line: projected change per category (add minus drop), using the same stat source as that table (ESPN, FanGraphs YTD, or ROS projections).

### Injury filtering

**Waiver targets** with any non-`ACTIVE` ESPN `injuryStatus` (e.g. OUT, DAY_TO_DAY, IL designations, SUSPENSION) are **excluded** from recommendations so injured free agents are not suggested as adds.

**Note:** pybaseball pulls **season leader boards** for the year you set. Savant and extra FanGraphs calls require network access; failures are logged and non-fatal except when you choose a single source that fails to load.

## Dependencies

| Package | Purpose |
|---------|---------|
| espn_api | ESPN Fantasy Baseball API |
| pybaseball | FanGraphs leader data (`fg_batting_data`, `fg_pitching_data`) |
| pandas | Used by pybaseball / data handling |
| tabulate | Terminal tables |
| requests | FanGraphs projections API; Savant / MLB helper HTTP |

```bash
pip install -r requirements.txt
```

## Setup

1. Use **Python 3.10 or newer** (type hints use `X | Y` unions).
2. Copy `config_example.py` to `config_private.py` and fill in `LEAGUE_ID`, `YEAR`, `ESPN_S2`, `SWID`, `TEAM_NAME`.
3. Optionally set `FANGRAPHS_SEASON` in `config_private.py` if the MLB year for FanGraphs / Savant / schedule helpers should differ from `YEAR`.

## Run

```bash
python main.py
# default: --source all  → ESPN + FanGraphs YTD + ROS projections + overlaps + Savant/L14/discipline (see below)

python main.py --source espn       # ESPN projections only
python main.py --source fangraphs # FanGraphs YTD leaders only
python main.py --source steamer   # ROS projections only (system via --proj-system)
python main.py --source both      # ESPN ∩ FanGraphs overlap tables only (+ roster / matchup / drops)

python main.py --proj-system steamer      # default; alternatives: zips, thebat, depthcharts
python main.py --no-savant                # skip Savant, L14 splits, plate discipline, MLB schedule fetches
```

| `--source` | Behavior |
|------------|----------|
| **all** (default) | ESPN weekly/season tables, FanGraphs YTD, chosen ROS system, ESPN∩FG and ESPN∩ROS consensus, Savant signals, L14 weekly block, plate-discipline table, schedule context. |
| **espn** | ESPN only. |
| **fangraphs** | FanGraphs YTD only (exits if load fails). |
| **steamer** | ROS projections only; `--proj-system` selects model (exits if load fails). |
| **both** | Builds ESPN + FG rankings but prints **only** the overlap (consensus) weekly/season sections, plus roster, matchup, drops. |

## File overview

| File | Purpose |
|------|---------|
| `config_private.py` | Credentials (git-ignored) |
| `config_example.py` | Template for private config |
| `league_client.py` | ESPN league, categories, stat weights, matchups, roster slots, free agents (including weekly projection fetch) |
| `roster.py` | League slot list, eligibility display, bipartite matching for lineup feasibility, unique-drop helpers |
| `pybaseball_stats.py` | FanGraphs via pybaseball; recent L14 splits; plate discipline lookups; name normalization |
| `projections_stats.py` | FanGraphs ROS projections API (Steamer, ZiPS DC, THE BAT X, Depth Charts) |
| `savant_stats.py` | Baseball Savant xStats lookups and signal summaries |
| `mlb_stats.py` | MLB schedule context (starts / games remaining in period) |
| `recommender.py` | Rankings, consensus, coordinated add/drop pairing, injury filter for FAs, net-positive projected swap filter |
| `main.py` | CLI |
| `requirements.txt` | Dependencies |

## How scoring uses your league settings

- Category lists and **points per stat** come from ESPN `scoringSettings.scoringItems`. If the API reports `0` points (typical for pure category leagues), each category is weighted **1.0**.
- The same weights multiply each category’s normalized contribution in the ranker, for both ESPN and FanGraphs-based rankings.
- The **swap filter** uses the same per-category weights when summing projected **add − drop** deltas (only over the relevant target categories), so a category your league scores heavily counts more toward keeping or dropping a recommendation.
- Weekly loss margins still tighten the matchup weight (closer categories matter more) in the **ranking** step only; the swap filter does not use matchup margins—it only asks whether the trade-up is projected positive in the target stats.
