from espn_api.baseball import League
from espn_api.baseball.constant import STATS_MAP

BATTING_STATS = {
    'AB', 'H', 'AVG', '2B', '3B', 'HR', 'XBH', '1B', 'TB', 'SLG',
    'B_BB', 'B_IBB', 'HBP', 'SF', 'SH', 'SAC', 'PA', 'OBP', 'OPS',
    'RC', 'R', 'RBI', 'SB', 'CS', 'SB-CS', 'GDP', 'B_SO', 'PS', 'PPA',
    'CYC', 'G',
}

PITCHING_STATS = {
    'GP', 'GS', 'OUTS', 'TBF', 'P', 'P_H', 'OBA', 'P_BB', 'P_IBB',
    'WHIP', 'OOBP', 'P_R', 'ER', 'P_HR', 'ERA', 'K', 'K/9', 'WP',
    'BLK', 'PK', 'W', 'L', 'WPCT', 'SVO', 'SV', 'BLSV', 'SV%',
    'HLD', 'CG', 'QS', 'NH', 'PG', 'K/BB', 'SVHD', 'STARTER',
}

RATE_STATS = {
    'AVG', 'OBP', 'SLG', 'OPS', 'ERA', 'WHIP', 'K/9', 'K/BB',
    'FPCT', 'SV%', 'WPCT', 'OBA', 'OOBP', 'PPA',
}

INVERSE_STATS = {
    'ERA', 'WHIP', 'OBA', 'OOBP', 'L', 'P_H', 'P_BB', 'P_R', 'ER',
    'P_HR', 'WP', 'BLK', 'B_SO', 'CS', 'GDP', 'BLSV',
}


class LeagueClient:
    def __init__(self, league_id, year, espn_s2, swid):
        self.league = League(
            league_id=league_id,
            year=year,
            espn_s2=espn_s2,
            swid=swid,
        )

    def get_team(self, team_name):
        for team in self.league.teams:
            if team.team_name.lower().strip() == team_name.lower().strip():
                return team
        return None

    def get_all_team_names(self):
        return [(t.team_id, t.team_name) for t in self.league.teams]

    def get_scoring_categories(self):
        """Extract the league's H2H scoring categories from settings."""
        raw = self.league.settings._raw_scoring_settings
        scoring_items = raw.get('scoringItems', [])

        categories = []
        for item in scoring_items:
            stat_id = item.get('statId')
            stat_name = STATS_MAP.get(stat_id, f'STAT_{stat_id}')
            categories.append({
                'stat_id': stat_id,
                'name': stat_name,
                'is_batting': stat_name in BATTING_STATS,
                'is_pitching': stat_name in PITCHING_STATS,
                'is_rate': stat_name in RATE_STATS,
                'is_inverse': stat_name in INVERSE_STATS,
            })
        return categories

    def get_current_matchup(self, team):
        """Return the current week's H2H category matchup for the given team."""
        try:
            box_scores = self.league.box_scores()
        except Exception:
            return None

        for box in box_scores:
            home = box.home_team
            away = box.away_team

            home_id = home.team_id if hasattr(home, 'team_id') else home
            away_id = away.team_id if hasattr(away, 'team_id') else away

            if home_id == team.team_id:
                return {
                    'is_home': True,
                    'my_stats': box.home_stats,
                    'opp_stats': box.away_stats,
                    'my_wins': box.home_wins,
                    'my_losses': box.home_losses,
                    'my_ties': box.home_ties,
                    'opponent': away,
                    'box_score': box,
                }
            if away_id == team.team_id:
                return {
                    'is_home': False,
                    'my_stats': box.away_stats,
                    'opp_stats': box.home_stats,
                    'my_wins': box.away_wins,
                    'my_losses': box.away_losses,
                    'my_ties': box.away_ties,
                    'opponent': home,
                    'box_score': box,
                }
        return None

    def get_free_agents(self, size=150):
        """Return top free agents sorted by ownership percentage."""
        return self.league.free_agents(size=size)

    def get_matchup_info(self):
        """Return metadata about the current matchup period."""
        matchup_map = self.league.settings.matchup_periods
        current_mp = self.league.currentMatchupPeriod
        current_sp = self.league.scoringPeriodId
        periods = matchup_map.get(str(current_mp), [])
        remaining = [p for p in periods if p >= current_sp]
        return {
            'matchup_period': current_mp,
            'scoring_period': current_sp,
            'total_days': len(periods),
            'days_remaining': len(remaining),
        }
