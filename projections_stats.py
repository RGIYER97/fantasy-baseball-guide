"""
FanGraphs rest-of-season projection fetcher (Steamer ROS, ZiPS DC, THE BAT X).

Fetches the public FanGraphs projections API — no authentication required.
Returns the same (bat_primary, bat_by_name, pit_primary, pit_by_name) lookup
format as build_fangraphs_lookups() in pybaseball_stats.py, so it plugs
directly into Recommender.split_free_agents_fangraphs() unchanged.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests

from pybaseball_stats import normalize_name, _cell, _team_code

_FG_PROJ_URL = 'https://www.fangraphs.com/api/projections'
_HEADERS = {'User-Agent': 'fantasy-baseball-helper/1.0'}

PROJECTION_SYSTEMS: Dict[str, str] = {
    'steamer':     'steamerr',     # Steamer ROS
    'zips':        'zipsdc',       # ZiPS + depth charts
    'thebat':      'thebatx',      # THE BAT X
    'depthcharts': 'fangraphsdc',  # FanGraphs Depth Charts (Steamer × current PT)
}


def _fetch(fg_type: str, stats: str) -> pd.DataFrame:
    params = {
        'type': fg_type,
        'stats': stats,
        'pos': 'all',
        'team': '0',
        'players': '0',
    }
    r = requests.get(_FG_PROJ_URL, params=params, headers=_HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list) or not data:
        raise ValueError(f'Empty or unexpected response for {fg_type}/{stats}')
    df = pd.DataFrame(data)
    df.columns = [str(c).strip() for c in df.columns]
    return df


def _player_name(row: pd.Series) -> str:
    # Projection endpoint uses PlayerName; leaderboard uses Name
    for col in ('PlayerName', 'Name', 'playerName', 'name'):
        if col in row.index:
            v = row[col]
            if v and not (isinstance(v, float) and pd.isna(v)):
                return str(v)
    return ''


def _row_to_bat(row: pd.Series) -> Dict[str, float]:
    p: Dict[str, float] = {}
    for espn, keys in (
        ('AB',   ('AB',)),
        ('H',    ('H',)),
        ('1B',   ('1B',)),
        ('2B',   ('2B',)),
        ('3B',   ('3B',)),
        ('HR',   ('HR',)),
        ('R',    ('R',)),
        ('RBI',  ('RBI',)),
        ('SB',   ('SB',)),
        ('CS',   ('CS',)),
        ('B_BB', ('BB',)),
        ('HBP',  ('HBP',)),
        ('SF',   ('SF',)),
        ('GDP',  ('GDP',)),
        ('B_SO', ('SO', 'K')),
        ('PA',   ('PA',)),
        ('AVG',  ('AVG',)),
        ('OBP',  ('OBP',)),
        ('SLG',  ('SLG',)),
        ('OPS',  ('OPS',)),
        ('G',    ('G',)),
    ):
        v = _cell(row, *keys)
        if v is not None:
            p[espn] = v

    if 'XBH' not in p:
        xbh = sum(p.get(k, 0.0) for k in ('2B', '3B', 'HR'))
        if xbh:
            p['XBH'] = xbh
    if 'TB' not in p:
        tb = (p.get('1B', 0.0) + 2 * p.get('2B', 0.0)
              + 3 * p.get('3B', 0.0) + 4 * p.get('HR', 0.0))
        if tb:
            p['TB'] = tb
    if 'SB-CS' not in p and 'SB' in p:
        p['SB-CS'] = p['SB'] - p.get('CS', 0.0)
    return p


def _row_to_pit(row: pd.Series) -> Dict[str, float]:
    p: Dict[str, float] = {}
    for espn, keys in (
        ('W',    ('W',)),
        ('L',    ('L',)),
        ('ERA',  ('ERA',)),
        ('SV',   ('SV',)),
        ('HLD',  ('HLD',)),
        ('GS',   ('GS',)),
        ('G',    ('G',)),
        ('CG',   ('CG',)),
        ('QS',   ('QS',)),
        ('K',    ('SO', 'K')),
        ('P_BB', ('BB',)),
        ('P_H',  ('H',)),
        ('P_HR', ('HR',)),
        ('P_R',  ('R',)),
        ('ER',   ('ER',)),
        ('WHIP', ('WHIP',)),
        ('K/9',  ('K/9',)),
        ('K/BB', ('K/BB',)),
        ('WP',   ('WP',)),
        ('TBF',  ('TBF',)),
        ('OBA',  ('OBA', 'AVG', 'BAA')),
    ):
        v = _cell(row, *keys)
        if v is not None:
            p[espn] = v

    ip = _cell(row, 'IP')
    if ip is not None:
        p['IP'] = ip
        full = int(ip)
        frac = round((ip - full) * 10)
        p['OUTS'] = float(full * 3 + frac)

    if 'SVHD' not in p and ('SV' in p or 'HLD' in p):
        p['SVHD'] = p.get('SV', 0.0) + p.get('HLD', 0.0)

    return p


def build_projection_lookups(
    system: str = 'steamer',
) -> Tuple[
    Dict[Tuple[str, str], Dict[str, float]],
    Dict[str, List[Tuple[str, Dict[str, float]]]],
    Dict[Tuple[str, str], Dict[str, float]],
    Dict[str, List[Tuple[str, Dict[str, float]]]],
]:
    """Fetch rest-of-season projections and return ESPN-key lookup dicts.

    *system* is 'steamer', 'zips', or 'thebat'.
    Returns (bat_primary, bat_by_name, pit_primary, pit_by_name) — identical
    structure to build_fangraphs_lookups() so it works with the Recommender
    and consensus logic unchanged.
    """
    fg_type = PROJECTION_SYSTEMS.get(system, system)

    bat_df = _fetch(fg_type, 'bat')
    pit_df = _fetch(fg_type, 'pit')

    bat_primary: Dict[Tuple[str, str], Dict[str, float]] = {}
    bat_by_name: Dict[str, List[Tuple[str, Dict[str, float]]]] = {}
    for _, row in bat_df.iterrows():
        nn = normalize_name(_player_name(row))
        team = _team_code(row.get('Team'))
        proj = _row_to_bat(row)
        if not nn:
            continue
        bat_primary[(nn, team)] = proj
        bat_by_name.setdefault(nn, []).append((team, proj))

    pit_primary: Dict[Tuple[str, str], Dict[str, float]] = {}
    pit_by_name: Dict[str, List[Tuple[str, Dict[str, float]]]] = {}
    for _, row in pit_df.iterrows():
        nn = normalize_name(_player_name(row))
        team = _team_code(row.get('Team'))
        proj = _row_to_pit(row)
        if not nn:
            continue
        pit_primary[(nn, team)] = proj
        pit_by_name.setdefault(nn, []).append((team, proj))

    return bat_primary, bat_by_name, pit_primary, pit_by_name
