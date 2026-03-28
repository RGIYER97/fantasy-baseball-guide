# Fantasy Baseball — H2H Categories Helper

A command-line tool that connects to your ESPN Fantasy Baseball league and recommends roster moves for **Head-to-Head Each Category** leagues.

## What it does

1. **Matchup dashboard** — shows your current weekly H2H category scores vs. your opponent, flags categories that are close or flippable.
2. **Weekly recommendations** — ranks free agents by how well they address the categories you are *currently losing*, weighted by how close each category is.
3. **Season recommendations** — ranks free agents by overall projected value across all scoring categories.
4. **Drop candidates** — lists your rostered players with the lowest projected value (bench/IL first) so you know who to cut.

Projections come from ESPN's own projected stat breakdowns, scored against your league's specific category set.

## Dependencies

| Package | Version | Purpose |
|-----------|---------|---------|
| espn_api | latest | ESPN Fantasy Baseball API |
| pybaseball| latest | FanGraphs data (future enhancement) |
| pandas | latest | Data manipulation |
| tabulate | latest | Pretty terminal tables |

Install all at once:

```bash
pip install espn_api pybaseball pandas tabulate
```

## Setup

1. **Create your private config**

   ```bash
   cp config_example.py config_private.py
   ```

2. **Fill in `config_private.py`** with your league details:

   | Field | How to find it |
   |-------|---------------|
   | `LEAGUE_ID` | From your league URL: `https://fantasy.espn.com/baseball/league?leagueId=XXXXX` |
   | `YEAR` | Current MLB season year (e.g. `2025`) |
   | `ESPN_S2` | Browser → DevTools → Application → Cookies → `espn.com` → `espn_s2` |
   | `SWID` | Same location → `SWID` (include the curly braces) |
   | `TEAM_NAME` | Your fantasy team's display name, exactly as shown on ESPN |

3. **Run the tool**

   ```bash
   python main.py
   ```

## File overview

| File | Purpose |
|------|---------|
| `config_example.py` | Template — copy to `config_private.py` |
| `config_private.py` | **Your credentials (git-ignored)** |
| `league_client.py` | ESPN API wrapper — league data, matchups, free agents |
| `recommender.py` | Analysis engine — scoring, ranking, drop candidates |
| `main.py` | Entry point — CLI display |
| `.gitignore` | Keeps `config_private.py` out of version control |

## How the recommender works

### Weekly strategy

For each H2H category you are **losing**:
- Free agents are ranked by their projected stat in that category, normalised to 0–1 across the free-agent pool.
- Categories where the margin is **closer** get a higher weight (a 1-HR deficit matters more than a 10-HR deficit).
- Hitters are evaluated against batting categories; pitchers against pitching categories.

### Season strategy

Same normalised scoring, but across **all** league categories equally (no margin weighting).

### Drop candidates

Roster players are scored by a composite value across all league categories. Bench and IL players sort first, then ascending by value — the bottom of the list are your best drop candidates.
