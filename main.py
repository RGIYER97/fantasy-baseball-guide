import sys
from tabulate import tabulate

from league_client import LeagueClient
from recommender import Recommender

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


def section(title):
    print()
    print('=' * 64)
    print(f'  {title}')
    print('=' * 64)


# ── Matchup status ───────────────────────────────────────────────

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


# ── Roster overview ──────────────────────────────────────────────

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


# ── Weekly recommendations ───────────────────────────────────────

def show_weekly(weekly, categories):
    section('Weekly Pickup Recommendations')

    losing = weekly.get('losing_categories', [])
    if not losing:
        print('\n  You are not losing any categories — no pickups needed this week!')
        return

    print(f'\n  Targeting categories: {", ".join(losing)}')

    bat_cats = [c['name'] for c in categories if c['is_batting']]
    pit_cats = [c['name'] for c in categories if c['is_pitching']]

    _show_player_table('Hitter', weekly['hitters'], bat_cats)
    _show_player_table('Pitcher', weekly['pitchers'], pit_cats)


def show_season(season, categories):
    section('Season-Long Pickup Recommendations')

    bat_cats = [c['name'] for c in categories if c['is_batting']]
    pit_cats = [c['name'] for c in categories if c['is_pitching']]

    _show_player_table('Hitter', season['hitters'], bat_cats)
    _show_player_table('Pitcher', season['pitchers'], pit_cats)


def _show_player_table(label, recs, cat_names):
    if not recs:
        print(f'\n  No {label.lower()} recommendations.')
        return

    show_cats = cat_names[:7]
    print(f'\n  Top {label} Pickups')
    rows = []
    for i, rec in enumerate(recs, 1):
        p = rec['player']
        proj = p.stats.get(0, {}).get('projected_breakdown', {})
        row = [i, p.name, p.proTeam, p.position, f'{p.percent_owned:.0f}%']
        for c in show_cats:
            row.append(fmt(c, proj.get(c)))
        row.append(rec['score'])
        rows.append(row)
    print(tabulate(rows, headers=['#', 'Player', 'Team', 'Pos', 'Own%'] + show_cats + ['Score'],
                   tablefmt='simple'))


# ── Drop candidates ──────────────────────────────────────────────

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


# ── Main ─────────────────────────────────────────────────────────

def main():
    try:
        from config_private import LEAGUE_ID, YEAR, ESPN_S2, SWID, TEAM_NAME
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
    bat_names = [c['name'] for c in categories if c['is_batting']]
    pit_names = [c['name'] for c in categories if c['is_pitching']]

    print(f'  League:     {client.league.settings.name}')
    print(f'  Team:       {team.team_name}')
    print(f'  Format:     H2H Each Category')
    print(f'  Batting:    {", ".join(bat_names)}')
    print(f'  Pitching:   {", ".join(pit_names)}')

    matchup_info = client.get_matchup_info()
    matchup = client.get_current_matchup(team)

    print('\n  Fetching free agents …')
    free_agents = client.get_free_agents(size=200)
    roster = team.roster

    rec = Recommender(categories, matchup, roster, free_agents)

    # ── Roster overview
    show_roster(roster, categories)

    if matchup:
        analysis = rec.analyze_matchup()
        show_matchup(analysis, matchup, matchup_info)
        weekly = rec.get_weekly_recommendations(analysis)
        show_weekly(weekly, categories)
    else:
        print('\n  No active matchup found — showing season recommendations only.')
        analysis = None

    # ── Drop candidates & season recs (always shown)
    drops = rec.get_drop_candidates()
    show_drops(drops)

    season = rec.get_season_recommendations()
    show_season(season, categories)

    print()


if __name__ == '__main__':
    main()
