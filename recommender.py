from pybaseball_stats import lookup_batter, lookup_pitcher

INJURED_STATUSES = frozenset({
    'OUT', 'DAY_TO_DAY', 'SUSPENSION',
    'TEN_DAY_DL', 'FIFTEEN_DAY_DL', 'SIXTY_DAY_DL', 'SEVEN_DAY_DL',
    'INJURED_RESERVE', 'PATERNITY', 'BEREAVEMENT',
})


class Recommender:
    def __init__(self, categories, matchup, roster, free_agents, stat_weights=None):
        self.categories = categories
        self.matchup = matchup
        self.roster = roster
        self.free_agents = free_agents
        self.stat_weights = stat_weights or {}
        self.batting_cats = [c for c in categories if c['is_batting']]
        self.pitching_cats = [c for c in categories if c['is_pitching']]
        self._hitter_drops = None
        self._pitcher_drops = None

    @staticmethod
    def _is_available(player):
        """Return True if the player is not on an injured/inactive list."""
        status = getattr(player, 'injuryStatus', None)
        if not status or status == 'ACTIVE':
            return True
        return False

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

    def get_weekly_recommendations(self, analysis, num=10, hitters=None, pitchers=None):
        losing = [a for a in analysis if a['result'] == 'LOSS']
        if not losing:
            return {'hitters': [], 'pitchers': [], 'losing_categories': []}

        losing_bat = [a for a in losing if a['is_batting']]
        losing_pit = [a for a in losing if a['is_pitching']]

        if hitters is None and pitchers is None:
            hitters, pitchers = self._split_free_agents()
        hitter_recs = self._rank_for_categories(hitters, losing_bat)[:num]
        pitcher_recs = self._rank_for_categories(pitchers, losing_pit)[:num]

        self._attach_drop_info(hitter_recs, is_pitcher=False)
        self._attach_drop_info(pitcher_recs, is_pitcher=True)

        return {
            'hitters': hitter_recs,
            'pitchers': pitcher_recs,
            'losing_categories': [a['name'] for a in losing],
        }

    # ------------------------------------------------------------------
    # Season recommendations — overall value across ALL categories
    # ------------------------------------------------------------------

    def get_season_recommendations(self, num=10, hitters=None, pitchers=None):
        if hitters is None and pitchers is None:
            hitters, pitchers = self._split_free_agents()

        hitter_recs = self._rank_for_categories(hitters, self._cats_with_zero_margin(self.batting_cats))[:num]
        pitcher_recs = self._rank_for_categories(pitchers, self._cats_with_zero_margin(self.pitching_cats))[:num]

        self._attach_drop_info(hitter_recs, is_pitcher=False)
        self._attach_drop_info(pitcher_recs, is_pitcher=True)

        return {
            'hitters': hitter_recs,
            'pitchers': pitcher_recs,
        }

    @staticmethod
    def _cats_with_zero_margin(cats):
        return [{**c, 'margin': 0} for c in cats]

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
    # Free-agent splits: ESPN projections vs FanGraphs (pybaseball) stats
    # ------------------------------------------------------------------

    def split_free_agents_fangraphs(self, bat_primary, bat_by_name, pit_primary, pit_by_name):
        hitters, pitchers = [], []
        for fa in self.free_agents:
            if not self._is_available(fa):
                continue
            if fa.position in ('SP', 'RP', 'P'):
                proj = lookup_pitcher(fa.name, fa.proTeam, pit_primary, pit_by_name)
                if proj:
                    pitchers.append((fa, proj))
            else:
                proj = lookup_batter(fa.name, fa.proTeam, bat_primary, bat_by_name)
                if proj:
                    hitters.append((fa, proj))
        return hitters, pitchers

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _split_free_agents(self):
        hitters, pitchers = [], []
        for fa in self.free_agents:
            if not self._is_available(fa):
                continue
            proj = fa.stats.get(0, {}).get('projected_breakdown', {})
            if not proj:
                continue
            if fa.position in ('SP', 'RP', 'P'):
                pitchers.append((fa, proj))
            else:
                hitters.append((fa, proj))
        return hitters, pitchers

    def _rank_for_categories(self, players_with_proj, target_cats):
        """Rank players by normalized composite score across *target_cats*.

        Uses ESPN league stat weights from scoring settings. Weekly nuance: each
        losing category's margin tightens the weight (same as before).
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
            full_proj = dict(proj)
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
                matchup_w = 1.0 / (1.0 + margin * 0.05)
                league_w = float(self.stat_weights.get(name, 1.0))

                score += norm * matchup_w * league_w

            if score > 0:
                scored.append({
                    'player': fa,
                    'score': round(score, 3),
                    'key_stats': key_stats,
                    'proj': full_proj,
                })

        scored.sort(key=lambda x: x['score'], reverse=True)
        return scored

    def _composite_value(self, proj, cats):
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

    # ------------------------------------------------------------------
    # Drop pairing — attach a suggested drop + category deltas to recs
    # ------------------------------------------------------------------

    def _get_drop_candidates_by_type(self):
        """Return (hitter_drops, pitcher_drops) sorted weakest first (bench before starters)."""
        if self._hitter_drops is not None:
            return self._hitter_drops, self._pitcher_drops

        hitter_drops, pitcher_drops = [], []
        for player in self.roster:
            proj = player.stats.get(0, {}).get('projected_breakdown', {})
            value = self._composite_value(proj, self.categories)
            injury = player.injuryStatus
            if injury == 'ACTIVE':
                injury = None

            candidate = {
                'player': player,
                'value': value,
                'proj': proj,
                'lineup_slot': player.lineupSlot,
                'is_bench': player.lineupSlot in ('BE', 'IL'),
                'injury': injury,
            }

            if player.position in ('SP', 'RP', 'P'):
                pitcher_drops.append(candidate)
            else:
                hitter_drops.append(candidate)

        hitter_drops.sort(key=lambda c: (not c['is_bench'], c['value']))
        pitcher_drops.sort(key=lambda c: (not c['is_bench'], c['value']))

        self._hitter_drops = hitter_drops
        self._pitcher_drops = pitcher_drops
        return hitter_drops, pitcher_drops

    def _attach_drop_info(self, recs, is_pitcher):
        """Enrich each recommendation with the best drop candidate and per-category deltas."""
        hitter_drops, pitcher_drops = self._get_drop_candidates_by_type()
        drops = pitcher_drops if is_pitcher else hitter_drops
        cats = self.pitching_cats if is_pitcher else self.batting_cats

        if not drops:
            return

        best_drop = drops[0]
        drop_proj = best_drop['proj']

        for rec in recs:
            add_proj = rec.get('proj', {})
            deltas = {}
            for cat in cats:
                name = cat['name']
                add_val = add_proj.get(name, 0) or 0
                drop_val = drop_proj.get(name, 0) or 0
                deltas[name] = round(add_val - drop_val, 4)

            rec['drop'] = best_drop
            rec['category_deltas'] = deltas


def consensus_pickups(espn_hitters, espn_pitchers, fg_hitters, fg_pitchers, top_n=25):
    """Players appearing in the top *top_n* of both ESPN and FanGraphs lists."""
    return (
        _consensus_side(espn_hitters, fg_hitters, top_n),
        _consensus_side(espn_pitchers, fg_pitchers, top_n),
    )


def _consensus_side(espn_list, fg_list, top_n):
    rank_e = {rec['player'].playerId: (idx, rec) for idx, rec in enumerate(espn_list[:top_n])}
    rank_f = {rec['player'].playerId: (idx, rec) for idx, rec in enumerate(fg_list[:top_n])}
    shared = set(rank_e) & set(rank_f)
    rows = []
    for pid in shared:
        ie, er = rank_e[pid]
        jf, fr = rank_f[pid]
        rows.append({
            'player': er['player'],
            'espn_rank': ie + 1,
            'fg_rank': jf + 1,
            'espn_score': er['score'],
            'fg_score': fr['score'],
            'score': round((er['score'] + fr['score']) / 2, 3),
            'proj': er['proj'],
            'proj_fg': fr['proj'],
            'drop': er.get('drop'),
            'category_deltas': er.get('category_deltas'),
        })
    rows.sort(key=lambda x: x['espn_rank'] + x['fg_rank'])
    return rows
