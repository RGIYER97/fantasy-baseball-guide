import argparse
import sys
from tabulate import tabulate

from league_client import LeagueClient
from pybaseball_stats import build_fangraphs_lookups
from recommender import Recommender, consensus_pickups
from roster import eligible_display, positional_scarcity, set_league_slots

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


def show_weekly(weekly, categories, subtitle=''):
    title = 'Weekly Pickup Recommendations'
    if subtitle:
        title = f'{title} — {subtitle}'
    section(title)

    losing = weekly.get('losing_categories', [])
    if not losing:
        print('\n  You are not losing any categories — no pickups needed this week!')
        return

    proj_src = weekly.get('projection_source', 'season')
    src_label = 'weekly (matchup period)' if proj_src == 'weekly' else 'full-season'
    print(f'\n  Targeting categories: {", ".join(losing)}')
    print(f'  Projection basis:    {src_label}')

    bat_cats = [c['name'] for c in categories if c['is_batting']]
    pit_cats = [c['name'] for c in categories if c['is_pitching']]

    _show_player_table('Hitter', weekly['hitters'], bat_cats)
    _show_player_table('Pitcher', weekly['pitchers'], pit_cats)
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


def _show_player_table(label, recs, cat_names):
    if not recs:
        print(f'\n  No {label.lower()} recommendations.')
        return

    show_cats = cat_names[:7]
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
            delta_row = ['', f'  → drop: {drop_str}', '', '', ''] + delta_parts + ['']
            rows.append(delta_row)

    print(tabulate(rows, headers=['#', 'Player', 'Team', 'Eligible', 'Own%'] + show_cats + ['Score'],
                   tablefmt='simple'))


def show_consensus_weekly(h_cons, p_cons, categories):
    section('Weekly — ESPN ∩ FanGraphs (both top lists)')
    bat_cats = [c['name'] for c in categories if c['is_batting']]
    pit_cats = [c['name'] for c in categories if c['is_pitching']]
    _show_consensus_table('Hitter', h_cons, bat_cats)
    _show_consensus_table('Pitcher', p_cons, pit_cats)


def show_consensus_season(h_cons, p_cons, categories):
    section('Season — ESPN ∩ FanGraphs (both top lists)')
    bat_cats = [c['name'] for c in categories if c['is_batting']]
    pit_cats = [c['name'] for c in categories if c['is_pitching']]
    _show_consensus_table('Hitter', h_cons, bat_cats)
    _show_consensus_table('Pitcher', p_cons, pit_cats)


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


def parse_args():
    p = argparse.ArgumentParser(description='ESPN H2H Categories helper with optional FanGraphs (pybaseball) stats.')
    p.add_argument(
        '--source',
        choices=('all', 'espn', 'fangraphs', 'both'),
        default='all',
        help='all: ESPN + FanGraphs + overlap | espn | fangraphs | both: overlap only',
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

    print()
    print('╔══════════════════════════════════════════════════════════════╗')
    print('║         Fantasy Baseball — H2H Categories Helper           ║')
    print('╚══════════════════════════════════════════════════════════════╝')

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
        print(f'  Lineup:     {", ".join(f"{v}x{k}" if v > 1 else k for k, v in slot_summary.items())}')
    except Exception as e:
        print(f'  Could not load roster slots from API ({e}), inferring from roster.')

    matchup_info = client.get_matchup_info()
    matchup = client.get_current_matchup(team)
    scoring_period = matchup_info.get('scoring_period') if matchup_info else None

    print('\n  Fetching free agents …')
    free_agents = client.get_free_agents(size=200)

    weekly_free_agents = None
    try:
        print('  Fetching weekly projections …')
        weekly_free_agents = client.get_free_agents_with_weekly_projections(size=200)
    except Exception:
        print('  Weekly projections not available, using season projections.')

    roster = team.roster

    rec = Recommender(
        categories, matchup, roster, free_agents,
        stat_weights=stat_weights,
        scoring_period=scoring_period,
        weekly_free_agents=weekly_free_agents,
    )

    fg_bat_p = fg_bat_bn = fg_pit_p = fg_pit_bn = None
    if args.source in ('all', 'fangraphs', 'both'):
        print(f'\n  Loading FanGraphs leader stats via pybaseball (MLB season {fg_year}) …')
        try:
            fg_bat_p, fg_bat_bn, fg_pit_p, fg_pit_bn = build_fangraphs_lookups(int(fg_year))
        except Exception as e:
            print(f'  FanGraphs load failed: {e}')
            if args.source in ('fangraphs', 'both'):
                sys.exit(2)
            fg_bat_p = fg_bat_bn = fg_pit_p = fg_pit_bn = None

    fg_h = fg_p = None
    if fg_bat_p is not None:
        fg_h, fg_p = rec.split_free_agents_fangraphs(fg_bat_p, fg_bat_bn, fg_pit_p, fg_pit_bn)

    show_roster(roster, categories)

    analysis = None
    if matchup:
        analysis = rec.analyze_matchup()
        show_matchup(analysis, matchup, matchup_info)

    weekly_espn = weekly_fg = None
    if analysis:
        if args.source in ('all', 'espn', 'both'):
            weekly_espn = rec.get_weekly_recommendations(analysis)
        if args.source in ('all', 'fangraphs', 'both') and fg_h is not None:
            weekly_fg = rec.get_weekly_recommendations(analysis, hitters=fg_h, pitchers=fg_p)

        if args.source in ('all', 'espn') and weekly_espn:
            show_weekly(weekly_espn, categories, 'ESPN projections')

        if args.source in ('all', 'fangraphs') and weekly_fg:
            show_weekly(weekly_fg, categories, 'FanGraphs (pybaseball) season stats')

        if args.source in ('all', 'both') and weekly_espn and weekly_fg:
            ch, cp = consensus_pickups(
                weekly_espn['hitters'], weekly_espn['pitchers'],
                weekly_fg['hitters'], weekly_fg['pitchers'],
            )
            show_consensus_weekly(ch, cp, categories)
    else:
        print('\n  No active matchup found. Skipping weekly pickup sections.')

    show_drops(rec.get_drop_candidates())

    season_espn = season_fg = None
    if args.source in ('all', 'espn', 'both'):
        season_espn = rec.get_season_recommendations()
    if args.source in ('all', 'fangraphs', 'both') and fg_h is not None:
        season_fg = rec.get_season_recommendations(hitters=fg_h, pitchers=fg_p)

    if args.source in ('all', 'espn') and season_espn:
        show_season(season_espn, categories, 'ESPN projections')

    if args.source in ('all', 'fangraphs') and season_fg:
        show_season(season_fg, categories, 'FanGraphs (pybaseball) season stats')

    if args.source in ('all', 'both') and season_espn and season_fg:
        sh, sp = consensus_pickups(
            season_espn['hitters'], season_espn['pitchers'],
            season_fg['hitters'], season_fg['pitchers'],
        )
        show_consensus_season(sh, sp, categories)

    print()


if __name__ == '__main__':
    main()
