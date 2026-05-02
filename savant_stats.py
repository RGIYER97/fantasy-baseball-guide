"""
Baseball Savant expected statistics (xwOBA, xBA, xSLG, xERA).

Fetches from the public Savant leaderboard CSV endpoint — no auth required.
Used to surface "buy low" free agents (underperforming vs. expected contact
quality) and "sell high" roster players (overperforming vs. expected quality).

Signals
-------
Hitter buy low  : xwOBA > wOBA by threshold → hitting ball harder than results
Pitcher buy low : ERA   > xERA by threshold → unlucky, likely to improve
Hitter sell high: wOBA  > xwOBA by threshold → BABIP-driven, likely to cool off
Pitcher sell high: ERA  < xERA by threshold → outpitching skills, likely to rise
"""

from __future__ import annotations

from io import StringIO
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests

from pybaseball_stats import normalize_name

_SAVANT_URL = 'https://baseballsavant.mlb.com/leaderboard/expected_statistics'

# Savant requires a browser-like UA; bare script strings return HTML instead of CSV
_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Referer': 'https://baseballsavant.mlb.com/',
    'Accept': 'text/csv,text/plain,*/*',
}

_BAT_XWOBA_THRESHOLD = 0.020   # xwOBA − wOBA
_PIT_XERA_THRESHOLD  = 0.50    # ERA − xERA (absolute)
_PIT_ERA_MAX         = 15.0    # above this ERA is a small-sample artifact, not a signal


def _safe_str(val: object) -> str:
    """Return val as a stripped string; empty string for NaN / None / 'nan'."""
    if val is None:
        return ''
    try:
        if pd.isna(val):
            return ''
    except (TypeError, ValueError):
        pass
    s = str(val).strip()
    return '' if s.lower() == 'nan' else s


def _safe_float(val: object) -> float:
    s = _safe_str(val)
    try:
        return float(s)
    except ValueError:
        return 0.0


def _fetch_savant(player_type: str, year: int, min_pa: int = 25) -> pd.DataFrame:
    """Fetch the Savant expected-statistics leaderboard as a DataFrame.

    Tries both csv=1 and csv=true since the accepted form has changed over
    Savant's history.  Raises ValueError with a diagnostic message when the
    response is not valid CSV (e.g. Savant blocked the request and returned
    HTML) or when the CSV contains no data rows.
    """
    min_pa = max(min_pa, 25)
    base_params = {
        'type':     player_type,
        'year':     str(year),
        'position': '',
        'team':     '',
        'min':      str(min_pa),
    }

    last_err: str = ''
    for csv_flag in ('1', 'true'):
        try:
            r = requests.get(
                _SAVANT_URL,
                params={**base_params, 'csv': csv_flag},
                headers=_HEADERS,
                timeout=30,
            )
            r.raise_for_status()
            text = r.text.strip()

            # Detect HTML / JSON error pages
            if not text:
                last_err = f'empty response (csv={csv_flag})'
                continue
            if text.startswith('<') or text.lower().startswith('<!doctype'):
                last_err = f'got HTML instead of CSV (csv={csv_flag}) — Savant may be blocking the request'
                continue
            if text.startswith('{') or text.startswith('['):
                last_err = f'got JSON instead of CSV (csv={csv_flag})'
                continue

            df = pd.read_csv(StringIO(text))
            df.columns = [c.strip() for c in df.columns]

            if df.empty:
                last_err = f'CSV parsed but no data rows (csv={csv_flag})'
                continue

            return df

        except requests.RequestException as exc:
            last_err = str(exc)
            continue

    raise ValueError(
        f'Savant fetch failed for year={year}, type={player_type}: {last_err}'
    )


def _build_name(row: pd.Series) -> str:
    """Extract a 'First Last' player name from a Savant CSV row.

    Tries combined-name columns first, then the standard Savant
    last_name / first_name pair.  All values are NaN-safe via _safe_str.
    """
    for col in ('player_name', 'name', 'Name', 'full_name'):
        v = _safe_str(row.get(col))
        if v:
            return v

    # Savant sometimes emits a single column literally named "last_name, first_name"
    # with values like "Trout, Mike".
    combined = _safe_str(row.get('last_name, first_name'))
    if combined:
        if ',' in combined:
            last, _, first = combined.partition(',')
            return f'{first.strip()} {last.strip()}'
        return combined

    last  = _safe_str(row.get('last_name'))
    first = _safe_str(row.get('first_name'))
    if last or first:
        return f'{first} {last}'.strip() if first else last
    return ''


def build_savant_lookups(year: int) -> Tuple[Dict[str, dict], Dict[str, dict]]:
    """Return (bat_lookup, pit_lookup) keyed by normalized player name.

    bat_lookup values : xwOBA, wOBA, xBA, BA, xSLG, SLG, xwoba_diff, xba_diff
    pit_lookup values : xERA, ERA, xera_diff, xwOBA, wOBA
    """
    bat_df = _fetch_savant('batter', year)
    pit_df = _fetch_savant('pitcher', year)

    bat_lookup: Dict[str, dict] = {}
    for _, row in bat_df.iterrows():
        nn = normalize_name(_build_name(row))
        if not nn:
            continue
        xwoba = _safe_float(row.get('est_woba'))
        woba  = _safe_float(row.get('woba'))
        xba   = _safe_float(row.get('est_ba'))
        ba    = _safe_float(row.get('ba'))
        xslg  = _safe_float(row.get('est_slg'))
        slg   = _safe_float(row.get('slg'))
        bat_lookup[nn] = {
            'xwOBA':      xwoba,
            'wOBA':       woba,
            'xBA':        xba,
            'BA':         ba,
            'xSLG':       xslg,
            'SLG':        slg,
            'xwoba_diff': round(xwoba - woba, 3),
            'xba_diff':   round(xba   - ba,   3),
        }

    if not bat_lookup:
        cols = list(bat_df.columns)
        sample = [_build_name(bat_df.iloc[i]) for i in range(min(3, len(bat_df)))]
        raise ValueError(
            f'Savant: built 0 batter entries from {len(bat_df)} rows. '
            f'Columns: {cols}. _build_name samples: {sample}'
        )

    pit_lookup: Dict[str, dict] = {}
    for _, row in pit_df.iterrows():
        nn = normalize_name(_build_name(row))
        if not nn:
            continue
        xera     = _safe_float(row.get('xera'))
        era_col  = 'era' if 'era' in row.index else 'p_era'
        era      = _safe_float(row.get(era_col))
        xwoba    = _safe_float(row.get('est_woba'))
        woba     = _safe_float(row.get('woba'))
        pit_lookup[nn] = {
            'xERA':      xera,
            'ERA':       era,
            'xera_diff': round(era - xera, 3),
            'xwOBA':     xwoba,
            'wOBA':      woba,
        }

    return bat_lookup, pit_lookup


def _fuzzy_lookup_savant(nn: str, lookup: Dict[str, dict], threshold: float = 90.0) -> Optional[dict]:
    """Fuzzy name fallback for Savant dicts keyed by normalized name."""
    try:
        from rapidfuzz import process, fuzz  # type: ignore
    except ImportError:
        return None
    keys = list(lookup.keys())
    if not keys:
        return None
    result = process.extractOne(nn, keys, scorer=fuzz.WRatio, score_cutoff=threshold)
    return lookup[result[0]] if result is not None else None


def get_savant_signals(
    free_agents,
    roster,
    bat_lookup: Dict[str, dict],
    pit_lookup: Dict[str, dict],
    starts_remaining: Optional[Dict[str, int]] = None,
    top_n: int = 8,
) -> dict:
    """Classify players into buy-low / sell-high groups.

    starts_remaining : {normalized_name: starts_this_week} from the MLB schedule API.
    Sell-high pitcher entries include a 'starts' key so the display layer can flag
    pitchers who are still worth starting this week despite the regression signal.
    """
    fa_buy_hit: List[dict] = []
    fa_buy_pit: List[dict] = []
    for fa in free_agents:
        nn = normalize_name(fa.name)
        if fa.position in ('SP', 'RP', 'P'):
            d = pit_lookup.get(nn) or _fuzzy_lookup_savant(nn, pit_lookup)
            if (d and d['xera_diff'] > _PIT_XERA_THRESHOLD
                    and d['xERA'] > 0 and d['ERA'] <= _PIT_ERA_MAX):
                fa_buy_pit.append({'player': fa, **d})
        else:
            d = bat_lookup.get(nn) or _fuzzy_lookup_savant(nn, bat_lookup)
            if d and d['xwoba_diff'] > _BAT_XWOBA_THRESHOLD and d['xwOBA'] > 0:
                fa_buy_hit.append({'player': fa, **d})

    fa_buy_hit.sort(key=lambda x: x['xwoba_diff'], reverse=True)
    fa_buy_pit.sort(key=lambda x: x['xera_diff'],  reverse=True)

    sr = starts_remaining or {}
    roster_sell_hit: List[dict] = []
    roster_sell_pit: List[dict] = []
    for p in roster:
        nn = normalize_name(p.name)
        if p.position in ('SP', 'RP', 'P'):
            d = pit_lookup.get(nn) or _fuzzy_lookup_savant(nn, pit_lookup)
            if d and d['xera_diff'] < -_PIT_XERA_THRESHOLD and d['ERA'] > 0:
                starts = sr.get(nn, -1)  # -1 = probable not yet announced
                roster_sell_pit.append({'player': p, 'starts': starts, **d})
        else:
            d = bat_lookup.get(nn) or _fuzzy_lookup_savant(nn, bat_lookup)
            if d and d['xwoba_diff'] < -_BAT_XWOBA_THRESHOLD and d['wOBA'] > 0:
                roster_sell_hit.append({'player': p, **d})

    roster_sell_hit.sort(key=lambda x: x['xwoba_diff'])
    roster_sell_pit.sort(key=lambda x: x['xera_diff'])

    return {
        'fa_buy_hitters':       fa_buy_hit[:top_n],
        'fa_buy_pitchers':      fa_buy_pit[:top_n],
        'roster_sell_hitters':  roster_sell_hit[:top_n],
        'roster_sell_pitchers': roster_sell_pit[:top_n],
    }
