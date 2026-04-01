"""Roster construction and positional feasibility checks.

Uses bipartite matching (augmenting-path / Hopcroft-Karp lite) to verify
that every required starting lineup slot can be legally filled after a
proposed add/drop swap.

Slot configuration can come from two sources (best → fallback):
  1. League API settings  (get_roster_slots() in LeagueClient)
  2. Inferred from the current roster's lineup slot assignments
"""

from __future__ import annotations

from typing import Optional

BENCH_SLOTS = frozenset({"BE", "IL"})

# Module-level cache — set once via set_league_slots(), used everywhere.
_league_slots: list[str] | None = None


def set_league_slots(slots: list[str]) -> None:
    """Store the authoritative slot list fetched from the ESPN API."""
    global _league_slots
    _league_slots = list(slots)


# ── Slot inspection ──────────────────────────────────────────────────────

def get_starting_slots(roster) -> list[str]:
    """Return the starting lineup slots for the league.

    Uses the API-provided slot configuration if available (set via
    set_league_slots), otherwise falls back to inferring from the roster.
    """
    if _league_slots is not None:
        return list(_league_slots)
    return [p.lineupSlot for p in roster if p.lineupSlot not in BENCH_SLOTS]


def eligible_positions(player) -> list[str]:
    """Meaningful eligible position strings, excluding bench/IL/UTIL/P flex."""
    skip = {"BE", "IL"}
    return [s for s in getattr(player, "eligibleSlots", []) if s not in skip]


def eligible_display(player) -> str:
    """Compact string of playing positions for display purposes."""
    skip = {"BE", "IL", "UTIL", "P"}
    slots = [s for s in getattr(player, "eligibleSlots", []) if s not in skip]
    return "/".join(slots) if slots else getattr(player, "position", "?")


def is_pitcher(player) -> bool:
    pos = getattr(player, "position", "")
    return pos in ("SP", "RP", "P")


# Slots that are generic flex / non-positional — excluded when comparing
# whether two players share a "real" position.
_FLEX_SLOTS = frozenset({"BE", "IL", "UTIL"})


def playing_positions(player) -> set[str]:
    """Real playing positions (C, 1B, 2B, SS, OF, SP, RP, etc.)
    excluding flex/generic slots."""
    return {
        s for s in getattr(player, "eligibleSlots", [])
        if s not in _FLEX_SLOTS
    }


def shares_position(player_a, player_b) -> bool:
    """True if two players share at least one real playing position."""
    return bool(playing_positions(player_a) & playing_positions(player_b))


# ── Bipartite matching ───────────────────────────────────────────────────

def _can_match(players, slots) -> bool:
    """True if *players* can cover every slot via maximum bipartite matching."""
    if not slots:
        return True

    n_slots = len(slots)
    adj: list[list[int]] = [[] for _ in range(n_slots)]
    for si, slot in enumerate(slots):
        for pi, player in enumerate(players):
            elig = getattr(player, "eligibleSlots", [])
            if slot in elig:
                adj[si].append(pi)

    match_player: dict[int, int] = {}

    def _augment(si: int, visited: set[int]) -> bool:
        for pi in adj[si]:
            if pi in visited:
                continue
            visited.add(pi)
            if pi not in match_player or _augment(match_player[pi], visited):
                match_player[pi] = si
                return True
        return False

    matched = 0
    for si in range(n_slots):
        if _augment(si, set()):
            matched += 1

    return matched == n_slots


# ── Swap feasibility ────────────────────────────────────────────────────

def is_swap_feasible(roster, drop_player, add_player) -> bool:
    """Can all starting slots be filled after dropping *drop_player* and
    adding *add_player*?"""
    starting_slots = get_starting_slots(roster)
    new_roster = [p for p in roster if p is not drop_player] + [add_player]
    return _can_match(new_roster, starting_slots)


def find_best_drop(
    pickup_player,
    roster,
    drop_candidates: list[dict],
    excluded_ids: set | None = None,
    require_same_position: bool = False,
) -> Optional[dict]:
    """Return the first (weakest-value) drop candidate where the swap is
    positionally feasible.  *drop_candidates* must already be sorted
    weakest-first (bench before starters, lowest value first).

    Players whose ``playerId`` is in *excluded_ids* are skipped — this is
    used to ensure each pickup gets a **unique** drop recommendation.

    When *require_same_position* is True, only candidates that share at
    least one real playing position with *pickup_player* are considered.
    """
    excluded = excluded_ids or set()
    pickup_pos = playing_positions(pickup_player) if require_same_position else None
    for candidate in drop_candidates:
        pid = getattr(candidate["player"], "playerId", id(candidate["player"]))
        if pid in excluded:
            continue
        if require_same_position:
            cand_pos = playing_positions(candidate["player"])
            if not (pickup_pos & cand_pos):
                continue
        if is_swap_feasible(roster, candidate["player"], pickup_player):
            return candidate
    return None


def positional_scarcity(roster) -> dict[str, int]:
    """Count how many roster players are eligible for each starting slot.

    Positions with count == 1 are 'scarce' — dropping that player would
    leave the slot unfillable.
    """
    starting_slots = set(get_starting_slots(roster))
    counts: dict[str, int] = {s: 0 for s in starting_slots}
    for player in roster:
        elig = getattr(player, "eligibleSlots", [])
        for slot in starting_slots:
            if slot in elig:
                counts[slot] += 1
    return counts
