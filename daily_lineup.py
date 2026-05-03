"""
Daily start/sit recommendations.

For each batter on the roster, produces a per-day score combining:
  1. Per-game projection scaled from ROS counting stats.
  2. Platoon advantage based on opposing probable pitcher's handedness.
  3. Recent form (last 14 days vs season pace), Bayesian-shrunk toward season avg.
  4. Park factor (hitter-friendliness of today's venue), split by bat hand.
  5. Opposing SP quality relative to league-average ERA.

For pitchers, simply flags whose probable start is today.

A greedy slot assignment fills the league lineup highest-scored player first,
processing slots in scarcity order (positions with fewest eligible players go
first) so a flex slot can't poach the only catcher.  UTIL tiebreaks prefer
players whose team has more games remaining this week.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from pybaseball_stats import lookup_batter, lookup_pitcher, normalize_name
from roster import (
    BENCH_SLOTS, eligible_display, eligible_positions,
    is_pitcher as is_pitcher_player,
    playing_positions,
)


# ── Park factors split by batter handedness ──────────────────────────────────
# Each venue maps to {'L': factor, 'R': factor}.  Switch hitters ('S') or
# unknown handedness fall back to the average of L and R.
# Sources: Statcast / FanGraphs 3-year park factor tables (approximate).
_PARK_SPLITS: Dict[str, Dict[str, float]] = {
    'Coors Field':              {'L': 1.20, 'R': 1.20},
    'Great American Ball Park': {'L': 1.08, 'R': 1.10},
    'Globe Life Field':         {'L': 1.07, 'R': 1.07},
    # Fenway: Green Monster boosts RHB pull-side doubles; LHB pull to deep RF
    'Fenway Park':              {'L': 1.03, 'R': 1.09},
    # Yankee Stadium: short porch in RF is a LHB gift
    'Yankee Stadium':           {'L': 1.09, 'R': 1.02},
    'Truist Park':              {'L': 1.04, 'R': 1.04},
    'Citizens Bank Park':       {'L': 1.04, 'R': 1.04},
    'Wrigley Field':            {'L': 1.03, 'R': 1.03},
    'Rogers Centre':            {'L': 1.02, 'R': 1.04},
    'Chase Field':              {'L': 1.01, 'R': 1.03},
    # Camden: shorter RF porch helps LHBs slightly
    'Camden Yards':             {'L': 1.03, 'R': 1.01},
    'Oriole Park at Camden Yards': {'L': 1.03, 'R': 1.01},
    'Nationals Park':           {'L': 1.01, 'R': 1.01},
    # Target Field: warm-season hitter-friendly, RHB benefit from shorter RF
    'Target Field':             {'L': 1.00, 'R': 1.02},
    'Citi Field':               {'L': 0.99, 'R': 1.01},
    'Progressive Field':        {'L': 1.00, 'R': 1.00},
    'Daikin Park':              {'L': 1.00, 'R': 1.00},
    # Minute Maid: Crawford Boxes in LF boost RHB
    'Minute Maid Park':         {'L': 0.98, 'R': 1.02},
    'Dodger Stadium':           {'L': 0.99, 'R': 0.99},
    'Angel Stadium':            {'L': 0.98, 'R': 1.00},
    'Busch Stadium':            {'L': 0.97, 'R': 0.99},
    # PNC: deep RF hurts LHB pull-side HRs
    'PNC Park':                 {'L': 0.96, 'R': 1.00},
    'American Family Field':    {'L': 0.97, 'R': 0.99},
    'Kauffman Stadium':         {'L': 0.96, 'R': 0.98},
    'Comerica Park':            {'L': 0.96, 'R': 0.98},
    'Sutter Health Park':       {'L': 0.97, 'R': 0.97},
    'Oakland Coliseum':         {'L': 0.96, 'R': 0.96},
    'Guaranteed Rate Field':    {'L': 0.95, 'R': 0.97},
    'Rate Field':               {'L': 0.95, 'R': 0.97},
    # Oracle: very deep RF hurts RHB pull-side power specifically
    'Oracle Park':              {'L': 0.96, 'R': 0.92},
    # loanDepot: deep RF hurts LHB pull; moderate for RHB
    'loanDepot park':           {'L': 0.91, 'R': 0.95},
    'LoanDepot park':           {'L': 0.91, 'R': 0.95},
    # Petco: pitcher-friendly, LCF and CF very deep
    'Petco Park':               {'L': 0.93, 'R': 0.95},
    'T-Mobile Park':            {'L': 0.91, 'R': 0.93},
    'Tropicana Field':          {'L': 0.91, 'R': 0.93},
    'George M. Steinbrenner Field': {'L': 0.96, 'R': 0.96},
}


def _park_factor(venue: str, bat_hand: str = '') -> float:
    if not venue:
        return 1.0
    splits = _PARK_SPLITS.get(venue)
    if splits is None:
        return 1.0
    hand = bat_hand if bat_hand in ('L', 'R') else ''
    if hand:
        return splits[hand]
    return sum(splits.values()) / len(splits)


# ── Platoon multiplier (batter vs pitcher hand) ─────────────────────────────
# Real platoon splits average ~10% of OPS at the population level. We damp
# slightly because the rest of the score is already projection-driven.
def _platoon_multiplier(bats: str, throws: str) -> float:
    if not bats or not throws:
        return 1.0
    if bats == 'S':
        return 1.0
    if bats == throws:
        return 0.93        # same-handed (disadvantage)
    return 1.07            # opposite-handed (advantage)


# ── Opposing SP quality ─────────────────────────────────────────────────────
_LEAGUE_AVG_ERA = 4.20

def _opp_sp_quality(opp_sp_stats: Optional[dict]) -> float:
    """Multiplier for opposing starter quality relative to league-average ERA.

    Good SPs (low ERA) penalise batter scores; bad SPs (high ERA) boost them.
    ERA is clamped to [1.50, 8.00] to dampen tiny-sample extremes, then
    scaled around the league average with a sensitivity of 0.04 per ERA point.
    Result clamped to [0.85, 1.15].
    """
    if not opp_sp_stats:
        return 1.0
    era = opp_sp_stats.get('ERA')
    if era is None or float(era) <= 0:
        return 1.0
    era = max(1.50, min(8.00, float(era)))
    raw = 1.0 + (era - _LEAGUE_AVG_ERA) * 0.04
    return round(max(0.85, min(1.15, raw)), 3)


# ── Per-game value ─────────────────────────────────────────────────────────
def _per_game_value(proj: dict, batting_cats, stat_weights: dict) -> float:
    """League-weighted sum of per-game counting stats from a season projection.

    Rate stats are skipped — they're already implied by the counting categories
    that drive them, and dividing AVG by games would be meaningless.
    """
    if not proj:
        return 0.0
    games = float(proj.get('G') or 0)
    if games <= 0:
        return 0.0
    score = 0.0
    for cat in batting_cats:
        if cat.get('is_rate'):
            continue
        val = proj.get(cat['name'])
        if val is None:
            continue
        per_g = float(val) / games
        weight = float(stat_weights.get(cat['name'], 1.0) or 1.0)
        if cat.get('is_inverse'):
            score -= per_g * weight
        else:
            score += per_g * weight
    return score


# ── Recent form (Bayesian-shrunk) ──────────────────────────────────────────
_FORM_PRIOR_PA = 50.0   # prior weight in PA; shrinks ratio toward 1.0 for short samples

def _recent_form_multiplier(
    recent_proj: Optional[dict], season_proj: Optional[dict],
    batting_cats, stat_weights: dict,
) -> float:
    """Bayesian-shrunk ratio of recent (L14) per-game value to season per-game value.

    Blends the observed ratio with a prior of 1.0 (= season pace) weighted by
    _FORM_PRIOR_PA.  A batter with 10 PA in the recent window gets ~17% weight
    on their recent ratio; one with 50 PA gets ~50%.  Final result clamped to
    [0.90, 1.10] — tighter than the old [0.75, 1.25] to resist noise.
    """
    if not recent_proj or not season_proj:
        return 1.0
    recent_pgv = _per_game_value(recent_proj, batting_cats, stat_weights)
    season_pgv = _per_game_value(season_proj, batting_cats, stat_weights)
    if season_pgv <= 0 or recent_pgv <= 0:
        return 1.0
    ratio = recent_pgv / season_pgv
    recent_pa = float(recent_proj.get('PA') or 0)
    blended = (recent_pa * ratio + _FORM_PRIOR_PA * 1.0) / (recent_pa + _FORM_PRIOR_PA)
    return round(max(0.90, min(1.10, blended)), 3)


# ── Batter scoring ─────────────────────────────────────────────────────────
def score_batter_today(
    player,
    season_proj: Optional[dict],
    recent_proj: Optional[dict],
    matchup: Optional[dict],
    bat_hand: str,
    pitch_hand: str,
    opp_sp_stats: Optional[dict],
    batting_cats,
    stat_weights: dict,
) -> dict:
    """Return a scoring breakdown for a single batter on today's slate.

    matchup is the dict from get_daily_matchups()[team_abbr] — None means the
    player's team has no game today.
    """
    base = _per_game_value(season_proj or {}, batting_cats, stat_weights)
    has_game = matchup is not None
    if not has_game or base <= 0:
        return {
            'player':      player,
            'has_game':    has_game,
            'score':       0.0,
            'base':        round(base, 2),
            'platoon':     1.0,
            'form':        1.0,
            'park':        1.0,
            'opp_quality': 1.0,
            'matchup':     matchup,
            'bat_hand':    bat_hand,
            'pitch_hand':  pitch_hand,
        }

    platoon     = _platoon_multiplier(bat_hand, pitch_hand)
    form        = _recent_form_multiplier(recent_proj, season_proj, batting_cats, stat_weights)
    park        = _park_factor(matchup.get('venue', ''), bat_hand)
    opp_quality = _opp_sp_quality(opp_sp_stats)

    return {
        'player':      player,
        'has_game':    True,
        'score':       round(base * platoon * form * park * opp_quality, 3),
        'base':        round(base, 2),
        'platoon':     round(platoon, 3),
        'form':        round(form, 3),
        'park':        round(park, 3),
        'opp_quality': opp_quality,
        'matchup':     matchup,
        'bat_hand':    bat_hand,
        'pitch_hand':  pitch_hand,
    }


# ── Pitcher: starting today flag ───────────────────────────────────────────
def is_pitcher_starting_today(player, daily_matchups: Dict[str, dict]) -> bool:
    """True if this pitcher's team plays today and they are the listed probable.

    Matches by normalized name on the opposing-team side of the matchup record.
    The matchup dict keys are team abbreviations; each entry's `opp_pitcher`
    is the *opposing* probable starter, so for player team T we check the
    opponent's matchup record.
    """
    team = str(getattr(player, 'proTeam', '') or '').upper().strip()
    own = daily_matchups.get(team)
    if not own:
        return False
    opp_abbr = own.get('opponent')
    opp = daily_matchups.get(opp_abbr) if opp_abbr else None
    if not opp:
        return False
    pp = opp.get('opp_pitcher', '')
    if not pp:
        return False
    return normalize_name(pp) == normalize_name(player.name)


# ── Position-respecting slot assignment ────────────────────────────────────
def _slot_eligibility(slot: str, player) -> bool:
    return slot in getattr(player, 'eligibleSlots', [])


def _pid(entry):
    p = entry['player']
    return getattr(p, 'playerId', id(p))


def assign_lineup(
    scored_batters: List[dict],
    starting_slots: List[str],
    team_games_remaining: Optional[Dict[str, int]] = None,
) -> Tuple[List[dict], List[dict]]:
    """Position-respecting slot assignment.

    Rules:
      - Current starters stay in their assigned slot whenever they have a game
        today and aren't outscored by a same-slot-eligible bench player.
      - Bench players are only promoted into a starting slot if they share
        position eligibility with the slot they take (same-position swap), or
        into a UTIL slot.
      - Cross-slot starter shuffles are not generated — Correa at 3B does not
        get suggested for SS.

    UTIL is filled last from any unassigned player with a game.  Ties among
    UTIL candidates break in favour of the player whose team has more games
    remaining this scoring period (multi-day lookahead).

    Returns (lineup_assignments, leftover_bench).
    """
    tgr = team_games_remaining or {}

    real_slots = [s for s in starting_slots if s not in BENCH_SLOTS
                  and s not in ('SP', 'RP', 'P')]

    slot_counts: Dict[str, int] = {}
    for s in real_slots:
        slot_counts[s] = slot_counts.get(s, 0) + 1

    bench_pool: List[dict] = []
    by_current_slot: Dict[str, List[dict]] = {}
    for entry in scored_batters:
        slot = getattr(entry['player'], 'lineupSlot', '')
        if slot in BENCH_SLOTS:
            bench_pool.append(entry)
        else:
            by_current_slot.setdefault(slot, []).append(entry)

    used_pids = set()
    lineup: List[dict] = []

    # Process non-UTIL slots scarcer-first so multi-position bench players
    # land where they're most needed.
    non_util = [s for s in slot_counts if s != 'UTIL']

    def candidate_count(slot: str) -> int:
        c = len(by_current_slot.get(slot, []))
        c += sum(1 for b in bench_pool if _slot_eligibility(slot, b['player']))
        return c

    non_util.sort(key=lambda s: (candidate_count(s), s))

    for slot in non_util:
        count = slot_counts[slot]
        current = [
            c for c in by_current_slot.get(slot, [])
            if _pid(c) not in used_pids and c.get('has_game')
        ]
        bench_eligible = [
            b for b in bench_pool
            if _pid(b) not in used_pids
            and _slot_eligibility(slot, b['player'])
            and b.get('has_game')
        ]
        # Rank by score; ties break toward the current occupant (status-quo).
        pool = current + bench_eligible
        pool.sort(
            key=lambda x: (x['score'], 1 if x in current else 0),
            reverse=True,
        )
        for entry in pool[:count]:
            lineup.append({'slot': slot, **entry})
            used_pids.add(_pid(entry))

    # UTIL last — any unassigned player with a game.
    # Primary sort: today's score.  Tiebreaker: games remaining this week.
    util_count = slot_counts.get('UTIL', 0)
    if util_count:
        util_pool = [
            e for e in scored_batters
            if _pid(e) not in used_pids
            and e.get('has_game')
            and 'UTIL' in getattr(e['player'], 'eligibleSlots', [])
        ]

        def _util_key(e):
            team = str(getattr(e['player'], 'proTeam', '') or '').upper().strip()
            games_left = tgr.get(team, 0)
            return (e['score'], games_left)

        util_pool.sort(key=_util_key, reverse=True)
        for entry in util_pool[:util_count]:
            lineup.append({'slot': 'UTIL', **entry})
            used_pids.add(_pid(entry))

    bench = [e for e in scored_batters if _pid(e) not in used_pids]
    return lineup, bench


# ── Top-level orchestrator ─────────────────────────────────────────────────
def recommend_daily_lineup(
    roster,
    daily_matchups: Dict[str, dict],
    handedness: Dict[str, Dict[str, str]],
    season_lookup: Optional[Tuple],
    recent_lookup: Optional[Tuple],
    starting_slots: List[str],
    batting_cats,
    pitching_cats,
    stat_weights: dict,
    team_games_remaining: Optional[Dict[str, int]] = None,
) -> dict:
    """Build the full daily recommendation payload.

    season_lookup / recent_lookup are 4-tuples in the
    (bat_primary, bat_by_name, pit_primary, pit_by_name) shape returned by
    pybaseball_stats.build_*_lookups; either may be None when unavailable.

    Returns a dict with:
      'lineup'    : assigned starting batters [{slot, player, score, ...}]
      'bench'     : batters with games today but not in the lineup
      'off_days'  : roster batters whose team has no game today
      'sps_today' : pitchers from the roster scheduled to start today
      'sps_off'   : roster SPs whose team plays but they aren't the probable
    """
    s_bat_p = s_bat_bn = s_pit_p = s_pit_bn = None
    if season_lookup is not None:
        s_bat_p, s_bat_bn, s_pit_p, s_pit_bn = season_lookup
    r_bat_p = r_bat_bn = None
    if recent_lookup is not None:
        r_bat_p, r_bat_bn, _, _ = recent_lookup

    # Pre-compute opp SP stats per (batter's) opponent team so we only look
    # up each pitcher once rather than once per roster player.
    _opp_sp_cache: Dict[str, Optional[dict]] = {}

    def _get_opp_sp_stats(opp_pitcher: str, opp_team: str) -> Optional[dict]:
        key = f'{opp_pitcher}|{opp_team}'
        if key in _opp_sp_cache:
            return _opp_sp_cache[key]
        stats = None
        if opp_pitcher and s_pit_p is not None:
            stats = lookup_pitcher(opp_pitcher, opp_team, s_pit_p, s_pit_bn)
        _opp_sp_cache[key] = stats
        return stats

    scored: List[dict] = []
    off_days: List[dict] = []
    for player in roster:
        if is_pitcher_player(player):
            continue
        # Players on IL can't be moved into a starting slot without a separate
        # roster move; skip them from the daily decision entirely.
        if getattr(player, 'lineupSlot', '') == 'IL':
            continue
        team = str(getattr(player, 'proTeam', '') or '').upper().strip()
        matchup = daily_matchups.get(team)

        season_proj = None
        if s_bat_p is not None:
            season_proj = lookup_batter(player.name, player.proTeam, s_bat_p, s_bat_bn)
        if not season_proj:
            # Fallback to ESPN season projection on the player itself
            season_proj = (player.stats.get(0) or {}).get('projected_breakdown') or {}

        recent_proj = None
        if r_bat_p is not None:
            recent_proj = lookup_batter(player.name, player.proTeam, r_bat_p, r_bat_bn)

        nn = normalize_name(player.name)
        bat_hand = (handedness.get(nn) or {}).get('bats', '')

        pitch_hand = ''
        opp_sp_stats = None
        if matchup:
            opp_pp = matchup.get('opp_pitcher', '')
            opp_team = matchup.get('opponent', '')
            if opp_pp:
                pitch_hand = (handedness.get(normalize_name(opp_pp)) or {}).get('throws', '')
                opp_sp_stats = _get_opp_sp_stats(opp_pp, opp_team)

        entry = score_batter_today(
            player, season_proj, recent_proj, matchup,
            bat_hand, pitch_hand, opp_sp_stats,
            batting_cats, stat_weights,
        )
        entry['eligible'] = eligible_display(player)

        if not entry['has_game']:
            off_days.append(entry)
        else:
            scored.append(entry)

    lineup, bench = assign_lineup(scored, starting_slots, team_games_remaining)

    sps_today: List[dict] = []
    sps_off:   List[dict] = []
    rps_with_game: List[dict] = []
    rps_off:       List[dict] = []
    for player in roster:
        if not is_pitcher_player(player):
            continue
        if getattr(player, 'lineupSlot', '') == 'IL':
            continue
        team = str(getattr(player, 'proTeam', '') or '').upper().strip()
        own = daily_matchups.get(team)
        info = {
            'player':    player,
            'eligible':  eligible_display(player),
            'opponent':  (own or {}).get('opponent', ''),
            'venue':     (own or {}).get('venue', ''),
            'is_home':   (own or {}).get('is_home', False),
            'has_game':  own is not None,
        }
        if player.position == 'RP':
            # RPs don't start; only the team's game presence matters today.
            if own is not None:
                rps_with_game.append(info)
            else:
                rps_off.append(info)
            continue
        # SP-eligible (includes 'P' multi-position when treated as SP).
        if own is None:
            continue
        if is_pitcher_starting_today(player, daily_matchups):
            sps_today.append(info)
        else:
            sps_off.append(info)

    return {
        'lineup':        lineup,
        'bench':         bench,
        'off_days':      off_days,
        'sps_today':     sps_today,
        'sps_off':       sps_off,
        'rps_with_game': rps_with_game,
        'rps_off':       rps_off,
    }
