# CLAUDE.md

Guidance for coding agents working in this repository.

## Setup

- Python 3.10+ required.
- Install dependencies:

```bash
pip install -r requirements.txt
```

- Copy `config_example.py` to `config_private.py` and fill in:
  - `LEAGUE_ID`, `YEAR`, `ESPN_S2`, `SWID`, `TEAM_NAME`
  - Optional: `FANGRAPHS_SEASON`
- `config_private.py` is gitignored.

## Run Commands

```bash
python main.py                        # default: --source all, minimal output
python main.py --source espn          # ESPN projections only
python main.py --source fangraphs     # FanGraphs YTD only
python main.py --source steamer       # ROS projections only
python main.py --proj-system zips     # steamer / zips / thebat / depthcharts
python main.py --scout "Team Name"    # scout any roster by name (partial match supported)
python main.py --no-cache             # bypass disk cache, fetch all data fresh
python main.py --verbose              # show detailed output (rosters, loading messages, plate discipline, Savant signals)
```

## Core Flow

`LeagueClient` fetches ESPN data -> `Recommender` ranks and filters moves -> `main.py` renders output sections.

**`daily_lineup.py` — Today's start/sit recommendations**
- For each non-pitcher on the roster (excluding IL slot), computes a per-day score:
  `base × platoon × recent form × park`. Base = league-weighted per-game value (ROS counting stats / G); rate stats are skipped. Platoon: 1.07 opposite-handed, 0.93 same-handed, 1.0 switch — driven by MLB StatsAPI handedness. Recent form: L14 per-game value ÷ season per-game value, clamped to [0.75, 1.25]. Park factor: static `_PARK_FACTORS_BY_VENUE` LUT (3-year approximate runs index, 1.00 = neutral).
- `assign_lineup()` is **position-respecting**: current starters stay in their assigned slot; bench players can only be promoted into a slot they're eligible for (same-position swap) or into UTIL. No cross-slot starter shuffles — Correa at 3B will not be moved to SS just because the algorithm could optimize the total. Non-UTIL slots are processed scarcer-first so a multi-position bench player (e.g., a 3B/SS/OF utility hitter) lands where they're most needed; ties favor the incumbent. UTIL is filled last from any unassigned player with a game.
- `recommend_daily_lineup()` returns `lineup`, `bench` (had a game but not chosen), `off_days` (no game today), `sps_today` (probable starters), `sps_off` (SPs whose team plays but they aren't the probable), `rps_with_game` (RPs whose team plays — appearance possible), and `rps_off` (RPs whose team is dark — flagged to sit if currently in an active slot).
- RPs are not part of `assign_lineup` since they don't start. `is_pitcher_starting_today()` is used only for SPs/multi-P, matching normalized name against the opposing-team probable in `daily_matchups`.

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
- **Category correlation bonus:** `_CORR_GROUPS` defines sets of stats driven by the same underlying skill (ERA/WHIP/OBA, K/K9/K/BB, AVG/OBP/OPS, SV/SVHD, HLD/SVHD, R/RBI). When a player positively contributes ≥2 stats from the same group, a 15% bonus is applied to that group's combined score per extra correlated category.
- `INJURED_STATUSES` filters out non-active free agents from all recommendations.
- `consensus_pickups()` finds players appearing in both ESPN and FanGraphs top lists.
- `project_week_end(opp_roster)` estimates projected end-of-week category totals for both rosters using per-game/per-start rates × schedule context. Rate stats (AVG, ERA, WHIP) are shown at current value only; counting stats get a projected-remaining column.
- `get_closer_targets(analysis, ytd_pitchers, ros_pitchers)` — fires only when SV/SVHD/HLD is a losing category. Filters FA relievers by SV pace (YTD SV/G ≥ 0.10) or season saves (≥5) or ROS projection (≥2). Blends pace 60% / ROS 40% for ranking. Does not apply the net-positive swap filter (closer adds are always worth evaluating for saves).
- `get_streaming_queue(pitchers)` — FA starters with `starts_remaining ≥ 2` this week, ranked by quality via zero-margin pitching category scoring, then sorted starts-first. Only returns results when MLB schedule context is available.

**`mlb_stats.py` — Schedule + handedness**
- `get_schedule_context(start, end)` — `(starts_remaining, team_games_remaining)` over a date range.
- `get_daily_matchups(date)` — keyed by team abbr, returns `{opponent, opp_pitcher, venue, is_home, game_time}` for one calendar day. Hydrates `probablePitcher,team,venue` on the schedule endpoint.
- `get_player_handedness(year)` — single MLB StatsAPI call to `/sports/1/players?season={year}`; returns `{normalized_name: {bats, throws}}` for every active player. Used for batter platoon and looking up opposing pitcher hand.

**`pybaseball_stats.py` — FanGraphs data**
- Calls the FanGraphs JSON API directly and maps column names to ESPN stat keys.
- Only players who appear in the ESPN free-agent list are scored, so FG data is filtered to the relevant pool.
- `_fuzzy_name_lookup()` provides a `rapidfuzz` WRatio fallback (≥90 score cutoff) when exact normalized-name matching fails. `lookup_pitcher` inherits this via `lookup_batter`.

**`cache.py` — Disk cache**
- JSON-based cache stored in `~/.cache/fantasy-baseball-helper/` (`.json` files).
- Dicts with tuple keys (the `(name, team)` primary lookups) are encoded with a `__tuple_keyed` tag and round-trip cleanly. This replaces the previous pickle-based cache.
- TTLs: schedule context 2h, FanGraphs YTD / Savant / recent splits / plate discipline 4h, ROS projections 8h.
- Bypass with `--no-cache`. Cache key includes year and system name so stale season data never leaks between years.

**`savant_stats.py` — Statcast signals**
- `_PIT_ERA_MAX = 15.0` gates buy-low pitcher signals: pitchers with ERA > 15 are excluded as small-sample artifacts regardless of xERA gap.
- `get_savant_signals()` accepts `starts_remaining` so sell-high pitcher entries include a `starts` field. Pitchers with ≥2 starts remaining are flagged `← hold this week` in the display.
- Uses `rapidfuzz` fuzzy name matching as a fallback for both buy-low and sell-high lookups.

**`main.py` — CLI and display**
- `CLOSE_THRESHOLDS` defines per-stat margins for flagging a matchup category as "close" or "flippable."
- `RATE_3DEC` / `RATE_2DEC` control decimal formatting for rate stats in all tables.
- Stat weights from ESPN (`scoringItems`) default to 1.0 per category when ESPN reports 0 points (pure category leagues).
- `--scout TEAM` shows any team's roster with projections (partial name match); useful for trade targets or upcoming opponents.
- **Output flow:** all recommendations are computed first, then displayed in this order: roster → today's lineup → matchup → opponent roster → projected outcome → weekly recs → streaming queue → closer targets → drop candidates → season recs → consensus → plate discipline → Savant signals → summary. The `show_summary` TL;DR is printed **last** so the conclusion lands after all detail tables. Sections with no content are suppressed rather than printing empty banners.
- `show_daily_lineup` is rendered after `show_roster` and before the matchup section. It prints **Recommended Starters** (sorted by natural slot order C/1B/2B/3B/SS/OF/UTIL with `← from X` indicators when the suggestion differs from current), **Sit / Bench**, **Off Day**, **Pitchers Starting Today**, **SPs not starting today**, and **Relievers — Team Schedule Today** (with `← consider sitting (no game)` when an active-slot RP's team is dark). The whole section is gated on `daily_matchups` being non-empty.
- `_cross_source_consensus` aggregates the top-10 of each weekly source and surfaces players appearing in 2+ lists. `_has_recs` guards all `show_weekly`, `show_season`, and `show_consensus_*` calls.
- `show_streaming_queue` only renders if `rec.get_streaming_queue()` returns results (requires schedule context).
- `show_closer_targets` only renders if losing save categories and qualifying FA relievers exist.

## Editing Notes

- Keep CLI docs aligned with `parse_args()` in `main.py`.
- When adding/removing output sections, update both `README.md` and this file.
- Preserve net-positive swap semantics in recommendation filtering unless intentionally changing ranking behavior.
