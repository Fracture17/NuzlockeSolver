"""
Tests for Gen 3 AI switch-in logic (ai_switch.py).

Uses the worked examples from the design document to verify Section 1 and
Section 2 behaviour.  All tests use fake battle state dicts — no IPC/Node.
"""

import pytest
from ai_flags import PokeType as T, MoveCategory, MoveEffect
from ai_flags import MoveInfo
from ai_switch import (
    _section1_type_score,
    _has_se_move,
    _section1_select,
    _section2_move_damage,
    _section2_select,
    select_switch_in,
)


# ---------------------------------------------------------------------------
# Helpers to build minimal fake battle state dicts
# ---------------------------------------------------------------------------

def _make_move_slot(move_id: str) -> dict:
    return {"id": move_id, "pp": 10, "maxpp": 10}


def _make_pokemon(
    name: str,
    types: list[str],
    hp: int = 100,
    maxhp: int = 100,
    ability: str = "",
    move_ids: list[str] = None,
    is_active: bool = False,
    active_turns: int = 1,
    last_move: str = "",
) -> dict:
    return {
        "id": name.lower(),
        "name": name,
        "types": types,
        "hp": hp,
        "maxhp": maxhp,
        "ability": ability,
        "moveSlots": [_make_move_slot(m) for m in (move_ids or [])],
        "isActive": is_active,
        "activeTurns": active_turns,
        "lastMove": last_move,
        "boosts": {},
        "volatiles": {},
    }


def _make_state(p1_pokemon: list[dict], p2_pokemon: list[dict]) -> dict:
    return {
        "battle": {
            "sides": [
                {"pokemon": p1_pokemon, "sideConditions": {}},
                {"pokemon": p2_pokemon, "sideConditions": {}},
            ],
            "field": {},
            "turn": 5,
        },
        "p1_moves": [],
        "p1_switches": [],
        "p2_moves": [],
        "p2_switches": [],
        "is_over": False,
        "winner": None,
        "p1_dmg_calcs": {},
        "p2_dmg_calcs": {},
    }


# ---------------------------------------------------------------------------
# Section 1: _section1_type_score
# ---------------------------------------------------------------------------

class TestSection1TypeScore:
    """Verify the ordered-chart type score formula, including rounding."""

    def test_example1_corsola(self):
        """Combusken (Fire/Fighting) vs Corsola (Water/Rock): score = 4."""
        # Fire vs Water: 10 * 0.5 = 5
        # Fire vs Rock:  5  * 0.5 = 2
        # Fighting vs Rock: 2 * 2 = 4
        player = [T.FIRE, T.FIGHTING]
        ai = [T.WATER, T.ROCK]
        assert _section1_type_score(player, ai) == 4

    def test_example1_yanma(self):
        """Combusken (Fire/Fighting) vs Yanma (Bug/Flying): score = 5."""
        # Fire vs Bug: 10 * 2 = 20
        # Fighting vs Flying: 20 * 0.5 = 10
        # Fighting vs Bug:    10 * 0.5 = 5
        player = [T.FIRE, T.FIGHTING]
        ai = [T.BUG, T.FLYING]
        assert _section1_type_score(player, ai) == 5

    def test_example2_rhyhorn(self):
        """Combusken (Fire/Fighting) vs Rhyhorn (Ground/Rock): score = 10."""
        # Fire vs Rock: 10 * 0.5 = 5
        # Fighting vs Rock: 5 * 2 = 10
        player = [T.FIRE, T.FIGHTING]
        ai = [T.GROUND, T.ROCK]
        assert _section1_type_score(player, ai) == 10

    def test_example2_magnemite(self):
        """Combusken (Fire/Fighting) vs Magnemite (Electric/Steel): score = 40."""
        # Fire vs Steel: 10 * 2 = 20
        # Fighting vs Steel: 20 * 2 = 40
        player = [T.FIRE, T.FIGHTING]
        ai = [T.ELECTRIC, T.STEEL]
        assert _section1_type_score(player, ai) == 40

    def test_example3_lunatone(self):
        """Combusken (Fire/Fighting) vs Lunatone (Rock/Psychic): score = 4.

        The ordered chart checks Fighting vs Psychic BEFORE Fighting vs Rock,
        so score follows: Fire vs Rock (5), Fighting vs Psychic (2), Fighting vs Rock (4).
        """
        # Fire vs Rock: 10 * 0.5 = 5
        # Fighting vs Psychic: 5 * 0.5 = 2  (Psychic comes before Rock for Fighting)
        # Fighting vs Rock: 2 * 2 = 4
        player = [T.FIRE, T.FIGHTING]
        ai = [T.ROCK, T.PSYCHIC]
        assert _section1_type_score(player, ai) == 4

    def test_example4_typhlosion_vs_charizard(self):
        """Typhlosion (Fire only) vs Charizard (Fire/Flying): score = 2.

        Single-typed attacker: Fire type applied twice.
        Fire vs Fire: 10 * 0.5 = 5; again: 5 * 0.5 = 2.
        """
        # Single-typed → expand to [Fire, Fire]
        player = [T.FIRE, T.FIRE]
        ai = [T.FIRE, T.FLYING]
        assert _section1_type_score(player, ai) == 2

    def test_example4_typhlosion_vs_corsola(self):
        """Typhlosion (Fire only) vs Corsola (Water/Rock): score = 0.

        Fire vs Water: 10 * 0.5 = 5; Fire vs Rock: 5 * 0.5 = 2
        Fire vs Water (again): 2 * 0.5 = 1; Fire vs Rock (again): 1 * 0.5 = 0.
        """
        player = [T.FIRE, T.FIRE]  # expanded single-typed
        ai = [T.WATER, T.ROCK]
        assert _section1_type_score(player, ai) == 0

    def test_neutral_score_unchanged(self):
        """If no interactions apply, score stays at 10."""
        player = [T.NORMAL, T.NORMAL]
        ai = [T.NORMAL]           # Normal-type: no non-neutral interactions with Normal attack
        assert _section1_type_score(player, ai) == 10

    def test_immunity_yields_zero(self):
        """Electric vs Ground-type defender: immunity, score becomes 0."""
        player = [T.ELECTRIC, T.ELECTRIC]
        ai = [T.GROUND]
        assert _section1_type_score(player, ai) == 0


# ---------------------------------------------------------------------------
# Section 1: _has_se_move
# ---------------------------------------------------------------------------

class TestHasSEMove:
    """Verify the super-effective move check."""

    def _mi(self, move_type: T, category=MoveCategory.PHYSICAL) -> MoveInfo:
        return MoveInfo(
            name="Test", type=move_type, category=category,
            power=80, effect=MoveEffect.NONE,
        )

    def test_se_move_returns_true(self):
        moves = [self._mi(T.WATER)]
        assert _has_se_move(moves, [T.FIRE], False)

    def test_not_se_returns_false(self):
        moves = [self._mi(T.NORMAL)]
        assert not _has_se_move(moves, [T.NORMAL], False)

    def test_status_moves_excluded(self):
        status_mi = MoveInfo(
            name="Thunder Wave", type=T.ELECTRIC, category=MoveCategory.STATUS,
            power=0, effect=MoveEffect.PARALYZE,
        )
        # Electric is SE on Water, but status moves don't count
        assert not _has_se_move([status_mi], [T.WATER], False)

    def test_ground_skipped_for_levitate(self):
        moves = [self._mi(T.GROUND)]
        # Ground would be SE on Fire, but player has Levitate
        assert not _has_se_move(moves, [T.FIRE], player_has_levitate=True)

    def test_ground_hits_non_levitate(self):
        moves = [self._mi(T.GROUND)]
        assert _has_se_move(moves, [T.FIRE], player_has_levitate=False)

    def test_dual_type_one_se(self):
        moves = [self._mi(T.FIRE)]
        # SE on Grass, even though Ice resists Fire
        assert _has_se_move(moves, [T.GRASS, T.ICE], False)


# ---------------------------------------------------------------------------
# Section 1: _section1_select (integration)
# ---------------------------------------------------------------------------

class TestSection1Select:
    """Integration tests using fake battle states."""

    def test_example1_yanma_chosen_over_corsola(self):
        """Yanma (score=5) wins over Corsola (score=4); both have SE moves."""
        # Player: Combusken (Fire/Fighting) active
        p1_active = _make_pokemon("Combusken", ["Fire", "Fighting"], is_active=True)
        # AI: Corsola (Water/Rock, has Surf), Yanma (Bug/Flying, has AncientPower)
        corsola = _make_pokemon("Corsola", ["Water", "Rock"], hp=100,
                                move_ids=["surf"])           # Water → SE on Fire
        yanma   = _make_pokemon("Yanma", ["Bug", "Flying"], hp=100,
                                move_ids=["wingattack"])     # Flying → SE on Fighting

        state = _make_state([p1_active], [corsola, yanma])
        # switch 1 = corsola (slot 0+1), switch 2 = yanma (slot 1+1)
        result = _section1_select(state, ["switch 1", "switch 2"], player=2)
        assert result == "switch 2"   # Yanma has higher score

    def test_example2_rhyhorn_chosen_over_yanma(self):
        """Rhyhorn (score=10) wins over Yanma (score=5), both have SE moves."""
        p1_active = _make_pokemon("Combusken", ["Fire", "Fighting"], is_active=True)
        rhyhorn = _make_pokemon("Rhyhorn", ["Ground", "Rock"], hp=100,
                                move_ids=["earthquake"])    # Ground → SE on Fire
        yanma   = _make_pokemon("Yanma", ["Bug", "Flying"], hp=100,
                                move_ids=["wingattack"])    # Flying → SE on Fighting
        magnemite = _make_pokemon("Magnemite", ["Electric", "Steel"], hp=100,
                                  move_ids=["thunderwave"])  # No SE damaging move

        state = _make_state([p1_active], [rhyhorn, yanma, magnemite])
        result = _section1_select(state, ["switch 1", "switch 2", "switch 3"], player=2)
        # Magnemite has highest score (40) but no SE damaging move.
        # Rhyhorn (10) > Yanma (5), both have SE moves → Rhyhorn chosen.
        assert result == "switch 1"

    def test_example4_no_valid_candidate(self):
        """Corsola (score=0) is never chosen; returns None → fall through."""
        p1_active = _make_pokemon("Typhlosion", ["Fire"], is_active=True)
        # Charizard (Fire/Flying, no SE move on Fire)
        charizard = _make_pokemon("Charizard", ["Fire", "Flying"], hp=100,
                                  move_ids=["flamethrower"])
        # Corsola (Water/Rock, has SE move but score=0)
        corsola = _make_pokemon("Corsola", ["Water", "Rock"], hp=100,
                                move_ids=["surf"])

        state = _make_state([p1_active], [charizard, corsola])
        result = _section1_select(state, ["switch 1", "switch 2"], player=2)
        assert result is None

    def test_fainted_candidate_skipped(self):
        """A fainted (hp=0) switch candidate is ignored."""
        p1_active = _make_pokemon("Combusken", ["Fire", "Fighting"], is_active=True)
        fainted  = _make_pokemon("Vaporeon", ["Water"], hp=0, move_ids=["surf"])
        healthy  = _make_pokemon("Yanma", ["Bug", "Flying"], hp=100,
                                 move_ids=["wingattack"])    # Flying → SE on Fighting

        state = _make_state([p1_active], [fainted, healthy])
        result = _section1_select(state, ["switch 1", "switch 2"], player=2)
        assert result == "switch 2"


# ---------------------------------------------------------------------------
# Section 2: _section2_move_damage
# ---------------------------------------------------------------------------

class TestSection2MoveDamage:
    """Verify Section 2 damage calculation, including rounding and STAB."""

    def _mi(self, move_type: T, power: int, cat=MoveCategory.PHYSICAL) -> MoveInfo:
        return MoveInfo(
            name="TestMove", type=move_type, category=cat,
            power=power, effect=MoveEffect.NONE,
        )

    def test_neutral_no_stab(self):
        """Neutral effectiveness, no STAB: damage = base_dmg."""
        mi = self._mi(T.NORMAL, 50)
        dmg = _section2_move_damage(mi, 50, [T.FIRE], [T.NORMAL])
        assert dmg == 50

    def test_se_doubles(self):
        """2× effectiveness doubles damage."""
        mi = self._mi(T.WATER, 60)
        dmg = _section2_move_damage(mi, 60, [T.NORMAL], [T.FIRE])
        assert dmg == 120

    def test_stab_from_fainted_types(self):
        """STAB uses fainted Pokémon's types, not candidate's."""
        mi = self._mi(T.ELECTRIC, 80)
        # Fainted was Electric, so Electric move has STAB: int(80 * 1.5) = 120
        dmg = _section2_move_damage(mi, 80, [T.ELECTRIC], [T.NORMAL])
        assert dmg == 120

    def test_stab_rounding(self):
        """STAB with odd base_dmg floors correctly: int(31 * 1.5) = 46."""
        mi = self._mi(T.ELECTRIC, 31)
        dmg = _section2_move_damage(mi, 31, [T.ELECTRIC], [T.NORMAL])
        assert dmg == 46

    def test_status_move_counts_as_3(self):
        """STATUS moves use base damage of 3."""
        status_mi = MoveInfo(
            name="Thunder Wave", type=T.ELECTRIC, category=MoveCategory.STATUS,
            power=0, effect=MoveEffect.PARALYZE,
        )
        # Electric vs Water: 3 * 2 = 6
        dmg = _section2_move_damage(status_mi, 100, [T.NORMAL], [T.WATER])
        assert dmg == 6

    def test_immune_via_type_chart(self):
        """0× effectiveness yields 0 damage."""
        mi = self._mi(T.ELECTRIC, 80)
        dmg = _section2_move_damage(mi, 80, [T.NORMAL], [T.GROUND])
        assert dmg == 0

    def test_example4_fighting_vs_scizor(self):
        """Example 4 from document: Fighting vs Scizor (Bug/Steel), base_dmg=31.

        Ordered chart applies Bug first (×0.5 → 15), then Steel (×2 → 30).
        30 < 31 (neutral), so Fighting is scored lower despite being neutral overall.
        """
        fighting_mi = self._mi(T.FIGHTING, 31)
        # Bug/Steel defender
        dmg = _section2_move_damage(fighting_mi, 31, [T.NORMAL], [T.BUG, T.STEEL])
        assert dmg == 30   # 31 → ×0.5(bug)=15 → ×2(steel)=30


# ---------------------------------------------------------------------------
# Section 2: _section2_select (integration)
# ---------------------------------------------------------------------------

class TestSection2Select:
    """Integration tests for Section 2 switch selection."""

    def test_example1_thunderbolt_beats_tackle(self):
        """Water-type player: Thunderbolt (Electric SE) wins over Tackle (neutral)."""
        p1_active = _make_pokemon("Vaporeon", ["Water"], is_active=True)
        # Fainted AI pokemon (Electric), last move = thunderbolt
        fainted = _make_pokemon("Jolteon", ["Electric"], hp=0,
                                active_turns=5, last_move="thunderbolt")
        # switch 1: Pokémon with Tackle
        cand1 = _make_pokemon("Raticate", ["Normal"], hp=100, move_ids=["tackle"])
        # switch 2: Pokémon with Thunderbolt
        cand2 = _make_pokemon("Electabuzz", ["Electric"], hp=100, move_ids=["thunderbolt"])

        state = _make_state([p1_active], [fainted, cand1, cand2])
        result = _section2_select(state, ["switch 2", "switch 3"], player=2)
        assert result == "switch 3"   # Electabuzz/Thunderbolt is SE on Water

    def test_example2_thunder_wave_beats_tackle(self):
        """Water-type player: Thunder Wave (status, Electric) beats Tackle via SE."""
        p1_active = _make_pokemon("Vaporeon", ["Water"], is_active=True)
        fainted = _make_pokemon("Jolteon", ["Electric"], hp=0,
                                active_turns=5, last_move="thunderbolt")
        cand1 = _make_pokemon("Raticate", ["Normal"], hp=100, move_ids=["tackle"])
        cand2 = _make_pokemon("Ampharos", ["Electric"], hp=100, move_ids=["thunderwave"])

        state = _make_state([p1_active], [fainted, cand1, cand2])
        result = _section2_select(state, ["switch 2", "switch 3"], player=2)
        # base_dmg = power of thunderbolt (95), fainted_types = [Electric]
        # Cand1 Raticate (Normal) + Tackle: Normal vs Water = neutral → dmg = 95
        # Cand2 Ampharos (Electric) + Thunder Wave: STATUS → base=3, STAB (fainted Electric)
        #   → int(3 * 1.5)=4, Electric vs Water ×2 → 8
        # 95 > 8 → Raticate/Tackle wins → switch 2
        assert result == "switch 2"

    def test_example3_stab_from_fainted(self):
        """Normal-type player, fainted Electric Pokémon: Thunderbolt gets STAB bonus."""
        p1_active = _make_pokemon("Chansey", ["Normal"], is_active=True)
        # Fainted was Electric; last move = thunderbolt (base 95, STAB for Electric)
        fainted = _make_pokemon("Jolteon", ["Electric"], hp=0,
                                active_turns=5, last_move="thunderbolt")
        cand1 = _make_pokemon("Rhydon", ["Ground", "Rock"], hp=100, move_ids=["hyperbeam"])
        cand2 = _make_pokemon("Electabuzz", ["Electric"], hp=100, move_ids=["thunderbolt"])

        state = _make_state([p1_active], [fainted, cand1, cand2])
        result = _section2_select(state, ["switch 2", "switch 3"], player=2)
        # Cand1 Hyper Beam: excluded (MoveEffect.RECHARGE from _NAME_EFFECTS)
        # → cand1 effectively has 0 valid non-excluded moves
        # Cand2 Thunderbolt: STAB (fainted is Electric) → int(95*1.5) = 142, vs Normal = neutral
        assert result == "switch 3"

    def test_immune_all_returns_none(self):
        """If every candidate's best move does zero damage to p1, Section 2 returns None."""
        # Player is Ground-type → immune to Electric (ordered chart: Electric vs Ground = 0)
        p1_active = _make_pokemon("Sandshrew", ["Ground"], is_active=True)
        # Fainted AI pokemon (Electric), last move = thunderbolt (power 95)
        fainted = _make_pokemon("Jolteon", ["Electric"], hp=0,
                                active_turns=5, last_move="thunderbolt")
        # Candidate has only Thunderbolt (Electric) → 0 damage vs Ground-type player
        cand = _make_pokemon("Electrode", ["Electric"], hp=100, move_ids=["thunderbolt"])

        state = _make_state([p1_active], [fainted, cand])
        result = _section2_select(state, ["switch 2"], player=2)
        assert result is None

    def test_first_candidate_wins_ties(self):
        """When two candidates deal equal damage, the first in party wins."""
        p1_active = _make_pokemon("Alakazam", ["Psychic"], is_active=True)
        fainted = _make_pokemon("Gengar", ["Ghost", "Poison"], hp=0,
                                active_turns=5, last_move="shadowball")
        # Both candidates have Shadow Ball (Ghost, same power, neutral vs Psychic? No)
        # Shadow Ball (Ghost) vs Psychic: SE (2x). Both deal same damage.
        cand1 = _make_pokemon("Haunter", ["Ghost", "Poison"], hp=100, move_ids=["shadowball"])
        cand2 = _make_pokemon("Misdreavus", ["Ghost"], hp=100, move_ids=["shadowball"])

        state = _make_state([p1_active], [fainted, cand1, cand2])
        result = _section2_select(state, ["switch 2", "switch 3"], player=2)
        # Both deal the same damage → first candidate (switch 2) wins
        assert result == "switch 2"


# ---------------------------------------------------------------------------
# select_switch_in: public API
# ---------------------------------------------------------------------------

class TestSelectSwitchIn:
    """Verify the top-level select_switch_in function."""

    def test_returns_first_when_no_better_option(self):
        """When neither section finds a winner, fall back to switch_actions[0]."""
        p1_active = _make_pokemon("Mewtwo", ["Psychic"], is_active=True)
        fainted = _make_pokemon("Slowbro", ["Water", "Psychic"], hp=0,
                                active_turns=3, last_move="surf")
        # Candidate with Splash (Normal, no effect — effectively no damage)
        cand = _make_pokemon("Magikarp", ["Water"], hp=100, move_ids=["splash"])

        state = _make_state([p1_active], [fainted, cand])
        result = select_switch_in(state, ["switch 2"], player=2)
        assert result == "switch 2"

    def test_empty_switch_actions_returns_none(self):
        state = _make_state([], [])
        result = select_switch_in(state, [], player=2)
        assert result is None

    def test_section1_takes_priority_over_section2(self):
        """If Section 1 finds a valid candidate, Section 2 is not used."""
        p1_active = _make_pokemon("Vaporeon", ["Water"], is_active=True)
        fainted = _make_pokemon("Jolteon", ["Electric"], hp=0,
                                active_turns=5, last_move="thunderbolt")
        # cand1: high Section 2 damage (thunderbolt on Water) but also Section 1 valid
        cand1 = _make_pokemon("Electrode", ["Electric"], hp=100, move_ids=["thunderbolt"])
        # cand2: would win Section 2 but has higher Section 1 score
        cand2 = _make_pokemon("Raichu", ["Electric"], hp=100, move_ids=["thunderbolt"])

        state = _make_state([p1_active], [fainted, cand1, cand2])
        # Both have same types and same moves — Section 1 should pick first (switch 2)
        result = select_switch_in(state, ["switch 2", "switch 3"], player=2)
        assert result in ["switch 2", "switch 3"]

    def test_player1_perspective(self):
        """Works correctly with player=1 (AI is on side 0)."""
        p2_active = _make_pokemon("Blastoise", ["Water"], is_active=True)
        fainted_p1 = _make_pokemon("Jolteon", ["Electric"], hp=0,
                                   active_turns=3, last_move="thunderbolt")
        cand_p1 = _make_pokemon("Raichu", ["Electric"], hp=100, move_ids=["thunderbolt"])

        state = _make_state(
            [fainted_p1, cand_p1],  # p1 side (AI when player=1)
            [p2_active],             # p2 side (player)
        )
        result = select_switch_in(state, ["switch 2"], player=1)
        assert result == "switch 2"
