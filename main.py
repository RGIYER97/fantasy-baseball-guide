import argparse
import sys
from tabulate import tabulate

import datetime

from cache import cache_get, cache_set, _SCHEDULE_TTL
from daily_lineup import recommend_daily_lineup
from league_client import LeagueClient
from pybaseball_stats import (
    build_fangraphs_lookups, build_recent_splits_lookups,
    build_plate_discipline_lookups, normalize_name,
)
from projections_stats import build_projection_lookups, PROJECTION_SYSTEMS
from savant_stats import build_savant_lookups, get_savant_signals
from mlb_stats import get_schedule_context, get_daily_matchups, get_player_handedness
from recommender import Recommender, consensus_pickups
from roster import eligible_display, get_starting_slots, positional_scarcity, set_league_slots

RATE_3DEC = {'AVG', 'OBP', 'SLG', 'OPS', 'WHIP', 'OBA', 'OOBP'}
RATE_2DEC = {'ERA', 'K/9', 'K/BB', 'PPA', 'FPCT', 'SV%', 'WPCT'}

CLOSE_THRESHOLDS = {
    'R': 5, 'HR': 3, 'RBI': 5, 'SB': 3, 'H': 5, '2B': 3, '3B': 2,
    'B_BB': 4, 'XBH': 3, 'TB': 8, '1B': 4, 'K': 8, 'W': 2, 'SV': 2,
    'HLD': 3, 'QS': 2, 'SVHD': 3, 'L': 2,
    'AVG': 0.015, 'OBP': 0.020, 'SLG': 0.030, 'OPS': 0.040,
    'ERA': 0.75, 'WHIP': 0.10, 'K/9': 1.5, 'K/BB': 0.5,
}


def fmt(name, value):
    if value is None:
        return '-'
    if name in RATE_3DEC:
        return f'{value:.3f}'
    if name in RATE_2DEC:
        return f'{value:.2f}'
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    if isinstance(value, float):
        return f'{value:.1f}'
    return str(value)


def fmt_delta(name, value):
    if value is None:
        return ''
    sign = '+' if value >= 0 else ''
    if name in RATE_3DEC:
        return f'{sign}{value:.3f}'
    if name in RATE_2DEC:
        return f'{sign}{value:.2f}'
    if isinstance(value, float) and value == int(value):
        return f'{sign}{int(value)}'
    if isinstance(value, float):
        return f'{sign}{value:.1f}'
    return f'{sign}{value}'


def section(title):
    print()
    print('=' * 64)
    print(f'  {title}')
    print('=' * 64)


def show_matchup(analysis, matchup, matchup_info):
    section('Current Matchup Status')
    opp = matchup['opponent']
    opp_name = opp.team_name if hasattr(opp, 'team_name') else str(opp)
    print(f'  vs. {opp_name}')
    if matchup_info:
        print(f'  Day {matchup_info["total_days"] - matchup_info["days_remaining"] + 1}'
              f' of {matchup_info["total_days"]}'
              f' ({matchup_info["days_remaining"]} remaining)')
    print()

    rows = []
    for a in analysis:
        label = a['result']
        if label == 'LOSS' and abs(a['margin']) <= CLOSE_THRESHOLDS.get(a['name'], 3):
            label += '  ← flippable'
        elif label == 'WIN' and abs(a['margin']) <= CLOSE_THRESHOLDS.get(a['name'], 3):
            label += '  (close)'
        rows.append([
            a['name'],
            fmt(a['name'], a['my_value']),
            fmt(a['name'], a['opp_value']),
            f'{a["margin"]:+.3f}' if a['is_rate'] else f'{a["margin"]:+.0f}',
            label,
        ])
    print(tabulate(rows, headers=['Category', 'You', 'Opp', 'Margin', 'Status'],
                   tablefmt='simple'))

    w, l, t = matchup['my_wins'], matchup['my_losses'], matchup['my_ties']
    tag = 'WINNING' if w > l else ('LOSING' if l > w else 'TIED')
    print(f'\n  Overall: {w}–{l}–{t}  ({tag})')


def show_roster(roster, categories):
    section('Your Roster')
    bat_cats = [c['name'] for c in categories if c['is_batting']]
    pit_cats = [c['name'] for c in categories if c['is_pitching']]

    bat_show = bat_cats[:7]
    pit_show = pit_cats[:7]

    scarcity = positional_scarcity(roster)

    hitters, pitchers = [], []
    for p in roster:
        proj = p.stats.get(0, {}).get('projected_breakdown', {})
        inj = f' [{p.injuryStatus}]' if p.injuryStatus and p.injuryStatus != 'ACTIVE' else ''
        elig = eligible_display(p)
        if p.position in ('SP', 'RP', 'P'):
            row = [p.lineupSlot, p.name + inj, elig, p.proTeam]
            for c in pit_show:
                row.append(fmt(c, proj.get(c)))
            pitchers.append(row)
        else:
            row = [p.lineupSlot, p.name + inj, elig, p.proTeam]
            for c in bat_show:
                row.append(fmt(c, proj.get(c)))
            hitters.append(row)

    if hitters:
        print('\n  Hitters')
        print(tabulate(hitters, headers=['Slot', 'Player', 'Eligible', 'Team'] + bat_show,
                       tablefmt='simple'))
    if pitchers:
        print('\n  Pitchers')
        print(tabulate(pitchers, headers=['Slot', 'Player', 'Eligible', 'Team'] + pit_show,
                       tablefmt='simple'))

    scarce = [slot for slot, cnt in scarcity.items() if cnt <= 1]
    if scarce:
        print(f'\n  ⚠  Thin positions (only 1 eligible player): {", ".join(sorted(scarce))}')


def _proj_for_rec(rec):
    if rec.get('proj'):
        return rec['proj']
    p = rec['player']
    return p.stats.get(0, {}).get('projected_breakdown', {})


def _show_move_plan(moves):
    """Print a compact transaction plan showing each drop → add pair."""
    if not moves:
        return
    n = len(moves)
    print(f'\n  ── Recommended roster moves: {n} ──')
    for i, move in enumerate(moves, 1):
        drop = move['drop']
        add_rec = move['add']
        dp = drop['player']
        ap = add_rec['player']
        d_elig = drop.get('eligible', dp.position)
        a_elig = add_rec.get('eligible', ap.position)
        d_slot = 'bench' if drop['is_bench'] else drop['lineup_slot']
        util_tag = '  [for UTIL]' if add_rec.get('for_util') else ''
        print(f'    {i}. Drop {dp.name} ({d_elig}, {d_slot})'
              f'  →  Add {ap.name} ({a_elig}, {ap.proTeam}){util_tag}')


def _urgency_header(days_remaining):
    if days_remaining is None:
        return
    if days_remaining == 1:
        print(f'\n  ⚡ URGENT — final day of matchup period. Only players active TODAY matter.')
    elif days_remaining <= 3:
        print(f'\n  ⚡ {days_remaining} days remaining — prioritise players with confirmed starts/games.')
    elif days_remaining <= 5:
        print(f'\n  ⏰ {days_remaining} days remaining in matchup period.')


def show_weekly(weekly, categories, subtitle='', days_remaining=None):
    title = 'Weekly Pickup Recommendations'
    if subtitle:
        title = f'{title} — {subtitle}'
    section(title)

    losing = weekly.get('losing_categories', [])
    if not losing:
        print('\n  You are not losing any categories — no pickups needed this week!')
        return

    _urgency_header(days_remaining)

    proj_src = weekly.get('projection_source', 'season')
    src_label = 'weekly (matchup period)' if proj_src == 'weekly' else 'full-season'
    print(f'\n  Targeting categories: {", ".join(losing)}')
    print(f'  Projection basis:    {src_label}')

    bat_cats = [c['name'] for c in categories if c['is_batting']]
    pit_cats = [c['name'] for c in categories if c['is_pitching']]

    _show_player_table('Hitter',  weekly['hitters'],  bat_cats, schedule_col='G♦')
    _show_player_table('Pitcher', weekly['pitchers'], pit_cats, schedule_col='Starts')
    _show_move_plan(weekly.get('moves', []))


def show_season(season, categories, subtitle=''):
    title = 'Season-Long Pickup Recommendations'
    if subtitle:
        title = f'{title} — {subtitle}'
    section(title)

    bat_cats = [c['name'] for c in categories if c['is_batting']]
    pit_cats = [c['name'] for c in categories if c['is_pitching']]

    _show_player_table('Hitter', season['hitters'], bat_cats)
    _show_player_table('Pitcher', season['pitchers'], pit_cats)
    _show_move_plan(season.get('moves', []))


def _format_drop(drop_info, for_util=False):
    """Return a compact string describing a single drop candidate."""
    if not drop_info:
        return '—'
    dp = drop_info['player']
    slot = 'bench' if drop_info['is_bench'] else drop_info['lineup_slot']
    elig = drop_info.get('eligible', dp.position)
    inj = f', {drop_info["injury"]}' if drop_info.get('injury') else ''
    util_tag = '  [add for UTIL]' if for_util else ''
    return f'{dp.name} ({elig}, {slot}{inj}){util_tag}'


def _show_player_table(label, recs, cat_names, schedule_col=None):
    """Render a pickup recommendation table.

    schedule_col : optional column header ('Starts' or 'G♦') — when present,
                   a starts_remaining / games_remaining value is appended from
                   each rec dict. '?' = unknown (probable not announced), 0 = none.
    """
    if not recs:
        print(f'\n  No {label.lower()} recommendations.')
        return

    show_cats = cat_names[:7]
    has_sched = schedule_col is not None
    print(f'\n  Top {label} Pickups')

    rows = []
    for i, rec in enumerate(recs, 1):
        p = rec['player']
        proj = _proj_for_rec(rec)
        elig = rec.get('eligible', p.position)
        row = [i, p.name, p.proTeam, elig, f'{p.percent_owned:.0f}%']
        for c in show_cats:
            row.append(fmt(c, proj.get(c)))
        row.append(rec['score'])
        if has_sched:
            # 'Starts' key for pitchers, 'games_remaining' for hitters
            sched_key = 'starts_remaining' if schedule_col == 'Starts' else 'games_remaining'
            val = rec.get(sched_key, '?')
            row.append('?' if val == -1 else val)
        rows.append(row)

        drop_info = rec.get('drop')
        deltas = rec.get('category_deltas')
        if drop_info or deltas:
            drop_str = _format_drop(drop_info, for_util=rec.get('for_util', False))
            delta_parts = []
            if deltas:
                for c in show_cats:
                    d = deltas.get(c)
                    delta_parts.append(fmt_delta(c, d) if d is not None else '')
            else:
                delta_parts = [''] * len(show_cats)
            pad = ['', f'  → drop: {drop_str}', '', '', ''] + delta_parts + ['']
            if has_sched:
                pad.append('')
            rows.append(pad)

    headers = ['#', 'Player', 'Team', 'Eligible', 'Own%'] + show_cats + ['Score']
    if has_sched:
        headers.append(schedule_col)
    print(tabulate(rows, headers=headers, tablefmt='simple'))


def show_consensus_weekly(h_cons, p_cons, categories, title='ESPN ∩ FanGraphs (both top lists)'):
    section(f'Weekly — {title}')
    bat_cats = [c['name'] for c in categories if c['is_batting']]
    pit_cats = [c['name'] for c in categories if c['is_pitching']]
    _show_consensus_table('Hitter', h_cons, bat_cats)
    _show_consensus_table('Pitcher', p_cons, pit_cats)


def show_consensus_season(h_cons, p_cons, categories, title='ESPN ∩ FanGraphs (both top lists)'):
    section(f'Season — {title}')
    bat_cats = [c['name'] for c in categories if c['is_batting']]
    pit_cats = [c['name'] for c in categories if c['is_pitching']]
    _show_consensus_table('Hitter', h_cons, bat_cats)
    _show_consensus_table('Pitcher', p_cons, pit_cats)


def show_plate_discipline(free_agents, pit_disc, bat_disc, categories, top_n=8):
    section('Plate Discipline Signals (FanGraphs)')
    bat_fa = [fa for fa in free_agents if fa.position not in ('SP', 'RP', 'P')]
    pit_fa = [fa for fa in free_agents if fa.position in ('SP', 'RP', 'P')]

    print('\n  Elite Swing-and-Miss FA Pitchers  (SwStr% ≥ 12 — sustained K upside)')
    pit_rows = []
    for fa in pit_fa:
        d = pit_disc.get(normalize_name(fa.name))
        if d and d.get('SwStr%') is not None and d['SwStr%'] >= 12.0:
            pit_rows.append((fa, d))
    pit_rows.sort(key=lambda x: x[1]['SwStr%'], reverse=True)
    if pit_rows[:top_n]:
        rows = []
        for fa, d in pit_rows[:top_n]:
            rows.append([fa.name, fa.proTeam, f'{fa.percent_owned:.0f}%',
                         f'{d["SwStr%"]:.1f}%' if d['SwStr%'] is not None else '-',
                         f'{d["Zone%"]:.1f}%' if d.get('Zone%') is not None else '-',
                         f'{d["F-Strike%"]:.1f}%' if d.get('F-Strike%') is not None else '-',
                         f'{d["CSW%"]:.1f}%' if d.get('CSW%') is not None else '-'])
        print(tabulate(rows, headers=['Player', 'Team', 'Own%', 'SwStr%', 'Zone%', 'F-Str%', 'CSW%'],
                       tablefmt='simple'))
    else:
        print('  None above threshold.')

    print('\n  High-Contact FA Hitters  (Contact% ≥ 80 — low strikeout, sustained AVG)')
    bat_rows = []
    for fa in bat_fa:
        d = bat_disc.get(normalize_name(fa.name))
        if d and d.get('Contact%') is not None and d['Contact%'] >= 80.0:
            bat_rows.append((fa, d))
    bat_rows.sort(key=lambda x: x[1]['Contact%'], reverse=True)
    if bat_rows[:top_n]:
        rows = []
        for fa, d in bat_rows[:top_n]:
            rows.append([fa.name, fa.proTeam, f'{fa.percent_owned:.0f}%',
                         f'{d["Contact%"]:.1f}%' if d.get('Contact%') is not None else '-',
                         f'{d["SwStr%"]:.1f}%' if d.get('SwStr%') is not None else '-',
                         f'{d["O-Swing%"]:.1f}%' if d.get('O-Swing%') is not None else '-'])
        print(tabulate(rows, headers=['Player', 'Team', 'Own%', 'Contact%', 'SwStr%', 'O-Swing%'],
                       tablefmt='simple'))
    else:
        print('  None above threshold.')


def show_savant_signals(signals):
    section('Statcast xStats Signals (Baseball Savant)')

    fa_buy_hit  = signals['fa_buy_hitters']
    fa_buy_pit  = signals['fa_buy_pitchers']
    sell_hit    = signals['roster_sell_hitters']
    sell_pit    = signals['roster_sell_pitchers']

    print('\n  Buy-Low Free Agent Hitters  (xwOBA significantly above actual wOBA)')
    if fa_buy_hit:
        rows = []
        for s in fa_buy_hit:
            p = s['player']
            rows.append([
                p.name, p.proTeam, f'{p.percent_owned:.0f}%',
                f'{s["xwOBA"]:.3f}', f'{s["wOBA"]:.3f}', f'+{s["xwoba_diff"]:.3f}',
                f'{s["xBA"]:.3f}',   f'{s["BA"]:.3f}',   f'+{s["xba_diff"]:.3f}',
            ])
        print(tabulate(rows,
                       headers=['Player', 'Team', 'Own%', 'xwOBA', 'wOBA', 'Δxw',
                                 'xBA', 'BA', 'ΔxBA'],
                       tablefmt='simple'))
    else:
        print('  None above threshold.')

    print('\n  Buy-Low Free Agent Pitchers  (ERA significantly above xERA — likely to improve)')
    if fa_buy_pit:
        rows = []
        for s in fa_buy_pit:
            p = s['player']
            rows.append([
                p.name, p.proTeam, f'{p.percent_owned:.0f}%',
                f'{s["ERA"]:.2f}', f'{s["xERA"]:.2f}', f'+{s["xera_diff"]:.2f}',
            ])
        print(tabulate(rows,
                       headers=['Player', 'Team', 'Own%', 'ERA', 'xERA', 'Δ(ERA-xERA)'],
                       tablefmt='simple'))
    else:
        print('  None above threshold.')

    print('\n  Sell-High Roster Hitters  (wOBA significantly above xwOBA — likely to cool)')
    if sell_hit:
        rows = []
        for s in sell_hit:
            p = s['player']
            rows.append([
                p.name, p.proTeam,
                f'{s["xwOBA"]:.3f}', f'{s["wOBA"]:.3f}', f'{s["xwoba_diff"]:.3f}',
                f'{s["xBA"]:.3f}',   f'{s["BA"]:.3f}',
            ])
        print(tabulate(rows,
                       headers=['Player', 'Team', 'xwOBA', 'wOBA', 'Δxw', 'xBA', 'BA'],
                       tablefmt='simple'))
    else:
        print('  None above threshold.')

    print('\n  Sell-High Roster Pitchers  (ERA significantly below xERA — likely to rise)')
    if sell_pit:
        rows = []
        for s in sell_pit:
            p = s['player']
            starts = s.get('starts', -1)
            starts_str = '?' if starts == -1 else str(starts)
            note = ' ← hold this week' if starts >= 2 else ''
            rows.append([
                p.name, p.proTeam, starts_str,
                f'{s["ERA"]:.2f}', f'{s["xERA"]:.2f}', f'{s["xera_diff"]:.2f}',
                note,
            ])
        print(tabulate(rows,
                       headers=['Player', 'Team', 'Starts', 'ERA', 'xERA', 'Δ(ERA-xERA)', ''],
                       tablefmt='simple'))
    else:
        print('  None above threshold.')


def _show_consensus_table(label, recs, cat_names):
    if not recs:
        print(f'\n  No overlapping {label.lower()} recommendations.')
        return
    show_cats = cat_names[:5]

    print(f'\n  {label}s in both top lists (ESPN stats in table)')

    rows = []
    for i, rec in enumerate(recs, 1):
        p = rec['player']
        proj = rec.get('proj') or _proj_for_rec(rec)
        elig = rec.get('eligible', p.position)
        row = [i, p.name, elig, rec['espn_rank'], rec['fg_rank'], rec['espn_score'], rec['fg_score'],
               rec['score']]
        for c in show_cats:
            row.append(fmt(c, proj.get(c)))
        rows.append(row)

        drop_info = rec.get('drop')
        deltas = rec.get('category_deltas')
        if drop_info or deltas:
            drop_str = _format_drop(drop_info, for_util=rec.get('for_util', False))
            delta_parts = []
            if deltas:
                for c in show_cats:
                    d = deltas.get(c)
                    delta_parts.append(fmt_delta(c, d) if d is not None else '')
            else:
                delta_parts = [''] * len(show_cats)
            delta_row = ['', f'  → drop: {drop_str}', '', '', '', '', '', ''] + delta_parts
            rows.append(delta_row)

    print(tabulate(rows, headers=['#', 'Player', 'Eligible', 'E#', 'FG#', 'ESPN△', 'FG△', 'Avg△'] + show_cats,
                   tablefmt='simple'))


def _cross_source_consensus(weekly_sources, top_n=10):
    """Return (name, source_count, source_labels, best_rec) for players in 2+ sources."""
    counts: dict = {}
    labels_map: dict = {}
    best_rec: dict = {}
    for label, weekly in weekly_sources:
        if not weekly:
            continue
        pool = weekly.get('hitters', [])[:top_n] + weekly.get('pitchers', [])[:top_n]
        for r in pool:
            name = r['player'].name
            counts[name] = counts.get(name, 0) + 1
            labels_map.setdefault(name, []).append(label)
            if name not in best_rec or r.get('score', 0) > best_rec[name].get('score', 0):
                best_rec[name] = r
    return [
        (name, counts[name], labels_map[name], best_rec[name])
        for name in sorted(counts, key=lambda n: (-counts[n], -best_rec[n].get('score', 0)))
        if counts[name] >= 2
    ]


def _has_recs(rec_dict):
    return bool(rec_dict and (rec_dict.get('hitters') or rec_dict.get('pitchers')))


def show_summary(analysis, matchup, proj_outcome, opp_name,
                 weekly_sources, savant_signals):
    section('This Week — Summary')

    if matchup:
        w, l, t = matchup['my_wins'], matchup['my_losses'], matchup['my_ties']
        tag = 'WINNING' if w > l else ('LOSING' if l > w else 'TIED')
        line = f'  vs. {opp_name}:  {w}–{l}–{t}  ({tag})'
        if proj_outcome:
            pw = sum(1 for p in proj_outcome if p['projected_result'] == 'WIN')
            pl = sum(1 for p in proj_outcome if p['projected_result'] == 'LOSS')
            pu = sum(1 for p in proj_outcome if p['projected_result'] == 'PUSH')
            ptag = 'projected WIN' if pw > pl else ('projected LOSE' if pl > pw else 'projected TIE')
            line += f'  →  {pw}–{pl}–{pu} ({ptag})'
        print(line)
    else:
        print('\n  No active matchup.')

    if analysis:
        losing    = [a for a in analysis if a['result'] == 'LOSS']
        flippable = [a for a in losing if abs(a['margin']) <= CLOSE_THRESHOLDS.get(a['name'], 3)]
        if losing:
            print(f'  Losing:    {", ".join(a["name"] for a in losing)}')
        if flippable:
            print(f'  Flippable: {", ".join(a["name"] for a in flippable)}')

    consensus = _cross_source_consensus(weekly_sources)
    if consensus:
        print('\n  Top pickups (2+ sources agree):')
        for name, count, labels, r in consensus[:5]:
            p = r['player']
            src = ' + '.join(labels)
            drop_str = f'  → drop {r["drop"]["player"].name}' if r.get('drop') else ''
            print(f'    +  {p.name:<24}  {p.proTeam:<5}  [{src}]{drop_str}')
    elif analysis and any(a['result'] == 'LOSS' for a in analysis):
        print('\n  No consensus pickups — check individual source sections below.')

    if savant_signals:
        alerts = (savant_signals.get('roster_sell_pitchers', [])
                  + savant_signals.get('roster_sell_hitters', []))
        if alerts:
            print('\n  Sell-high alerts (roster players overperforming expected stats):')
            for s in alerts[:4]:
                p = s['player']
                if 'xERA' in s:
                    print(f'    ~  {p.name:<24}  ERA {s["ERA"]:.2f} → xERA {s["xERA"]:.2f}')
                else:
                    print(f'    ~  {p.name:<24}  wOBA {s["wOBA"]:.3f} → xwOBA {s["xwOBA"]:.3f}')


def show_opponent_roster(opponent, categories):
    opp_name = opponent.team_name if hasattr(opponent, 'team_name') else str(opponent)
    section(f'Opponent Roster — {opp_name}')
    bat_cats = [c['name'] for c in categories if c['is_batting']]
    pit_cats = [c['name'] for c in categories if c['is_pitching']]

    bat_show = bat_cats[:7]
    pit_show = pit_cats[:7]

    hitters, pitchers = [], []
    for p in opponent.roster:
        proj = p.stats.get(0, {}).get('projected_breakdown', {})
        inj = f' [{p.injuryStatus}]' if p.injuryStatus and p.injuryStatus != 'ACTIVE' else ''
        elig = eligible_display(p)
        if p.position in ('SP', 'RP', 'P'):
            row = [p.lineupSlot, p.name + inj, elig, p.proTeam]
            for c in pit_show:
                row.append(fmt(c, proj.get(c)))
            pitchers.append(row)
        else:
            row = [p.lineupSlot, p.name + inj, elig, p.proTeam]
            for c in bat_show:
                row.append(fmt(c, proj.get(c)))
            hitters.append(row)

    if hitters:
        print('\n  Hitters')
        print(tabulate(hitters, headers=['Slot', 'Player', 'Eligible', 'Team'] + bat_show,
                       tablefmt='simple'))
    if pitchers:
        print('\n  Pitchers')
        print(tabulate(pitchers, headers=['Slot', 'Player', 'Eligible', 'Team'] + pit_show,
                       tablefmt='simple'))


def show_projected_outcome(projection, opp_name, days_remaining=None):
    section(f'Projected Week-End Outcome  (vs. {opp_name})')
    if not projection:
        print('\n  Not enough schedule data to project outcomes.')
        return

    note = f'{days_remaining} day(s) remaining — ' if days_remaining is not None else ''
    print(f'\n  {note}counting stats projected, rate stats = current only')

    counting = [p for p in projection if not p['is_rate']]
    rate     = [p for p in projection if p['is_rate']]

    if counting:
        rows = []
        for p in counting:
            label = p['projected_result']
            diff  = abs((p['my_projected'] or 0) - (p['opp_projected'] or 0))
            if label == 'WIN'  and diff <= CLOSE_THRESHOLDS.get(p['name'], 3):
                label += ' (close)'
            elif label == 'LOSS' and diff <= CLOSE_THRESHOLDS.get(p['name'], 3):
                label += '  ← close!'
            my_rem  = f'+{fmt(p["name"], p["my_remaining"])}'  if p['my_remaining']  is not None else '—'
            opp_rem = f'+{fmt(p["name"], p["opp_remaining"])}' if p['opp_remaining'] is not None else '—'
            rows.append([
                p['name'],
                fmt(p['name'], p['my_current']),  my_rem,  fmt(p['name'], p['my_projected']),
                fmt(p['name'], p['opp_current']), opp_rem, fmt(p['name'], p['opp_projected']),
                label,
            ])
        print(tabulate(rows,
                       headers=['Cat', 'You', '+Proj', '=Total', 'Opp', '+Proj', '=Total', 'Projected'],
                       tablefmt='simple'))

    if rate:
        print('\n  Rate stats (current only):')
        rows = []
        for p in rate:
            rows.append([p['name'],
                         fmt(p['name'], p['my_projected']),
                         fmt(p['name'], p['opp_projected']),
                         p['projected_result']])
        print(tabulate(rows, headers=['Cat', 'You', 'Opp', 'Status'], tablefmt='simple'))

    wins   = sum(1 for p in projection if p['projected_result'] == 'WIN')
    losses = sum(1 for p in projection if p['projected_result'] == 'LOSS')
    pushes = sum(1 for p in projection if p['projected_result'] == 'PUSH')
    tag = 'WIN' if wins > losses else ('LOSE' if losses > wins else 'TIE')
    print(f'\n  Projected: {wins}–{losses}–{pushes}  ({tag} the matchup)')


def show_drops(drops):
    section('Drop Candidates (lowest projected value first)')
    if not drops:
        print('\n  No obvious drop candidates.')
        return

    rows = []
    for c in drops:
        p = c['player']
        status = 'BENCH' if c['is_bench'] else 'STARTER'
        inj = f'  ({c["injury"]})' if c['injury'] else ''
        elig = c.get('eligible', p.position)
        scarce_tag = ' ⚠' if c.get('sole_eligible') else ''
        rows.append([p.name + inj, elig, c['lineup_slot'], p.proTeam, status,
                     f'{c["value"]:.1f}', scarce_tag])
    print(tabulate(rows, headers=['Player', 'Eligible', 'Slot', 'Team', 'Status', 'Value', ''],
                   tablefmt='simple'))
    print('  ⚠ = sole eligible player for a starting slot (risky to drop)')


def show_closer_targets(targets, categories):
    section('Closer Targets  (save-role verified by SV pace)')
    cat_names = {c['name'] for c in categories}
    has_ros = any(r.get('ros_sv') is not None for r in targets)

    rows = []
    for i, rec in enumerate(targets, 1):
        p = rec['player']
        proj = rec['proj']
        pace_str = f"{rec['sv_pace']:.2f}/G"
        row = [i, p.name, p.proTeam, rec['eligible'],
               f'{p.percent_owned:.0f}%',
               fmt('SV', rec['sv_ytd']), pace_str]
        if has_ros:
            ros = rec.get('ros_sv')
            row.append(fmt('SV', ros) if ros is not None else '?')
        for c in ('ERA', 'WHIP', 'K/9'):
            if c in cat_names:
                row.append(fmt(c, proj.get(c)))
        rows.append(row)

    headers = ['#', 'Player', 'Team', 'Eligible', 'Own%', 'YTD SV', 'SV/G']
    if has_ros:
        headers.append('ROS SV')
    for c in ('ERA', 'WHIP', 'K/9'):
        if c in cat_names:
            headers.append(c)
    print(tabulate(rows, headers=headers, tablefmt='simple'))
    print('\n  Ranked by SV pace + ROS projection. Add to target losing SV/SVHD/HLD.')


def _platoon_label(bats, throws):
    if not bats or not throws:
        return '?'
    if bats == 'S':
        return f'S/{throws}'
    return f'{bats}v{throws}'


_SLOT_ORDER = ['C', '1B', '2B', '3B', 'SS', 'MI', 'CI', 'IF',
               'LF', 'CF', 'RF', 'OF', 'DH', 'UTIL']


def _slot_sort_key(slot):
    return _SLOT_ORDER.index(slot) if slot in _SLOT_ORDER else 99


def show_daily_lineup(daily, today):
    section(f"Today's Lineup — {today.isoformat()}")

    lineup    = sorted(daily['lineup'], key=lambda e: _slot_sort_key(e['slot']))
    bench     = daily['bench']
    off_days  = daily['off_days']
    sps_today = daily['sps_today']
    sps_off   = daily['sps_off']

    if lineup:
        print('\n  Recommended Starters')
        rows = []
        for entry in lineup:
            p = entry['player']
            m = entry['matchup']
            opp = m.get('opponent', '?')
            home = '' if m.get('is_home') else '@'
            sp = m.get('opp_pitcher', '') or '—'
            hand = _platoon_label(entry['bat_hand'], entry['pitch_hand'])
            curr = p.lineupSlot
            name = p.name if curr == entry['slot'] else f'{p.name}  ← from {curr}'
            rows.append([
                entry['slot'], name, p.proTeam,
                f'{home}{opp}', sp, hand,
                f'{entry["base"]:.1f}',
                f'{entry["platoon"]:.2f}',
                f'{entry["form"]:.2f}',
                f'{entry["park"]:.2f}',
                f'{entry["score"]:.2f}',
            ])
        print(tabulate(rows,
                       headers=['Slot', 'Player', 'Team', 'Opp', 'Opp SP',
                                'Hand', 'Base', 'Plt', 'Form', 'Park', 'Score'],
                       tablefmt='simple'))

    # Bench — players with games today but not in the recommended lineup
    bench_with_game = [b for b in bench if b.get('has_game')]
    if bench_with_game:
        print('\n  Sit / Bench (game today but not in suggested lineup)')
        rows = []
        for entry in bench_with_game:
            p = entry['player']
            m = entry['matchup']
            opp = m.get('opponent', '?')
            home = '' if m.get('is_home') else '@'
            sp = m.get('opp_pitcher', '') or '—'
            hand = _platoon_label(entry['bat_hand'], entry['pitch_hand'])
            elig = entry.get('eligible', p.position)
            rows.append([
                p.name, p.proTeam, elig,
                f'{home}{opp}', sp, hand,
                f'{entry["score"]:.2f}',
                p.lineupSlot,
            ])
        print(tabulate(rows,
                       headers=['Player', 'Team', 'Eligible', 'Opp', 'Opp SP',
                                'Hand', 'Score', 'Curr'],
                       tablefmt='simple'))

    if off_days:
        print('\n  Off Day (no game scheduled today)')
        rows = []
        for entry in off_days:
            p = entry['player']
            rows.append([p.name, p.proTeam, entry.get('eligible', p.position),
                         p.lineupSlot])
        print(tabulate(rows,
                       headers=['Player', 'Team', 'Eligible', 'Curr'],
                       tablefmt='simple'))

    if sps_today:
        print('\n  Pitchers Starting Today')
        rows = []
        for sp in sps_today:
            p = sp['player']
            home = '' if sp['is_home'] else '@'
            rows.append([p.name, p.proTeam, sp['eligible'],
                         f'{home}{sp["opponent"]}', sp['venue'] or '?',
                         p.lineupSlot])
        print(tabulate(rows,
                       headers=['Player', 'Team', 'Eligible', 'Opp', 'Venue', 'Curr'],
                       tablefmt='simple'))

    if sps_off:
        non_probable = [sp for sp in sps_off if sp['player'].position == 'SP']
        if non_probable:
            print('\n  SPs not starting today (team plays but they aren\'t the probable)')
            rows = []
            for sp in non_probable:
                p = sp['player']
                rows.append([p.name, p.proTeam, p.lineupSlot])
            print(tabulate(rows,
                           headers=['Player', 'Team', 'Curr'],
                           tablefmt='simple'))

    rps_today = daily.get('rps_with_game', [])
    rps_dark  = daily.get('rps_off', [])
    if rps_today or rps_dark:
        print('\n  Relievers — Team Schedule Today')
        rows = []
        for rp in rps_today:
            p = rp['player']
            home = '' if rp['is_home'] else '@'
            rows.append([p.name, p.proTeam, p.lineupSlot,
                         f'{home}{rp["opponent"]}', ''])
        for rp in rps_dark:
            p = rp['player']
            note = '← consider sitting (no game)' if p.lineupSlot in ('RP', 'P') else ''
            rows.append([p.name, p.proTeam, p.lineupSlot, 'OFF', note])
        print(tabulate(rows,
                       headers=['Player', 'Team', 'Curr', 'Game', ''],
                       tablefmt='simple'))

    print('\n  Score = (per-game projection) × platoon × recent form × park factor.')


def show_streaming_queue(queue):
    section('Streaming Queue  (FA starters with 2+ starts this week)')
    rows = []
    for i, rec in enumerate(queue, 1):
        p = rec['player']
        proj = rec['proj']
        starts = rec['starts_remaining']
        rows.append([
            i, p.name, p.proTeam, rec.get('eligible', p.position),
            f'{p.percent_owned:.0f}%', str(starts),
            fmt('ERA',  proj.get('ERA')),
            fmt('WHIP', proj.get('WHIP')),
            fmt('K/9',  proj.get('K/9')),
            fmt('K',    proj.get('K')),
        ])
    print(tabulate(rows,
                   headers=['#', 'Player', 'Team', 'Elig', 'Own%', 'Starts',
                             'ERA', 'WHIP', 'K/9', 'K'],
                   tablefmt='simple'))
    print('\n  Sorted: most starts first, then quality. Schedule-based — ignores category gaps.')


def parse_args():
    p = argparse.ArgumentParser(description='ESPN H2H Categories helper with optional FanGraphs (pybaseball) stats.')
    p.add_argument(
        '--source',
        choices=('all', 'espn', 'fangraphs', 'steamer'),
        default='all',
        help='all: ESPN + FanGraphs + Steamer ROS + overlaps | espn | fangraphs | steamer',
    )
    p.add_argument(
        '--proj-system',
        choices=tuple(PROJECTION_SYSTEMS),
        default='steamer',
        dest='proj_system',
        help='Projection system to use with --source steamer/all (default: steamer)',
    )
    p.add_argument(
        '--scout',
        metavar='TEAM',
        default=None,
        help='Scout any team by name and show their roster with projections (partial match supported)',
    )
    p.add_argument(
        '--no-cache',
        action='store_true',
        dest='no_cache',
        help='Bypass disk cache and fetch all external data fresh',
    )
    p.add_argument(
        '--verbose',
        action='store_true',
        dest='verbose',
        help='Show detailed output (rosters, loading messages, plate discipline, Savant signals)',
    )
    return p.parse_args()


def main():
    args = parse_args()
    try:
        import config_private as cfg
        LEAGUE_ID = cfg.LEAGUE_ID
        YEAR = cfg.YEAR
        ESPN_S2 = cfg.ESPN_S2
        SWID = cfg.SWID
        TEAM_NAME = cfg.TEAM_NAME
        fg_year = getattr(cfg, 'FANGRAPHS_SEASON', YEAR)
    except ImportError:
        print('Error: config_private.py not found.')
        print('Copy config_example.py to config_private.py and fill in your values.')
        sys.exit(1)

    if args.verbose:
        print()
        print('╔══════════════════════════════════════════════════════════════╗')
        print('║         Fantasy Baseball — H2H Categories Helper           ║')
        print('╚══════════════════════════════════════════════════════════════╝')

        from cache import _CACHE_DIR
        if args.no_cache:
            print('\n  Cache disabled (--no-cache)')
        else:
            print(f'\n  Cache dir:  {_CACHE_DIR}')

        print('\n  Connecting to ESPN …')
    client = LeagueClient(LEAGUE_ID, YEAR, ESPN_S2, SWID)

    team = client.get_team(TEAM_NAME)
    if not team:
        print(f'\n  Could not find team "{TEAM_NAME}". Available teams:')
        for tid, tname in client.get_all_team_names():
            print(f'    {tid}: {tname}')
        sys.exit(1)

    categories = client.get_scoring_categories()
    stat_weights = client.get_stat_weights()
    bat_names = [c['name'] for c in categories if c['is_batting']]
    pit_names = [c['name'] for c in categories if c['is_pitching']]

    if args.verbose:
        print(f'  League:     {client.league.settings.name}')
        print(f'  Team:       {team.team_name}')
        print(f'  Format:     H2H Each Category')
        print(f'  Rec source: {args.source}')
        print(f'  Batting:    {", ".join(bat_names)}')
        print(f'  Pitching:   {", ".join(pit_names)}')

        print('\n  Loading roster slot configuration …')
    try:
        league_slots = client.get_roster_slots()
        set_league_slots(league_slots)
        slot_summary = {}
        for s in league_slots:
            slot_summary[s] = slot_summary.get(s, 0) + 1
        if args.verbose:
            print(f'  Lineup:     {", ".join(f"{v}x{k}" if v > 1 else k for k, v in slot_summary.items())}')
    except Exception as e:
        if args.verbose:
            print(f'  Could not load roster slots from API ({e}), inferring from roster.')

    matchup_info = client.get_matchup_info()
    matchup = client.get_current_matchup(team)
    scoring_period = matchup_info.get('scoring_period') if matchup_info else None
    days_remaining = matchup_info.get('days_remaining') if matchup_info else None

    # Fallback: ESPN scoring period ID mapping can fail (currentMatchupPeriod
    # advances before the period ends, or period IDs use a different numbering).
    # Estimate from calendar day-of-week when the API returns 0 or a bogus value.
    total_days_reported = matchup_info.get('total_days', 0) if matchup_info else 0
    if (not days_remaining or total_days_reported < 3) and matchup:
        today = datetime.date.today()
        # Most ESPN baseball leagues run Mon-Sun; 7 - weekday gives days left.
        days_remaining = max(1, 7 - today.weekday())
        if matchup_info:
            matchup_info['days_remaining'] = days_remaining
            matchup_info['total_days'] = max(total_days_reported, days_remaining)
        if args.verbose:
            print(f'  ⚠  Scoring period mapping unreliable (ESPN reported {total_days_reported}-day period).')
            print(f'     Using calendar estimate: {days_remaining} days remaining this week.')

    if args.verbose:
        print('\n  Fetching free agents …')
    free_agents = client.get_free_agents(size=200)

    weekly_free_agents = None
    try:
        if args.verbose:
            print('  Fetching weekly projections …')
        weekly_free_agents = client.get_free_agents_with_weekly_projections(size=200)
    except Exception:
        if args.verbose:
            print('  Weekly projections not available, using season projections.')

    roster = team.roster

    # ── MLB schedule context: starts + games remaining this period ─────────
    starts_remaining: dict = {}
    team_games_remaining: dict = {}
    if days_remaining and days_remaining > 0:
        if args.verbose:
            print('\n  Fetching MLB schedule context …')
        try:
            today = datetime.date.today()
            period_end = today + datetime.timedelta(days=days_remaining - 1)
            _ck = f'schedule_{today.isoformat()}_{period_end.isoformat()}'
            _hit = None if args.no_cache else cache_get(_ck, ttl=_SCHEDULE_TTL)
            if _hit is not None:
                starts_remaining, team_games_remaining = _hit
                if args.verbose:
                    print(f'  Schedule:   {len(starts_remaining)} pitchers / '
                          f'{len(team_games_remaining)} teams (cached)')
            else:
                starts_remaining, team_games_remaining = get_schedule_context(today, period_end)
                cache_set(_ck, (starts_remaining, team_games_remaining))
                if args.verbose:
                    print(f'  Schedule:   {len(starts_remaining)} pitchers with known starts, '
                          f'{len(team_games_remaining)} teams with games through {period_end}')
        except Exception as e:
            if args.verbose:
                print(f'  Schedule fetch failed: {e}')

    # ── Today's matchups + player handedness for daily start/sit ──────────
    today = datetime.date.today()
    daily_matchups: dict = {}
    handedness: dict = {}
    try:
        _ck = f'daily_matchups_{today.isoformat()}'
        _hit = None if args.no_cache else cache_get(_ck, ttl=3600)
        if _hit is not None:
            daily_matchups = _hit
        else:
            daily_matchups = get_daily_matchups(today)
            cache_set(_ck, daily_matchups)
    except Exception as e:
        if args.verbose:
            print(f'  Daily matchup fetch failed: {e}')
    try:
        _ck = f'mlb_handedness_{YEAR}'
        _hit = None if args.no_cache else cache_get(_ck, ttl=24 * 3600)
        if _hit is not None:
            handedness = _hit
        else:
            handedness = get_player_handedness(int(YEAR))
            cache_set(_ck, handedness)
    except Exception as e:
        if args.verbose:
            print(f'  Handedness fetch failed: {e}')

    rec = Recommender(
        categories, matchup, roster, free_agents,
        stat_weights=stat_weights,
        scoring_period=scoring_period,
        weekly_free_agents=weekly_free_agents,
        starts_remaining=starts_remaining,
        team_games_remaining=team_games_remaining,
        days_remaining=days_remaining,
    )

    _fg_hit = None
    fg_bat_p = fg_bat_bn = fg_pit_p = fg_pit_bn = None
    if args.source in ('all', 'fangraphs'):
        if args.verbose:
            print(f'\n  Loading FanGraphs leader stats via pybaseball (MLB season {fg_year}) …')
        try:
            _ck = f'fg_leaders_{fg_year}'
            _fg_hit = None if args.no_cache else cache_get(_ck)
            if _fg_hit is not None:
                fg_bat_p, fg_bat_bn, fg_pit_p, fg_pit_bn = _fg_hit
            else:
                fg_bat_p, fg_bat_bn, fg_pit_p, fg_pit_bn = build_fangraphs_lookups(int(fg_year))
                cache_set(_ck, (fg_bat_p, fg_bat_bn, fg_pit_p, fg_pit_bn))
        except Exception as e:
            if args.verbose:
                print(f'  FanGraphs load failed: {e}')
            if args.source == 'fangraphs':
                sys.exit(2)
            fg_bat_p = fg_bat_bn = fg_pit_p = fg_pit_bn = None

    fg_h = fg_p = None
    if fg_bat_p is not None:
        fg_h, fg_p = rec.split_free_agents_fangraphs(fg_bat_p, fg_bat_bn, fg_pit_p, fg_pit_bn)
        if args.verbose:
            _tag = ' (cached)' if _fg_hit is not None else ''
            print(f'  FanGraphs YTD: {len(fg_bat_p)} batters / {len(fg_pit_p)} pitchers{_tag} '
                  f'→ {len(fg_h)} FA hitters / {len(fg_p)} FA pitchers matched')

    # ── Steamer / ZiPS / THE BAT ROS projections ──────────────────────────
    _st_hit = None
    st_bat_p = st_bat_bn = st_pit_p = st_pit_bn = None
    proj_label = args.proj_system.title()
    if args.source in ('all', 'steamer'):
        if args.verbose:
            print(f'\n  Loading {proj_label} ROS projections from FanGraphs …')
        try:
            _ck = f'fg_proj_{args.proj_system}_{fg_year}'
            _st_hit = None if args.no_cache else cache_get(_ck, ttl=8 * 3600)
            if _st_hit is not None:
                st_bat_p, st_bat_bn, st_pit_p, st_pit_bn = _st_hit
            else:
                st_bat_p, st_bat_bn, st_pit_p, st_pit_bn = build_projection_lookups(args.proj_system)
                cache_set(_ck, (st_bat_p, st_bat_bn, st_pit_p, st_pit_bn))
        except Exception as e:
            if args.verbose:
                print(f'  {proj_label} load failed: {e}')
            if args.source == 'steamer':
                sys.exit(2)

    st_h = st_p = None
    if st_bat_p is not None:
        st_h, st_p = rec.split_free_agents_fangraphs(st_bat_p, st_bat_bn, st_pit_p, st_pit_bn)
        if args.verbose:
            _tag = ' (cached)' if _st_hit is not None else ''
            print(f'  {proj_label}:   {len(st_bat_p)} batters / {len(st_pit_p)} pitchers{_tag} '
                  f'→ {len(st_h)} FA hitters / {len(st_p)} FA pitchers matched')

    # ── Baseball Savant xStats ─────────────────────────────────────────────
    savant_bat = savant_pit = None
    if args.source == 'all':
        if args.verbose:
            print('\n  Loading Baseball Savant xStats …')
        try:
            _ck = f'savant_{fg_year}'
            _hit = None if args.no_cache else cache_get(_ck)
            if _hit is not None:
                savant_bat, savant_pit = _hit
                if args.verbose:
                    print(f'  Savant:        {len(savant_bat)} batters / {len(savant_pit)} pitchers (cached)')
            else:
                savant_bat, savant_pit = build_savant_lookups(int(fg_year))
                cache_set(_ck, (savant_bat, savant_pit))
                if args.verbose:
                    print(f'  Savant:        {len(savant_bat)} batters / {len(savant_pit)} pitchers loaded')
        except Exception as e:
            if args.verbose:
                print(f'  Savant load failed: {e}')

    # ── Recent form (L14 FanGraphs) ────────────────────────────────────────
    _rec_hit = None
    rec_bat_p = rec_bat_bn = rec_pit_p = rec_pit_bn = None
    if args.source == 'all':
        if args.verbose:
            print('\n  Loading recent form (L14 FanGraphs) …')
        try:
            _ck = f'fg_recent_{fg_year}_14d'
            _rec_hit = None if args.no_cache else cache_get(_ck)
            if _rec_hit is not None:
                rec_bat_p, rec_bat_bn, rec_pit_p, rec_pit_bn = _rec_hit
            else:
                rec_bat_p, rec_bat_bn, rec_pit_p, rec_pit_bn = build_recent_splits_lookups(
                    int(fg_year), days=14)
                cache_set(_ck, (rec_bat_p, rec_bat_bn, rec_pit_p, rec_pit_bn))
        except Exception as e:
            if args.verbose:
                print(f'  Recent splits load failed: {e}')

    recent_h = recent_p = None
    if rec_bat_p is not None:
        recent_h, recent_p = rec.split_free_agents_fangraphs(
            rec_bat_p, rec_bat_bn, rec_pit_p, rec_pit_bn)
        if args.verbose:
            _tag = ' (cached)' if _rec_hit is not None else ''
            print(f'  Recent (L14):  {len(rec_bat_p)} batters / {len(rec_pit_p)} pitchers{_tag} '
                  f'→ {len(recent_h)} FA hitters / {len(recent_p)} FA pitchers matched')

    # ── Plate discipline (FanGraphs SwStr% / Contact%) ────────────────────
    _disc_hit = None
    bat_disc = pit_disc = None
    if args.source == 'all':
        if args.verbose:
            print('\n  Loading plate discipline data …')
        try:
            _ck = f'fg_disc_{fg_year}'
            _disc_hit = None if args.no_cache else cache_get(_ck)
            if _disc_hit is not None:
                bat_disc, pit_disc = _disc_hit
                if args.verbose:
                    print(f'  Plate discipline: loaded from cache')
            else:
                bat_disc, pit_disc = build_plate_discipline_lookups(int(fg_year))
                cache_set(_ck, (bat_disc, pit_disc))
        except Exception as e:
            if args.verbose:
                print(f'  Plate discipline load failed: {e}')

    # ── Compute all recs before any display ───────────────────────────────
    analysis = rec.analyze_matchup() if matchup else None
    opp = matchup.get('opponent') if matchup else None
    proj_outcome: list = []
    opp_name = ''
    if opp and hasattr(opp, 'roster'):
        proj_outcome = rec.project_week_end(opp.roster)
        opp_name = opp.team_name if hasattr(opp, 'team_name') else str(opp)

    weekly_espn = weekly_fg = weekly_st = weekly_recent = None
    if analysis:
        if args.source in ('all', 'espn'):
            weekly_espn = rec.get_weekly_recommendations(analysis)
        if args.source in ('all', 'fangraphs') and fg_h is not None:
            weekly_fg = rec.get_weekly_recommendations(analysis, hitters=fg_h, pitchers=fg_p)
        if args.source in ('all', 'steamer') and st_h is not None:
            weekly_st = rec.get_weekly_recommendations(analysis, hitters=st_h, pitchers=st_p)
        if args.source == 'all' and recent_h is not None:
            weekly_recent = rec.get_weekly_recommendations(
                analysis, hitters=recent_h, pitchers=recent_p)

    season_espn = season_fg = season_st = None
    if args.source in ('all', 'espn'):
        season_espn = rec.get_season_recommendations()
    if args.source in ('all', 'fangraphs') and fg_h is not None:
        season_fg = rec.get_season_recommendations(hitters=fg_h, pitchers=fg_p)
    if args.source in ('all', 'steamer') and st_h is not None:
        season_st = rec.get_season_recommendations(hitters=st_h, pitchers=st_p)

    savant_signals = None
    if savant_bat is not None:
        savant_signals = get_savant_signals(
            free_agents, roster, savant_bat, savant_pit,
            starts_remaining=starts_remaining,
        )

    # ── Display ────────────────────────────────────────────────────────────
    weekly_sources = [
        ('ESPN',   weekly_espn),
        ('FG',     weekly_fg),
        ('Steamer', weekly_st),
        ('L14',    weekly_recent),
    ]

    if args.verbose:
        show_roster(roster, categories)

    # Daily start/sit — uses ROS projection (preferred) or ESPN season fallback.
    season_lookup = None
    if st_bat_p is not None:
        season_lookup = (st_bat_p, st_bat_bn, st_pit_p, st_pit_bn)
    elif fg_bat_p is not None:
        season_lookup = (fg_bat_p, fg_bat_bn, fg_pit_p, fg_pit_bn)
    recent_lookup = None
    if rec_bat_p is not None:
        recent_lookup = (rec_bat_p, rec_bat_bn, rec_pit_p, rec_pit_bn)

    if daily_matchups:
        try:
            daily = recommend_daily_lineup(
                roster=roster,
                daily_matchups=daily_matchups,
                handedness=handedness,
                season_lookup=season_lookup,
                recent_lookup=recent_lookup,
                starting_slots=get_starting_slots(roster),
                batting_cats=[c for c in categories if c['is_batting']],
                pitching_cats=[c for c in categories if c['is_pitching']],
                stat_weights=stat_weights,
            )
            show_daily_lineup(daily, today)
        except Exception as e:
            print(f'\n  Daily lineup recommendation failed: {e}')

    if args.scout:
        _scout_lower = args.scout.lower()
        _scout_team = client.get_team(args.scout)
        if not _scout_team:
            for _t in client.league.teams:
                if _scout_lower in _t.team_name.lower():
                    _scout_team = _t
                    break
        if _scout_team:
            show_opponent_roster(_scout_team, categories)
        else:
            print(f'\n  Scout: team "{args.scout}" not found. Available teams:')
            for _tid, _tname in client.get_all_team_names():
                print(f'    {_tid}: {_tname}')

    if matchup:
        show_matchup(analysis, matchup, matchup_info)
        if opp and hasattr(opp, 'roster'):
            if args.verbose:
                show_opponent_roster(opp, categories)
            show_projected_outcome(proj_outcome, opp_name, days_remaining)
    else:
        print('\n  No active matchup found. Skipping weekly pickup sections.')

    if analysis:
        if args.source in ('all', 'espn') and _has_recs(weekly_espn):
            show_weekly(weekly_espn, categories, 'ESPN projections',
                        days_remaining=days_remaining)
        if args.source in ('all', 'fangraphs') and _has_recs(weekly_fg):
            show_weekly(weekly_fg, categories, 'FanGraphs (pybaseball) season stats',
                        days_remaining=days_remaining)
        if args.source in ('all', 'steamer') and _has_recs(weekly_st):
            show_weekly(weekly_st, categories, f'{proj_label} ROS projections',
                        days_remaining=days_remaining)
        if args.source == 'all' and _has_recs(weekly_recent):
            show_weekly(weekly_recent, categories, 'Recent Form — last 14 days (FanGraphs)',
                        days_remaining=days_remaining)

        if args.source == 'all' and weekly_espn and weekly_fg:
            ch, cp = consensus_pickups(
                weekly_espn['hitters'], weekly_espn['pitchers'],
                weekly_fg['hitters'], weekly_fg['pitchers'],
            )
            if ch or cp:
                show_consensus_weekly(ch, cp, categories)

        if args.source == 'all' and weekly_espn and weekly_st:
            ch, cp = consensus_pickups(
                weekly_espn['hitters'], weekly_espn['pitchers'],
                weekly_st['hitters'], weekly_st['pitchers'],
            )
            if ch or cp:
                show_consensus_weekly(ch, cp, categories,
                                      title=f'ESPN ∩ {proj_label} ROS (both top lists)')

    # Streaming queue — schedule-based; show whenever starts data is available
    streaming_queue = rec.get_streaming_queue(
        pitchers=st_p if st_p is not None else fg_p
    )
    if streaming_queue:
        show_streaming_queue(streaming_queue)

    # Closer targets — only fires when SV/SVHD/HLD is a losing category
    if analysis:
        closer_targets = rec.get_closer_targets(
            analysis, ytd_pitchers=fg_p, ros_pitchers=st_p
        )
        if closer_targets:
            show_closer_targets(closer_targets, categories)

    show_drops(rec.get_drop_candidates())

    if args.source in ('all', 'espn') and _has_recs(season_espn):
        show_season(season_espn, categories, 'ESPN projections')
    if args.source in ('all', 'fangraphs') and _has_recs(season_fg):
        show_season(season_fg, categories, 'FanGraphs (pybaseball) season stats')
    if args.source in ('all', 'steamer') and _has_recs(season_st):
        show_season(season_st, categories, f'{proj_label} ROS projections')

    if args.source == 'all' and season_espn and season_fg:
        sh, sp = consensus_pickups(
            season_espn['hitters'], season_espn['pitchers'],
            season_fg['hitters'], season_fg['pitchers'],
        )
        if sh or sp:
            show_consensus_season(sh, sp, categories)

    if args.source == 'all' and season_espn and season_st:
        sh, sp = consensus_pickups(
            season_espn['hitters'], season_espn['pitchers'],
            season_st['hitters'], season_st['pitchers'],
        )
        if sh or sp:
            show_consensus_season(sh, sp, categories,
                                  title=f'ESPN ∩ {proj_label} ROS (both top lists)')

    if args.verbose and bat_disc is not None and pit_disc is not None:
        show_plate_discipline(free_agents, pit_disc, bat_disc, categories)

    if args.verbose and savant_signals is not None:
        show_savant_signals(savant_signals)

    show_summary(analysis, matchup, proj_outcome, opp_name, weekly_sources, savant_signals)

    print()


if __name__ == '__main__':
    main()
