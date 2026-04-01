import json

from espn_api.baseball import League
from espn_api.baseball.constant import POSITION_MAP, STATS_MAP
from espn_api.baseball.player import Player

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

    def get_stat_weights(self):
        """Points per stat from ESPN league settings (H2H points or category weights).

        H2H category leagues often use 0 in the API; we treat that as weight 1.0 per category.
        """
        raw = self.league.settings._raw_scoring_settings
        scoring_items = raw.get('scoringItems', [])
        weights = {}
        for item in scoring_items:
            stat_id = item.get('statId')
            name = STATS_MAP.get(stat_id)
            if not name:
                continue
            pts = item.get('points')
            if pts is None:
                pts = 0
            try:
                w = float(pts)
            except (TypeError, ValueError):
                w = 0.0
            if w == 0.0:
                w = 1.0
            weights[name] = w
        return weights

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

    def get_roster_slots(self):
        """Return the league's required lineup slots from ESPN settings.

        Returns a list of slot name strings with duplicates for positions
        that have multiple slots (e.g. three 'OF' entries).
        Bench and IL slots are excluded.
        """
        params = {'view': 'mSettings'}
        data = self.league.espn_request.league_get(params=params)
        slot_counts = (
            data.get('settings', {})
            .get('rosterSettings', {})
            .get('lineupSlotCounts', {})
        )

        bench_ids = {
            POSITION_MAP.get('BE', 16),
            POSITION_MAP.get('IL', 17),
        }
        slots = []
        for slot_id_str, count in slot_counts.items():
            slot_id = int(slot_id_str)
            if count <= 0 or slot_id in bench_ids:
                continue
            slot_name = POSITION_MAP.get(slot_id, f'SLOT_{slot_id}')
            slots.extend([slot_name] * count)
        return slots

    def get_free_agents(self, size=150):
        """Return top free agents sorted by ownership percentage."""
        return self.league.free_agents(size=size)

    def get_free_agents_with_weekly_projections(self, size=150):
        """Fetch free agents with current-scoring-period projected stats.

        ESPN's kona_player_info view can be filtered to include stat
        projections for specific scoring periods via x-fantasy-filter.
        Falls back to the normal fetch if the extra data isn't available.
        """
        week = self.league.current_week
        year = self.league.year

        params = {
            'view': 'kona_player_info',
            'scoringPeriodId': week,
        }
        filters = {
            'players': {
                'filterStatus': {'value': ['FREEAGENT', 'WAIVERS']},
                'limit': size,
                'sortPercOwned': {'sortPriority': 1, 'sortAsc': False},
                'sortDraftRanks': {
                    'sortPriority': 100,
                    'sortAsc': True,
                    'value': 'STANDARD',
                },
                'filterStatsForCurrentMatchupPeriod': {
                    'value': True,
                },
            },
        }
        headers = {'x-fantasy-filter': json.dumps(filters)}

        try:
            data = self.league.espn_request.league_get(
                params=params, headers=headers,
            )
            players = data.get('players', [])
            return [Player(p, year) for p in players]
        except Exception:
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
