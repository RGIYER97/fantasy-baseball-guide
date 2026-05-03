"""
MLB Stats API — schedule context for the current scoring period.
Provides starts remaining per pitcher and games remaining per team.
Free endpoint, no authentication required.

A pitcher absent from starts_remaining has unknown scheduling (probable
not yet announced), not zero starts — treat as -1, not 0.
"""

from __future__ import annotations

import datetime
import requests
from typing import Dict, Tuple

from pybaseball_stats import normalize_name

_MLB_BASE = 'https://statsapi.mlb.com/api/v1'
_HEADERS = {'User-Agent': 'fantasy-baseball-helper/1.0'}


def _get_schedule(
    start_date: datetime.date,
    end_date: datetime.date,
    extra_hydrate: str = '',
) -> list:
    hydrate = 'probablePitcher,team'
    if extra_hydrate:
        hydrate = f'{hydrate},{extra_hydrate}'
    r = requests.get(
        f'{_MLB_BASE}/schedule',
        params={
            'sportId': '1',
            'startDate': start_date.isoformat(),
            'endDate': end_date.isoformat(),
            'gameType': 'R',
            # 'teams' hydration ensures abbreviation/teamCode fields are populated
            'hydrate': hydrate,
        },
        headers=_HEADERS,
        timeout=15,
    )
    r.raise_for_status()
    return r.json().get('dates', [])


# MLB StatsAPI abbreviations that differ from what ESPN uses.
_MLB_TO_ESPN_ABBR = {
    'ATH': 'OAK',   # Athletics (Sacramento era)
    'AZ':  'ARI',   # Arizona Diamondbacks
    'CWS': 'CHW',   # Chicago White Sox
}


def _team_abbr(team_obj: dict) -> str:
    raw = (
        team_obj.get('abbreviation')
        or team_obj.get('teamCode')
        or team_obj.get('clubName', '')[:3]
    ).upper().strip()
    return _MLB_TO_ESPN_ABBR.get(raw, raw)


def get_schedule_context(
    start_date: datetime.date,
    end_date: datetime.date,
) -> Tuple[Dict[str, int], Dict[str, int]]:
    """Return (starts_remaining, team_games_remaining) for the date range.

    starts_remaining     : {normalized_pitcher_name: start_count}
    team_games_remaining : {team_abbreviation: game_count}
    """
    dates = _get_schedule(start_date, end_date)
    starts: Dict[str, int] = {}
    games: Dict[str, int] = {}

    for date_block in dates:
        for game in date_block.get('games', []):
            teams_data = game.get('teams', {})
            for side in ('home', 'away'):
                side_data = teams_data.get(side, {})

                abbr = _team_abbr(side_data.get('team', {}))
                if abbr:
                    games[abbr] = games.get(abbr, 0) + 1

                pp = side_data.get('probablePitcher')
                if pp:
                    nn = normalize_name(pp.get('fullName', ''))
                    if nn:
                        starts[nn] = starts.get(nn, 0) + 1

    return starts, games


def get_daily_matchups(date: datetime.date) -> Dict[str, dict]:
    """Return per-team matchup info for a single calendar date.

    Result keyed by team abbreviation (the team whose POV the entry describes):
      {
        'opponent':     opp_team_abbr,
        'opp_pitcher':  full name of opposing probable starter (or ''),
        'venue':        ballpark name (e.g. 'Yankee Stadium'),
        'is_home':      True if this team is the home team,
        'game_time':    ISO timestamp of game start (UTC),
      }

    Pitcher handedness is looked up separately via get_player_handedness(); we
    keep the daily fetch lightweight so it caches well within a 1-hour TTL.
    """
    matchups: Dict[str, dict] = {}
    for date_block in _get_schedule(date, date, extra_hydrate='venue'):
        for game in date_block.get('games', []):
            teams_data = game.get('teams', {})
            venue = (game.get('venue') or {}).get('name', '')
            game_time = game.get('gameDate', '')

            for side in ('home', 'away'):
                opp_side = 'away' if side == 'home' else 'home'
                side_data = teams_data.get(side, {})
                opp_data = teams_data.get(opp_side, {})

                abbr = _team_abbr(side_data.get('team', {}))
                if not abbr:
                    continue
                opp_abbr = _team_abbr(opp_data.get('team', {}))

                opp_pp = opp_data.get('probablePitcher') or {}
                opp_pp_name = opp_pp.get('fullName', '')

                matchups[abbr] = {
                    'opponent': opp_abbr,
                    'opp_pitcher': opp_pp_name,
                    'venue': venue,
                    'is_home': side == 'home',
                    'game_time': game_time,
                }
    return matchups


def get_player_handedness(year: int) -> Dict[str, Dict[str, str]]:
    """Return {normalized_name: {'bats': 'L'/'R'/'S', 'throws': 'L'/'R'}} for
    every active MLB player in *year*.

    One API call (~1500 players); caches well for a full day. Used to map
    fantasy roster names to bat-side and to look up an opposing probable
    pitcher's throwing hand.
    """
    r = requests.get(
        f'{_MLB_BASE}/sports/1/players',
        params={'season': str(year)},
        headers=_HEADERS,
        timeout=30,
    )
    r.raise_for_status()
    out: Dict[str, Dict[str, str]] = {}
    for p in r.json().get('people', []):
        nn = normalize_name(p.get('fullName', ''))
        if not nn:
            continue
        out[nn] = {
            'bats':   (p.get('batSide')  or {}).get('code', ''),
            'throws': (p.get('pitchHand') or {}).get('code', ''),
        }
    return out
