from league_client import INVERSE_STATS, RATE_STATS


class Recommender:
    def __init__(self, categories, matchup, roster, free_agents):
        self.categories = categories
        self.matchup = matchup
        self.roster = roster
        self.free_agents = free_agents
        self.batting_cats = [c for c in categories if c['is_batting']]
        self.pitching_cats = [c for c in categories if c['is_pitching']]

    # ------------------------------------------------------------------
    # Matchup analysis
    # ------------------------------------------------------------------

    def analyze_matchup(self):
        """Break down each category: current values, margins, result."""
        if not self.matchup:
            return []

        my_stats = self.matchup['my_stats'] or {}
        opp_stats = self.matchup['opp_stats'] or {}
        analysis = []

        for cat in self.categories:
            name = cat['name']
            my_val = my_stats.get(name, {}).get('value', 0)
            opp_val = opp_stats.get(name, {}).get('value', 0)
            result = my_stats.get(name, {}).get('result', 'UNDECIDED')

            if cat['is_inverse']:
                margin = opp_val - my_val
            else:
                margin = my_val - opp_val

            analysis.append({
                'name': name,
                'my_value': my_val,
                'opp_value': opp_val,
                'margin': margin,
                'result': result,
                'is_rate': cat['is_rate'],
                'is_inverse': cat['is_inverse'],
                'is_batting': cat['is_batting'],
                'is_pitching': cat['is_pitching'],
            })

        return analysis

    # ------------------------------------------------------------------
    # Weekly recommendations — focus on flipping losing categories
    # ------------------------------------------------------------------

    def get_weekly_recommendations(self, analysis, num=10):
        losing = [a for a in analysis if a['result'] == 'LOSS']
        if not losing:
            return {'hitters': [], 'pitchers': [], 'losing_categories': []}

        losing_bat = [a for a in losing if a['is_batting']]
        losing_pit = [a for a in losing if a['is_pitching']]

        hitters, pitchers = self._split_free_agents()

        hitter_recs = self._rank_for_categories(hitters, losing_bat)
        pitcher_recs = self._rank_for_categories(pitchers, losing_pit)

        return {
            'hitters': hitter_recs[:num],
            'pitchers': pitcher_recs[:num],
            'losing_categories': [a['name'] for a in losing],
        }

    # ------------------------------------------------------------------
    # Season recommendations — overall value across ALL categories
    # ------------------------------------------------------------------

    def get_season_recommendations(self, num=10):
        hitters, pitchers = self._split_free_agents()

        hitter_recs = self._rank_for_categories(hitters, self.batting_cats)
        pitcher_recs = self._rank_for_categories(pitchers, self.pitching_cats)

        return {
            'hitters': hitter_recs[:num],
            'pitchers': pitcher_recs[:num],
        }

    # ------------------------------------------------------------------
    # Drop candidates — roster players with lowest projected value
    # ------------------------------------------------------------------

    def get_drop_candidates(self, num=8):
        candidates = []
        for player in self.roster:
            proj = player.stats.get(0, {}).get('projected_breakdown', {})
            value = self._composite_value(proj, self.categories)
            injury = player.injuryStatus
            if injury == 'ACTIVE':
                injury = None

            candidates.append({
                'player': player,
                'value': value,
                'lineup_slot': player.lineupSlot,
                'is_bench': player.lineupSlot in ('BE', 'IL'),
                'injury': injury,
            })

        candidates.sort(key=lambda c: (not c['is_bench'], c['value']))
        return candidates[:num]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _split_free_agents(self):
        hitters, pitchers = [], []
        for fa in self.free_agents:
            proj = fa.stats.get(0, {}).get('projected_breakdown', {})
            if not proj:
                continue
            if fa.position in ('SP', 'RP', 'P'):
                pitchers.append((fa, proj))
            else:
                hitters.append((fa, proj))
        return hitters, pitchers

    def _rank_for_categories(self, players_with_proj, target_cats):
        """Rank players by a normalized composite score across *target_cats*.

        For each target category we normalise every player's projected value
        to 0‒1 among the pool, then weight by how close the category margin
        is (closer losses are more impactful to target).
        """
        if not target_cats or not players_with_proj:
            return []

        cat_values = {cat['name']: [] for cat in target_cats}
        for _, proj in players_with_proj:
            for cat in target_cats:
                v = proj.get(cat['name'])
                if v is not None:
                    cat_values[cat['name']].append(v)

        cat_ranges = {}
        for name, vals in cat_values.items():
            if vals:
                cat_ranges[name] = (min(vals), max(vals))
            else:
                cat_ranges[name] = (0, 1)

        scored = []
        for fa, proj in players_with_proj:
            score = 0.0
            key_stats = {}
            for cat in target_cats:
                name = cat['name']
                val = proj.get(name)
                if val is None:
                    continue

                key_stats[name] = val
                lo, hi = cat_ranges[name]
                norm = (val - lo) / (hi - lo) if hi != lo else 0.5

                if cat.get('is_inverse'):
                    norm = 1.0 - norm

                margin = abs(cat.get('margin', 0))
                weight = 1.0 / (1.0 + margin * 0.05)

                score += norm * weight

            if score > 0:
                scored.append({
                    'player': fa,
                    'score': round(score, 3),
                    'key_stats': key_stats,
                })

        scored.sort(key=lambda x: x['score'], reverse=True)
        return scored

    def _composite_value(self, proj, cats):
        """Quick scalar value for a player across a set of categories."""
        if not proj:
            return 0.0

        score = 0.0
        for cat in cats:
            name = cat['name']
            val = proj.get(name, 0)
            if not val:
                continue
            if cat['is_rate']:
                if cat['is_inverse']:
                    score += max(0, 5.0 - val) * 10
                else:
                    score += val * 20
            else:
                if cat['is_inverse']:
                    score -= val * 0.1
                else:
                    score += val
        return round(score, 2)
