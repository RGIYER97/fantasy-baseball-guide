# Fantasy Baseball — H2H Categories Helper

A command-line tool that connects to your ESPN Fantasy Baseball league and recommends roster moves for **Head-to-Head Each Category** leagues.

## What it does

1. **Matchup dashboard** — shows your current weekly H2H category scores vs. your opponent, flags categories that are close or flippable.
2. **Weekly recommendations** — ranks **ESPN waiver/free agents** by how well they address the categories you are *currently losing*, with tighter margins weighted more heavily.
3. **Season recommendations** — ranks the same free-agent pool across all league scoring categories.
4. **FanGraphs via pybaseball** — optional second ranking using **FanGraphs leader stats** (`fg_batting_data` / `fg_pitching_data`) for the chosen MLB season, mapped to ESPN stat names. Only players who appear in your ESPN free-agent list are scored; stats are blended with your league’s **ESPN scoring weights** (`scoringItems` points, defaulting to 1 per category when points are zero).
5. **Overlap** — players who land in the top of **both** ESPN and FanGraphs rankings.
6. **Drop candidates** — roster players with the lowest ESPN projected composite value (bench/IL first).

### Add/drop pairings and category impact

Pickup tables show **who to drop** to make a one-for-one swap work: for hitter recommendations, the weakest hitter on your roster (bench/IL prioritized); for pitchers, the weakest pitcher. A **`swap Δ`** row under each pickup lists the **projected change per scoring category** if you add that player and drop the suggested player (add projection minus drop projection, using the same stat source as that table—ESPN projections or FanGraphs season stats).

Weekly and season sections use the same underlying projection rows; interpret “this week” vs “rest of season” from the section title, not from different stat feeds unless you run ESPN vs FanGraphs sources separately.

### Injury filtering

**Waiver targets** with any non-`ACTIVE` ESPN `injuryStatus` (e.g. OUT, DAY_TO_DAY, IL designations, SUSPENSION) are **excluded** from recommendations so injured free agents are not suggested as adds.

**Note:** pybaseball pulls **season-to-date (or full-season) FanGraphs leader boards** for the year you set—not a separate Steamer/ZiPS projection export. Early in a season, FG numbers may be based on small samples.

## Dependencies

| Package | Purpose |
|---------|---------|
| espn_api | ESPN Fantasy Baseball API |
| pybaseball | FanGraphs leader data (`fg_batting_data`, `fg_pitching_data`) |
| pandas | Used by pybaseball / tables |
| tabulate | Terminal tables |

```bash
pip install espn_api pybaseball pandas tabulate
```

## Setup

1. Copy `config_example.py` to `config_private.py` and fill in `LEAGUE_ID`, `YEAR`, `ESPN_S2`, `SWID`, `TEAM_NAME`.
2. Optionally set `FANGRAPHS_SEASON` in `config_private.py` if the MLB year for FanGraphs should differ from `YEAR`.

## Run

```bash
python main.py                  # ESPN + FanGraphs + overlap (default)
python main.py --source espn    # ESPN projections only
python main.py --source fangraphs
python main.py --source both   # overlap only (top of both lists)
```

## File overview

| File | Purpose |
|------|---------|
| `config_private.py` | Credentials (git-ignored) |
| `league_client.py` | ESPN league, categories, stat weights, matchups |
| `pybaseball_stats.py` | Load FG data via pybaseball; map rows → ESPN stat keys; name lookup |
| `recommender.py` | Rankings, consensus, add/drop deltas, injury filter for FAs |
| `main.py` | CLI |
| `requirements.txt` | Dependencies |

## How scoring uses your league settings

- Category lists and **points per stat** come from ESPN `scoringSettings.scoringItems`. If the API reports `0` points (typical for pure category leagues), each category is weighted **1.0**.
- The same weights multiply each category’s normalized contribution in the ranker, for both ESPN and FanGraphs-based rankings.
- Weekly loss margins still tighten the matchup weight (closer categories matter more).
