# Fantasy Baseball — H2H Categories Helper

A command-line tool that connects to your ESPN Fantasy Baseball league and recommends roster moves for **Head-to-Head Each Category** leagues.

## What it does

1. **Action summary (TL;DR)** — the first section printed. Shows the current matchup score + projected end-of-week outcome on one line, which categories you're losing and which are flippable, the top pickups recommended by 2+ data sources, and any sell-high alerts for roster players overperforming their expected stats.
2. **Matchup dashboard** — full per-category breakdown vs. your opponent: current stats, margin, and WIN/LOSS/TIE for each category.
3. **Opponent scouting** — your opponent's full roster with season projections so you can see where their category contributions are coming from.
4. **Projected week-end outcome** — estimates end-of-week category totals for both rosters using per-game (hitters) and per-start (SP) rates multiplied by games/starts remaining in the period. Shows current stat, projected remaining, and projected final for each counting category side by side, with a WIN/LOSS/PUSH for each and a matchup record projection. Rate stats (AVG, ERA, WHIP, etc.) are shown at current value only.
5. **Weekly recommendations** — ranks **ESPN waiver/free agents** by how well they address the categories you are *currently losing*, with tighter margins weighted more heavily. A pairing is shown only if the projected **add minus drop** is a **net positive** in those losing categories; otherwise the pickup is omitted. Sections with no recommendations are suppressed entirely.
6. **Season recommendations** — ranks the same free-agent pool across **all** league batting or pitching categories using the same **net-positive swap** rule.
7. **FanGraphs via pybaseball** — optional ranking using **FanGraphs leader stats** for the chosen MLB season, mapped to ESPN stat names and blended with your league's ESPN scoring weights.
8. **FanGraphs ROS projections** — optional rankings from the public FanGraphs projections API (**Steamer**, **ZiPS DC**, **THE BAT X**, or **FanGraphs Depth Charts**).
9. **Overlap / consensus** — players who land in the top of **both** ESPN and FanGraphs rankings, and (in full **all** mode) ESPN plus your chosen ROS system. Only shown when there are actual overlapping results.
10. **Drop candidates** — roster players with the lowest ESPN projected composite value (bench/IL first), with **position scarcity** warnings.
11. **Full default run (`--source all`)** — additionally loads **MLB schedule context** (probable starts and team games left in the scoring period), **Baseball Savant** xStats for signal-style tables, **last-14-days** FanGraphs splits for a short-term weekly lens, and **plate discipline** (SwStr%, Contact%) for free agents.

### Action summary

The first section of output is always a compact summary:

```
  vs. Rival Team:  4–5–1  (LOSING)  →  5–5–0 (projected TIE)
  Losing:    R, ERA, WHIP, OBP
  Flippable: SB-CS

  Top pickups (2+ sources agree):
    +  Grant Taylor            ChW    [ESPN + Steamer]  → drop Carlos Rodon
    +  Matt Brash              Sea    [FG + L14 + Steamer]  → drop Clay Holmes

  Sell-high alerts (roster players overperforming expected stats):
    ~  Tanner Scott            LAD    ERA 2.38 → xERA 4.60
    ~  Clay Holmes             NYM    ERA 2.10 → xERA 4.21
```

Cross-source consensus aggregates the top-10 from each weekly source (ESPN, FanGraphs YTD, Steamer ROS, L14 recent form) and surfaces players recommended by 2 or more.

### Opponent scouting and projected outcome

The opponent roster section mirrors your own roster display — Slot, Player, Eligible positions, Team, and up to 7 stat projections per side. It is shown for your active matchup opponent automatically.

The projected outcome section breaks down every scoring category as:

```
Cat    You (cur)  +Proj  =Total   Opp (cur)  +Proj  =Total   Projected
-----  ---------  -----  -------  ---------  -----  -------  ---------
R      15         +8     23       12         +6     18       WIN
HR     5          +3     8        4          +4     8        PUSH
...
```

Counting stat projections use season-projection per-game rates × games remaining for hitters and per-start rates × confirmed starts remaining for SPs. RPs use per-appearance rates × team games remaining. Players on the 60-day IL or injured reserve are excluded from projections.

### Scout any team with `--scout`

```bash
python main.py --scout "Rival Team"   # exact or partial name match
```

Shows the scouted team's roster with projections before the matchup sections. Useful for evaluating trade targets, previewing a future opponent, or checking on a strong division rival.

### Roster rules & league lineup settings

- **Lineup slots** come from ESPN (`rosterSettings.lineupSlotCounts`), not inferred from your current roster.
- **Add/drop feasibility** uses bipartite matching: every **starting** slot must still be fillable after a proposed swap.
- The **your roster** table includes an **Eligible** column and warns on **thin positions** (only one roster player eligible for a slot).

### Projection basis

| Section | ESPN stats used |
|--------|------------------|
| **Weekly** | Prefers **current matchup period** projected stats when ESPN returns them (`filterStatsForCurrentMatchupPeriod`); otherwise falls back to full-season projections. |
| **Season** | Full-season ESPN projections (`scoringPeriod` 0). |

FanGraphs **leader** rows are season-to-date stats. **ROS projection** tables use rest-of-season models from FanGraphs.

### Add/drop pairings, unique drops, and move plan

- Each pickup gets its own **feasible** drop: weakest-value roster players preferred, scarce-position players deprioritized. Same-position replacement preferred; UTIL paths are tagged.
- **Net-positive swap filter:** projected category deltas (add − drop) must be positive on balance over the target categories. Inverse stats (ERA, WHIP) flip sign. If the weighted total is ≤ 0, the row is removed.
- **No duplicate drops** across the combined hitter + pitcher lists. After each table, a **Recommended roster moves** block lists every Drop → Add pair.

### Injury filtering

Waiver targets with any non-`ACTIVE` ESPN `injuryStatus` (OUT, DAY_TO_DAY, IL designations, SUSPENSION) are **excluded** from recommendations.

### Savant signal quality

Buy-low pitcher signals require ERA ≤ 15. Pitchers above that threshold are small-sample artifacts (e.g. 1 blown outing early in the season) and are filtered regardless of how large the ERA–xERA gap appears.

### Disk cache

External API calls (FanGraphs, Savant, MLB schedule) are cached to `~/.cache/fantasy-baseball-helper/` to speed up repeated runs during the same session:

| Source | TTL |
|--------|-----|
| MLB schedule context | 2 hours |
| FanGraphs YTD / Savant / recent splits / plate discipline | 4 hours |
| ROS projections (Steamer, ZiPS, etc.) | 8 hours |

Pass `--no-cache` to bypass the cache and fetch all data fresh.

## Dependencies

| Package | Purpose |
|---------|---------|
| espn_api | ESPN Fantasy Baseball API |
| pybaseball | FanGraphs leader data |
| pandas | Data handling |
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
# default: --source all → ESPN + FanGraphs YTD + ROS projections + overlaps + Savant/L14/discipline

python main.py --source espn          # ESPN projections only
python main.py --source fangraphs     # FanGraphs YTD leaders only
python main.py --source steamer       # ROS projections only (system via --proj-system)
python main.py --source both          # ESPN ∩ FanGraphs overlap tables only

python main.py --proj-system steamer  # default; alternatives: zips, thebat, depthcharts
python main.py --no-savant            # skip Savant xStats only (schedule/L14/discipline still run)
python main.py --scout "Team Name"    # scout any roster by name (partial match)
python main.py --no-cache             # bypass disk cache, fetch all external data fresh
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
| `cache.py` | Disk cache with TTL for slow external API calls |
| `league_client.py` | ESPN league, categories, stat weights, matchups, roster slots, free agents |
| `roster.py` | League slot list, eligibility display, bipartite matching for lineup feasibility |
| `pybaseball_stats.py` | FanGraphs via JSON API; recent L14 splits; plate discipline lookups; name normalization |
| `projections_stats.py` | FanGraphs ROS projections API (Steamer, ZiPS DC, THE BAT X, Depth Charts) |
| `savant_stats.py` | Baseball Savant xStats lookups and signal summaries (ERA ≤ 15 gate on buy-low signals) |
| `mlb_stats.py` | MLB schedule context (starts / games remaining in period) |
| `recommender.py` | Rankings, consensus, add/drop pairing, net-positive swap filter, projected week-end outcome |
| `main.py` | CLI, all display functions |
| `requirements.txt` | Dependencies |

## How scoring uses your league settings

- Category lists and **points per stat** come from ESPN `scoringSettings.scoringItems`. If the API reports `0` points (typical for pure category leagues), each category is weighted **1.0**.
- The same weights multiply each category's normalized contribution in the ranker, for both ESPN and FanGraphs-based rankings.
- The **swap filter** uses the same per-category weights when summing projected **add − drop** deltas, so a category your league scores heavily counts more toward keeping or dropping a recommendation.
- Weekly loss margins tighten the matchup weight (closer categories matter more) in the **ranking** step only; the swap filter does not use matchup margins.
