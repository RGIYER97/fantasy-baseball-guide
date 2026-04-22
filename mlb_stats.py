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


def _get_schedule(start_date: datetime.date, end_date: datetime.date) -> list:
    r = requests.get(
        f'{_MLB_BASE}/schedule',
        params={
            'sportId': '1',
            'startDate': start_date.isoformat(),
            'endDate': end_date.isoformat(),
            'gameType': 'R',
            # 'teams' hydration ensures abbreviation/teamCode fields are populated
            'hydrate': 'probablePitcher,team',
        },
        headers=_HEADERS,
        timeout=15,
    )
    r.raise_for_status()
    return r.json().get('dates', [])


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

                team_obj = side_data.get('team', {})
                # 'abbreviation' requires team hydration; fall back to
                # 'teamCode' (always present) then 'clubName' first 3 chars
                abbr = (
                    team_obj.get('abbreviation')
                    or team_obj.get('teamCode')
                    or team_obj.get('clubName', '')[:3]
                ).upper().strip()
                if abbr:
                    games[abbr] = games.get(abbr, 0) + 1

                pp = side_data.get('probablePitcher')
                if pp:
                    nn = normalize_name(pp.get('fullName', ''))
                    if nn:
                        starts[nn] = starts.get(nn, 0) + 1

    return starts, games
