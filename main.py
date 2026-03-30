import argparse
import sys
from tabulate import tabulate

from league_client import LeagueClient
from pybaseball_stats import build_fangraphs_lookups
from recommender import Recommender, consensus_pickups

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

    hitters, pitchers = [], []
    for p in roster:
        proj = p.stats.get(0, {}).get('projected_breakdown', {})
        inj = f' [{p.injuryStatus}]' if p.injuryStatus and p.injuryStatus != 'ACTIVE' else ''
        if p.position in ('SP', 'RP', 'P'):
            row = [p.lineupSlot, p.name + inj, p.position, p.proTeam]
            for c in pit_show:
                row.append(fmt(c, proj.get(c)))
            pitchers.append(row)
        else:
            row = [p.lineupSlot, p.name + inj, p.position, p.proTeam]
            for c in bat_show:
                row.append(fmt(c, proj.get(c)))
            hitters.append(row)

    if hitters:
        print('\n  Hitters')
        print(tabulate(hitters, headers=['Slot', 'Player', 'Pos', 'Team'] + bat_show,
                       tablefmt='simple'))
    if pitchers:
        print('\n  Pitchers')
        print(tabulate(pitchers, headers=['Slot', 'Player', 'Pos', 'Team'] + pit_show,
                       tablefmt='simple'))


def _proj_for_rec(rec):
    if rec.get('proj'):
        return rec['proj']
    p = rec['player']
    return p.stats.get(0, {}).get('projected_breakdown', {})


def show_weekly(weekly, categories, subtitle=''):
    title = 'Weekly Pickup Recommendations'
    if subtitle:
        title = f'{title} — {subtitle}'
    section(title)

    losing = weekly.get('losing_categories', [])
    if not losing:
        print('\n  You are not losing any categories — no pickups needed this week!')
        return

    print(f'\n  Targeting categories: {", ".join(losing)}')

    bat_cats = [c['name'] for c in categories if c['is_batting']]
    pit_cats = [c['name'] for c in categories if c['is_pitching']]

    _show_player_table('Hitter', weekly['hitters'], bat_cats)
    _show_player_table('Pitcher', weekly['pitchers'], pit_cats)


def show_season(season, categories, subtitle=''):
    title = 'Season-Long Pickup Recommendations'
    if subtitle:
        title = f'{title} — {subtitle}'
    section(title)

    bat_cats = [c['name'] for c in categories if c['is_batting']]
    pit_cats = [c['name'] for c in categories if c['is_pitching']]

    _show_player_table('Hitter', season['hitters'], bat_cats)
    _show_player_table('Pitcher', season['pitchers'], pit_cats)


def _drop_header(recs):
    """Return a formatted string describing the suggested drop, or None."""
    drop_info = recs[0].get('drop') if recs else None
    if not drop_info:
        return None
    dp = drop_info['player']
    slot = 'bench' if drop_info['is_bench'] else drop_info['lineup_slot']
    inj = f', {drop_info["injury"]}' if drop_info.get('injury') else ''
    return f'{dp.name} ({dp.position}, {slot}{inj}, Value {drop_info["value"]:.1f})'


def _show_player_table(label, recs, cat_names):
    if not recs:
        print(f'\n  No {label.lower()} recommendations.')
        return

    show_cats = cat_names[:7]

    drop_str = _drop_header(recs)
    if drop_str:
        print(f'\n  Top {label} Pickups  →  drop: {drop_str}')
    else:
        print(f'\n  Top {label} Pickups')

    rows = []
    for i, rec in enumerate(recs, 1):
        p = rec['player']
        proj = _proj_for_rec(rec)
        row = [i, p.name, p.proTeam, p.position, f'{p.percent_owned:.0f}%']
        for c in show_cats:
            row.append(fmt(c, proj.get(c)))
        row.append(rec['score'])
        rows.append(row)

        deltas = rec.get('category_deltas')
        if deltas:
            delta_row = ['', '  swap Δ', '', '', '']
            for c in show_cats:
                d = deltas.get(c)
                delta_row.append(fmt_delta(c, d) if d is not None else '')
            delta_row.append('')
            rows.append(delta_row)

    print(tabulate(rows, headers=['#', 'Player', 'Team', 'Pos', 'Own%'] + show_cats + ['Score'],
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

    drop_str = _drop_header(recs)
    if drop_str:
        print(f'\n  {label}s in both top lists  →  drop: {drop_str}')
    else:
        print(f'\n  {label}s in both top lists (ESPN stats in table)')

    rows = []
    for i, rec in enumerate(recs, 1):
        p = rec['player']
        proj = rec.get('proj') or _proj_for_rec(rec)
        row = [i, p.name, p.proTeam, rec['espn_rank'], rec['fg_rank'], rec['espn_score'], rec['fg_score'],
               rec['score']]
        for c in show_cats:
            row.append(fmt(c, proj.get(c)))
        rows.append(row)

        deltas = rec.get('category_deltas')
        if deltas:
            delta_row = ['', '  swap Δ', '', '', '', '', '', '']
            for c in show_cats:
                d = deltas.get(c)
                delta_row.append(fmt_delta(c, d) if d is not None else '')
            rows.append(delta_row)

    print(tabulate(rows, headers=['#', 'Player', 'Team', 'E#', 'FG#', 'ESPN△', 'FG△', 'Avg△'] + show_cats,
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
        rows.append([p.name + inj, p.position, c['lineup_slot'], p.proTeam, status,
                     f'{c["value"]:.1f}'])
    print(tabulate(rows, headers=['Player', 'Pos', 'Slot', 'Team', 'Status', 'Value'],
                   tablefmt='simple'))


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

    matchup_info = client.get_matchup_info()
    matchup = client.get_current_matchup(team)

    print('\n  Fetching free agents …')
    free_agents = client.get_free_agents(size=200)
    roster = team.roster

    rec = Recommender(categories, matchup, roster, free_agents, stat_weights=stat_weights)

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
