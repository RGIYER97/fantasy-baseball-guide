"""
FanGraphs hitting/pitching stats for a single MLB season.

Fetches directly from the FanGraphs JSON API (standard + advanced stat sets
merged) rather than using pybaseball, which hits the retired leaders-legacy.aspx
endpoint and receives 403s.

Values are mapped onto ESPN fantasy stat IDs so league scoring weights apply.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Mapping, Optional, Tuple

import pandas as pd
import requests

_FG_LEADERS_URL = 'https://www.fangraphs.com/api/leaders/major-league/data'
_FG_HEADERS = {'User-Agent': 'fantasy-baseball-helper/1.0'}


def _fetch_fg_leaders(
    stats: str,
    year: int,
    qual: int = 1,
    start_date: str = '',
    end_date: str = '',
    stat_types: tuple = (0, 1),
) -> pd.DataFrame:
    """Fetch FanGraphs major-league leaderboard directly from the JSON API.

    stat_types : FanGraphs type codes to fetch and merge. Default (0, 1)
                 gives standard + advanced stats. Use (6,) for plate
                 discipline. Use a single type for projections.
    start_date / end_date : ISO-format strings (YYYY-MM-DD) to restrict to a
                 date range. When empty the full season is returned.
    """
    base = {
        'pos': 'all', 'stats': stats, 'lg': 'all',
        'qual': str(qual), 'season': str(year), 'season1': str(year),
        'ind': '0', 'team': '0', 'rost': '0', 'age': '0',
        'pageitems': '2000000', 'pagenum': '1',
    }
    if start_date:
        base['startdate'] = start_date
    if end_date:
        base['enddate'] = end_date

    dfs = []
    for t in stat_types:
        r = requests.get(_FG_LEADERS_URL, params={**base, 'type': str(t)},
                         headers=_FG_HEADERS, timeout=30)
        r.raise_for_status()
        raw = r.json()
        rows = raw.get('data', raw) if isinstance(raw, dict) else raw
        if rows:
            df = pd.DataFrame(rows)
            df.columns = [str(c).strip() for c in df.columns]
            dfs.append(df)

    if not dfs:
        raise ValueError(f'Empty FanGraphs response for stats={stats}, year={year}')
    if len(dfs) == 1:
        return dfs[0]

    # Merge all frames on playerid (most reliable shared key)
    result = dfs[0]
    for extra in dfs[1:]:
        key = 'playerid' if ('playerid' in result.columns and 'playerid' in extra.columns) else None
        if key:
            new_cols = [c for c in extra.columns if c not in result.columns]
            if new_cols:
                result = result.merge(extra[[key] + new_cols], on=key, how='left')
    return result


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


def _row_name(row: pd.Series) -> str:
    """Extract player name from a FanGraphs API row in 'First Last' form.

    The leaderboard API (/api/leaders/major-league/data) uses 'Name', which
    can come back in 'Last, First' comma format or as an HTML anchor string.
    The projections API (/api/projections) uses 'PlayerName' in 'First Last'
    format.  We try every known variant and normalise the result.
    """
    for col in ('PlayerName', 'Name', 'playerName', 'name', 'PLAYERNAME'):
        if col not in row.index:
            continue
        v = row[col]
        if not v or (isinstance(v, float) and pd.isna(v)):
            continue
        s = str(v).strip()
        if not s or s.lower() == 'nan':
            continue
        # Skip HTML anchor values — leaderboard sometimes embeds <a href...>
        if s.startswith('<') or 'href' in s:
            continue
        # Convert "Last, First" → "First Last" so it matches ESPN's format
        if ', ' in s:
            parts = s.split(', ', 1)
            s = f'{parts[1].strip()} {parts[0].strip()}'
        return s
    return ''


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
    bat = _fetch_fg_leaders('bat', year, qual=0)
    pit = _fetch_fg_leaders('pit', year, qual=0)

    bat_primary: Dict[Tuple[str, str], Dict[str, float]] = {}
    bat_by_name: Dict[str, List[Tuple[str, Dict[str, float]]]] = {}
    for _, row in bat.iterrows():
        nn = normalize_name(_row_name(row))
        team = _team_code(row.get('Team'))
        proj = _row_to_batter_espn(row)
        if not nn:
            continue
        bat_primary[(nn, team)] = proj
        bat_by_name.setdefault(nn, []).append((team, proj))

    if not bat_primary:
        sample_cols = list(bat.columns[:15])
        sample_names = [str(bat.iloc[i].get('Name') or bat.iloc[i].get('PlayerName') or '')
                        for i in range(min(3, len(bat)))]
        raise ValueError(
            f'FanGraphs batting data empty for year={year}. '
            f'Columns: {sample_cols}. Sample names: {sample_names}'
        )

    pit_primary: Dict[Tuple[str, str], Dict[str, float]] = {}
    pit_by_name: Dict[str, List[Tuple[str, Dict[str, float]]]] = {}
    for _, row in pit.iterrows():
        nn = normalize_name(_row_name(row))
        team = _team_code(row.get('Team'))
        proj = _row_to_pitcher_espn(row)
        if not nn:
            continue
        pit_primary[(nn, team)] = proj
        pit_by_name.setdefault(nn, []).append((team, proj))

    return bat_primary, bat_by_name, pit_primary, pit_by_name


def _fuzzy_name_lookup(
    nn: str,
    by_name: Mapping[str, List[Tuple[str, Dict[str, float]]]],
    threshold: float = 90.0,
) -> Optional[Dict[str, float]]:
    """Fuzzy fallback when exact normalize-and-match fails.

    Uses rapidfuzz WRatio so common differences (punctuation, middle initials,
    apostrophes already stripped by normalize_name) still resolve. Returns None
    when rapidfuzz is not installed or no candidate exceeds the threshold.
    """
    try:
        from rapidfuzz import process, fuzz  # type: ignore
    except ImportError:
        return None
    keys = list(by_name.keys())
    if not keys:
        return None
    result = process.extractOne(nn, keys, scorer=fuzz.WRatio, score_cutoff=threshold)
    if result is None:
        return None
    entries = by_name[result[0]]
    return dict(entries[0][1])


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
    return _fuzzy_name_lookup(nn, by_name)


def lookup_pitcher(
    espn_name: str,
    espn_team: Any,
    primary: Mapping[Tuple[str, str], Dict[str, float]],
    by_name: Mapping[str, List[Tuple[str, Dict[str, float]]]],
) -> Optional[Dict[str, float]]:
    return lookup_batter(espn_name, espn_team, primary, by_name)


# ── Recent splits (L14 by default) ───────────────────────────────────────────

def build_recent_splits_lookups(
    year: int, days: int = 14,
) -> Tuple[Dict[Tuple[str, str], Dict[str, float]],
           Dict[str, List[Tuple[str, Dict[str, float]]]],
           Dict[Tuple[str, str], Dict[str, float]],
           Dict[str, List[Tuple[str, Dict[str, float]]]]]:
    """Return (bat_primary, bat_by_name, pit_primary, pit_by_name) for the last
    *days* days.  Same structure as build_fangraphs_lookups() so it plugs into
    Recommender.split_free_agents_fangraphs() and the ranking engine unchanged.
    """
    import datetime
    end = datetime.date.today()
    start = end - datetime.timedelta(days=days)

    bat = _fetch_fg_leaders('bat', year, qual=0,
                            start_date=start.isoformat(), end_date=end.isoformat())
    pit = _fetch_fg_leaders('pit', year, qual=0,
                            start_date=start.isoformat(), end_date=end.isoformat())

    bat_primary: Dict[Tuple[str, str], Dict[str, float]] = {}
    bat_by_name: Dict[str, List[Tuple[str, Dict[str, float]]]] = {}
    for _, row in bat.iterrows():
        nn = normalize_name(_row_name(row))
        team = _team_code(row.get('Team'))
        proj = _row_to_batter_espn(row)
        if not nn:
            continue
        bat_primary[(nn, team)] = proj
        bat_by_name.setdefault(nn, []).append((team, proj))

    pit_primary: Dict[Tuple[str, str], Dict[str, float]] = {}
    pit_by_name: Dict[str, List[Tuple[str, Dict[str, float]]]] = {}
    for _, row in pit.iterrows():
        nn = normalize_name(_row_name(row))
        team = _team_code(row.get('Team'))
        proj = _row_to_pitcher_espn(row)
        if not nn:
            continue
        pit_primary[(nn, team)] = proj
        pit_by_name.setdefault(nn, []).append((team, proj))

    return bat_primary, bat_by_name, pit_primary, pit_by_name


# ── Plate discipline (SwStr%, Contact%, etc.) ────────────────────────────────

def _to_pct(val) -> Optional[float]:
    """Normalise FanGraphs rate to a percentage value (0–100 scale)."""
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    # FG API returns rates as 0–1 decimals
    return round(f * 100, 1) if f <= 1.0 else round(f, 1)


def build_plate_discipline_lookups(
    year: int,
) -> Tuple[Dict[str, Dict[str, Optional[float]]], Dict[str, Dict[str, Optional[float]]]]:
    """Return (bat_disc, pit_disc) keyed by normalized player name.

    bat_disc values : SwStr%, Contact%, O-Swing%, Zone%
    pit_disc values : SwStr%, Zone%, F-Strike%, CSW%
    """
    bat_df = _fetch_fg_leaders('bat', year, qual=0, stat_types=(6,))
    pit_df = _fetch_fg_leaders('pit', year, qual=0, stat_types=(6,))

    bat_disc: Dict[str, Dict[str, Optional[float]]] = {}
    for _, row in bat_df.iterrows():
        nn = normalize_name(_row_name(row))
        if not nn:
            continue
        bat_disc[nn] = {
            'SwStr%':   _to_pct(_cell(row, 'SwStr%', 'Swing-Strike%', 'SwStr', 'swstr')),
            'Contact%': _to_pct(_cell(row, 'Contact%', 'Contact', 'contact_pct')),
            'O-Swing%': _to_pct(_cell(row, 'O-Swing%', 'OSwing%', 'O-Swing', 'o_swing')),
            'Zone%':    _to_pct(_cell(row, 'Zone%', 'Zone', 'zone_pct')),
        }

    pit_disc: Dict[str, Dict[str, Optional[float]]] = {}
    for _, row in pit_df.iterrows():
        nn = normalize_name(_row_name(row))
        if not nn:
            continue
        pit_disc[nn] = {
            'SwStr%':    _to_pct(_cell(row, 'SwStr%', 'Swing-Strike%', 'SwStr', 'swstr')),
            'Zone%':     _to_pct(_cell(row, 'Zone%', 'Zone', 'zone_pct')),
            'F-Strike%': _to_pct(_cell(row, 'F-Strike%', 'FStrike%', 'F-Strike', 'f_strike')),
            'CSW%':      _to_pct(_cell(row, 'CSW%', 'CSW', 'csw_pct')),
        }

    return bat_disc, pit_disc
