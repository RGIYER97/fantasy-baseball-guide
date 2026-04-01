from pybaseball_stats import lookup_batter, lookup_pitcher
from roster import (
    find_best_drop,
    eligible_display,
    eligible_positions,
    is_pitcher as is_pitcher_player,
    get_starting_slots,
    positional_scarcity,
)

INJURED_STATUSES = frozenset({
    'OUT', 'DAY_TO_DAY', 'SUSPENSION',
    'TEN_DAY_DL', 'FIFTEEN_DAY_DL', 'SIXTY_DAY_DL', 'SEVEN_DAY_DL',
    'INJURED_RESERVE', 'PATERNITY', 'BEREAVEMENT',
})


class Recommender:
    def __init__(self, categories, matchup, roster, free_agents,
                 stat_weights=None, scoring_period=None,
                 weekly_free_agents=None):
        self.categories = categories
        self.matchup = matchup
        self.roster = roster
        self.free_agents = free_agents
        self.weekly_free_agents = weekly_free_agents
        self.stat_weights = stat_weights or {}
        self.scoring_period = scoring_period
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
            return {'hitters': [], 'pitchers': [], 'losing_categories': [],
                    'projection_source': 'none'}

        losing_bat = [a for a in losing if a['is_batting']]
        losing_pit = [a for a in losing if a['is_pitching']]
        target_names = {a['name'] for a in losing}

        projection_source = 'season'
        if hitters is None and pitchers is None:
            hitters, pitchers = self._split_free_agents(use_weekly=True)
            if (self.weekly_free_agents and self.scoring_period is not None
                    and hitters
                    and self._best_projection(hitters[0][0], prefer_weekly=True)
                    != hitters[0][0].stats.get(0, {}).get('projected_breakdown', {})):
                projection_source = 'weekly'

        extra = num * 2
        hitter_recs = self._rank_for_categories(hitters, losing_bat)[:extra]
        pitcher_recs = self._rank_for_categories(pitchers, losing_pit)[:extra]

        used_drops: set = set()
        self._attach_drop_info(hitter_recs, is_pitcher=False, used_drops=used_drops,
                               target_cats=target_names)
        self._attach_drop_info(pitcher_recs, is_pitcher=True, used_drops=used_drops,
                               target_cats=target_names)

        hitter_recs = hitter_recs[:num]
        pitcher_recs = pitcher_recs[:num]

        moves = self._build_move_plan(hitter_recs + pitcher_recs)

        return {
            'hitters': hitter_recs,
            'pitchers': pitcher_recs,
            'losing_categories': [a['name'] for a in losing],
            'projection_source': projection_source,
            'moves': moves,
        }

    # ------------------------------------------------------------------
    # Season recommendations — overall value across ALL categories
    # ------------------------------------------------------------------

    def get_season_recommendations(self, num=10, hitters=None, pitchers=None):
        if hitters is None and pitchers is None:
            hitters, pitchers = self._split_free_agents()

        bat_cats_z = self._cats_with_zero_margin(self.batting_cats)
        pit_cats_z = self._cats_with_zero_margin(self.pitching_cats)
        bat_names = {c['name'] for c in self.batting_cats}
        pit_names = {c['name'] for c in self.pitching_cats}

        extra = num * 2
        hitter_recs = self._rank_for_categories(hitters, bat_cats_z)[:extra]
        pitcher_recs = self._rank_for_categories(pitchers, pit_cats_z)[:extra]

        used_drops: set = set()
        self._attach_drop_info(hitter_recs, is_pitcher=False, used_drops=used_drops,
                               target_cats=bat_names)
        self._attach_drop_info(pitcher_recs, is_pitcher=True, used_drops=used_drops,
                               target_cats=pit_names)

        hitter_recs = hitter_recs[:num]
        pitcher_recs = pitcher_recs[:num]

        moves = self._build_move_plan(hitter_recs + pitcher_recs)

        return {
            'hitters': hitter_recs,
            'pitchers': pitcher_recs,
            'moves': moves,
        }

    @staticmethod
    def _cats_with_zero_margin(cats):
        return [{**c, 'margin': 0} for c in cats]

    # ------------------------------------------------------------------
    # Drop candidates — roster players with lowest projected value
    # ------------------------------------------------------------------

    def get_drop_candidates(self, num=8):
        scarcity = positional_scarcity(self.roster)
        candidates = []
        for player in self.roster:
            proj = player.stats.get(0, {}).get('projected_breakdown', {})
            value = self._composite_value(proj, self.categories)
            injury = player.injuryStatus
            if injury == 'ACTIVE':
                injury = None

            sole_eligible = any(
                scarcity.get(slot, 99) <= 1
                for slot in eligible_positions(player)
                if slot not in ('BE', 'IL', 'UTIL', 'P')
            )

            candidates.append({
                'player': player,
                'value': value,
                'lineup_slot': player.lineupSlot,
                'is_bench': player.lineupSlot in ('BE', 'IL'),
                'injury': injury,
                'eligible': eligible_display(player),
                'sole_eligible': sole_eligible,
            })

        candidates.sort(key=lambda c: (not c['is_bench'], c['sole_eligible'], c['value']))
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

    def _split_free_agents(self, use_weekly=False):
        """Split free agents into (hitters, pitchers) with projections.

        When *use_weekly* is True and weekly free-agent data was provided,
        prefer the current-scoring-period projected stats.  Falls back to
        season projections (scoringPeriod 0) when weekly data is unavailable.
        """
        source = self.free_agents
        if use_weekly and self.weekly_free_agents:
            source = self.weekly_free_agents

        hitters, pitchers = [], []
        for fa in source:
            if not self._is_available(fa):
                continue
            proj = self._best_projection(fa, prefer_weekly=use_weekly)
            if not proj:
                continue
            if fa.position in ('SP', 'RP', 'P'):
                pitchers.append((fa, proj))
            else:
                hitters.append((fa, proj))
        return hitters, pitchers

    def _best_projection(self, player, prefer_weekly=False):
        """Return the best available projected stat breakdown for a player.

        Lookup order when prefer_weekly is True:
          1. Current scoring period's projected_breakdown
          2. Season projected_breakdown (period 0)
        """
        if prefer_weekly and self.scoring_period is not None:
            weekly = player.stats.get(self.scoring_period, {}).get('projected_breakdown')
            if weekly:
                return weekly
        return player.stats.get(0, {}).get('projected_breakdown', {})

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

    @staticmethod
    def _build_move_plan(all_recs):
        """Build an ordered list of {drop, add} dicts from recs that have drops."""
        moves = []
        seen_drops = set()
        for rec in all_recs:
            drop_info = rec.get('drop')
            if not drop_info:
                continue
            drop_pid = getattr(drop_info['player'], 'playerId',
                               id(drop_info['player']))
            if drop_pid in seen_drops:
                continue
            seen_drops.add(drop_pid)
            moves.append({
                'drop': drop_info,
                'add': rec,
            })
        return moves

    # ------------------------------------------------------------------
    # Drop pairing — position-aware, per-pickup drop suggestions
    # ------------------------------------------------------------------

    def _get_drop_candidates_by_type(self):
        """Return (hitter_drops, pitcher_drops) sorted weakest first.

        Sort order: bench before starters, then by ascending composite value.
        Positional scarcity is used as a tiebreaker — players who are the sole
        eligible player for a starting slot are pushed later (harder to drop).
        """
        if self._hitter_drops is not None:
            return self._hitter_drops, self._pitcher_drops

        scarcity = positional_scarcity(self.roster)

        hitter_drops, pitcher_drops = [], []
        for player in self.roster:
            proj = player.stats.get(0, {}).get('projected_breakdown', {})
            value = self._composite_value(proj, self.categories)
            injury = player.injuryStatus
            if injury == 'ACTIVE':
                injury = None

            sole_eligible = any(
                scarcity.get(slot, 99) <= 1
                for slot in eligible_positions(player)
                if slot not in ('BE', 'IL', 'UTIL', 'P')
            )

            candidate = {
                'player': player,
                'value': value,
                'proj': proj,
                'lineup_slot': player.lineupSlot,
                'is_bench': player.lineupSlot in ('BE', 'IL'),
                'injury': injury,
                'eligible': eligible_display(player),
                'sole_eligible': sole_eligible,
            }

            if is_pitcher_player(player):
                pitcher_drops.append(candidate)
            else:
                hitter_drops.append(candidate)

        hitter_drops.sort(key=lambda c: (not c['is_bench'], c['sole_eligible'], c['value']))
        pitcher_drops.sort(key=lambda c: (not c['is_bench'], c['sole_eligible'], c['value']))

        self._hitter_drops = hitter_drops
        self._pitcher_drops = pitcher_drops
        return hitter_drops, pitcher_drops

    def _attach_drop_info(self, recs, is_pitcher, used_drops=None,
                          target_cats=None):
        """Attach a position-feasible drop candidate and category deltas to each rec.

        Drop selection strategy:
          1. Try same-position first — only consider roster players that share
             a real playing position with the pickup (C↔C, OF↔OF, SP↔SP, …).
          2. If no same-position drop works, fall back to any roster player
             and mark the recommendation ``for_util=True`` (the pickup would
             fill a UTIL slot rather than replacing someone at the same position).

        After pairing, swaps where the pickup is projected worse than the drop
        across the *target_cats* (the categories that matter for this
        recommendation set) are removed.  This prevents recommending moves
        that would make the roster worse in the categories you're trying to
        improve.

        Each pickup gets a **unique** drop.  *used_drops* is a shared set of
        player IDs already claimed by earlier recommendations; it is mutated
        in-place so callers can coordinate across hitter and pitcher calls.
        """
        if used_drops is None:
            used_drops = set()

        hitter_drops, pitcher_drops = self._get_drop_candidates_by_type()
        all_drops = hitter_drops + pitcher_drops
        all_drops.sort(key=lambda c: (not c['is_bench'], c['sole_eligible'], c['value']))
        cats = self.pitching_cats if is_pitcher else self.batting_cats

        if not all_drops:
            return

        to_remove = []
        for idx, rec in enumerate(recs):
            pickup_player = rec['player']
            rec['eligible'] = eligible_display(pickup_player)

            best_drop = find_best_drop(
                pickup_player, self.roster, all_drops,
                excluded_ids=used_drops,
                require_same_position=True,
            )
            for_util = False

            if best_drop is None:
                best_drop = find_best_drop(
                    pickup_player, self.roster, all_drops,
                    excluded_ids=used_drops,
                    require_same_position=False,
                )
                if best_drop is not None:
                    for_util = True

            rec['for_util'] = for_util

            if best_drop:
                drop_proj = best_drop['proj']
                add_proj = rec.get('proj', {})
                deltas = {}
                for cat in cats:
                    name = cat['name']
                    add_val = add_proj.get(name, 0) or 0
                    drop_val = drop_proj.get(name, 0) or 0
                    deltas[name] = round(add_val - drop_val, 4)

                swap_score = self._net_swap_score(deltas, cats, target_cats)

                if swap_score <= 0:
                    to_remove.append(idx)
                    continue

                drop_pid = getattr(best_drop['player'], 'playerId',
                                   id(best_drop['player']))
                used_drops.add(drop_pid)

                rec['drop'] = best_drop
                rec['category_deltas'] = deltas
                rec['swap_score'] = round(swap_score, 3)
            else:
                rec['drop'] = None
                rec['category_deltas'] = {}
                rec['swap_score'] = 0
                to_remove.append(idx)

        for idx in reversed(to_remove):
            recs.pop(idx)

    def _net_swap_score(self, deltas, cats, target_cats=None):
        """Score a swap's category deltas, focusing on the target categories.

        Positive deltas in target categories contribute positively; negative
        deltas are penalised.  For inverse stats (ERA, WHIP, etc.) the sign
        is already correct in deltas (lower = better → positive delta means
        the pickup has a *higher* value, which is bad), but the caller
        already computes delta as add−drop, so for inverse stats a negative
        delta is actually good.  We account for that here.
        """
        score = 0.0
        for cat in cats:
            name = cat['name']
            d = deltas.get(name, 0)
            if d == 0:
                continue

            if target_cats and name not in target_cats:
                continue

            if cat.get('is_inverse'):
                d = -d

            weight = float(self.stat_weights.get(name, 1.0))
            score += d * weight

        return score


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
            'eligible': er.get('eligible', er['player'].position),
        })
    rows.sort(key=lambda x: x['espn_rank'] + x['fg_rank'])
    return rows
