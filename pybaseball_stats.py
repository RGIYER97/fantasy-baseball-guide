"""
FanGraphs hitting/pitching stats loaded through pybaseball's fg_batting_data /
fg_pitching_data (Fangraphs leader pages for a single MLB season).

These are cumulative season statistics for that year (not separate Steamer tables).
Values are mapped onto ESPN fantasy stat IDs so league scoring weights apply.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Mapping, Optional, Tuple

import pandas as pd
from pybaseball import fg_batting_data, fg_pitching_data


def normalize_name(name: str) -> str:
    if not name or not isinstance(name, str):
        return ''
    s = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
    s = s.lower().strip()
    s = re.sub(r'[\s]+', ' ', s)
    for suf in (' jr.', ' sr.', ' jr', ' sr', ' ii', ' iii', ' iv', ' v'):
        if s.endswith(suf):
            s = s[:-len(suf)].strip()
    return s


def _cell(row: pd.Series, *candidates: str) -> Optional[float]:
    for c in candidates:
        if c in row.index:
            v = row[c]
            if pd.isna(v):
                continue
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def _row_to_batter_espn(row: pd.Series) -> Dict[str, float]:
    """Map a FanGraphs batting leader row to ESPN STATS_MAP-style keys."""
    p: Dict[str, float] = {}
    for espn, keys in (
        ('AB', ('AB',)),
        ('H', ('H', 'Hits')),
        ('1B', ('1B', 'Singles')),
        ('2B', ('2B', 'Doubles')),
        ('3B', ('3B', 'Triples')),
        ('HR', ('HR', 'Home Runs')),
        ('R', ('R', 'Runs')),
        ('RBI', ('RBI',)),
        ('SB', ('SB', 'Stolen Bases')),
        ('CS', ('CS', 'Caught Stealing')),
        ('B_BB', ('BB', 'Walks', 'B_BB')),
        ('B_IBB', ('IBB', 'Intentional Walks')),
        ('HBP', ('HBP', 'Hit By Pitch')),
        ('SF', ('SF', 'Sacrifice Flies')),
        ('SH', ('SH', 'Sacrifice Hits')),
        ('GDP', ('GDP', 'Grounded Into Double Play')),
        ('B_SO', ('SO', 'Strike Outs', 'K')),
        ('PA', ('PA', 'Plate Appearances')),
        ('AVG', ('AVG', 'Batting Average')),
        ('OBP', ('OBP', 'On-Base Percentage')),
        ('SLG', ('SLG', 'Slugging')),
        ('OPS', ('OPS', 'On-Base Plus Slugging')),
        ('G', ('G', 'Games')),
        ('TB', ('TB', 'Total Bases')),
    ):
        v = _cell(row, *keys)
        if v is not None:
            p[espn] = v
    if 'XBH' not in p:
        x = 0.0
        for k in ('2B', '3B', 'HR'):
            if k in p:
                x += p[k]
        if x > 0:
            p['XBH'] = x
    if 'SB-CS' not in p and 'SB' in p:
        cs = p.get('CS', 0) or 0
        p['SB-CS'] = p['SB'] - cs
    return p


def _row_to_pitcher_espn(row: pd.Series) -> Dict[str, float]:
    p: Dict[str, float] = {}
    for espn, keys in (
        ('W', ('W', 'Wins')),
        ('L', ('L', 'Losses')),
        ('ERA', ('ERA',)),
        ('SV', ('SV', 'Saves')),
        ('GS', ('GS',)),
        ('G', ('G', 'Games')),
        ('CG', ('CG', 'Complete Games')),
        ('K', ('SO', 'Strike Outs')),
        ('P_BB', ('BB', 'Walks')),
        ('P_H', ('H', 'Hits')),
        ('P_HR', ('HR', 'Home Runs')),
        ('P_R', ('R', 'Runs')),
        ('ER', ('ER', 'Earned Runs')),
        ('WHIP', ('WHIP',)),
        ('K/9', ('K/9', 'K_9')),
        ('K/BB', ('K/BB', 'K_BB')),
        ('WP', ('WP', 'Wild Pitches')),
        ('HLD', ('HLD', 'Holds')),
        ('TBF', ('TBF',)),
        ('OBA', ('AVG', 'BAA', 'Opp AVG')),
    ):
        v = _cell(row, *keys)
        if v is not None:
            p[espn] = v
    ip = _cell(row, 'IP', 'Innings Pitched')
    if ip is not None:
        p['IP'] = ip
    if 'SVHD' not in p and ('SV' in p or 'HLD' in p):
        p['SVHD'] = p.get('SV', 0) + p.get('HLD', 0)
    return p


def _team_code(raw: Any) -> str:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return ''
    s = str(raw).strip().upper()
    if len(s) >= 3:
        return s[:3]
    return s


def build_fangraphs_lookups(year: int) -> Tuple[Dict[Tuple[str, str], Dict[str, float]], Dict[str, List[Tuple[str, Dict[str, float]]]],
                            Dict[Tuple[str, str], Dict[str, float]], Dict[str, List[Tuple[str, Dict[str, float]]]]]:
    """Return (bat_primary, bat_by_name, pit_primary, pit_by_name)."""
    bat = fg_batting_data(start_season=year, end_season=year, league='all', qual=1)
    pit = fg_pitching_data(start_season=year, end_season=year, league='all', qual=1)

    if 'Name' not in bat.columns:
        raise ValueError('FanGraphs batting table missing Name column')
    if 'Name' not in pit.columns:
        raise ValueError('FanGraphs pitching table missing Name column')

    bat_primary: Dict[Tuple[str, str], Dict[str, float]] = {}
    bat_by_name: Dict[str, List[Tuple[str, Dict[str, float]]]] = {}
    for _, row in bat.iterrows():
        nn = normalize_name(str(row['Name']))
        team = _team_code(row.get('Team'))
        proj = _row_to_batter_espn(row)
        if not nn:
            continue
        bat_primary[(nn, team)] = proj
        bat_by_name.setdefault(nn, []).append((team, proj))

    pit_primary: Dict[Tuple[str, str], Dict[str, float]] = {}
    pit_by_name: Dict[str, List[Tuple[str, Dict[str, float]]]] = {}
    for _, row in pit.iterrows():
        nn = normalize_name(str(row['Name']))
        team = _team_code(row.get('Team'))
        proj = _row_to_pitcher_espn(row)
        if not nn:
            continue
        pit_primary[(nn, team)] = proj
        pit_by_name.setdefault(nn, []).append((team, proj))

    return bat_primary, bat_by_name, pit_primary, pit_by_name


def lookup_batter(
    espn_name: str,
    espn_team: Any,
    primary: Mapping[Tuple[str, str], Dict[str, float]],
    by_name: Mapping[str, List[Tuple[str, Dict[str, float]]]],
) -> Optional[Dict[str, float]]:
    nn = normalize_name(espn_name)
    if not nn:
        return None
    team = _team_code(espn_team)
    if (nn, team) in primary:
        return dict(primary[(nn, team)])
    cands = by_name.get(nn, [])
    if len(cands) == 1:
        return dict(cands[0][1])
    for t, proj in cands:
        if t == team:
            return dict(proj)
    return None


def lookup_pitcher(
    espn_name: str,
    espn_team: Any,
    primary: Mapping[Tuple[str, str], Dict[str, float]],
    by_name: Mapping[str, List[Tuple[str, Dict[str, float]]]],
) -> Optional[Dict[str, float]]:
    return lookup_batter(espn_name, espn_team, primary, by_name)
