"""
Battle simulation using MCTS for player action selection.

Uses the NodeIPC Pokemon Showdown simulator to run full 6v6 battles.
Player actions are chosen by MCTS; the opponent uses its first available
move, or its first available switch if it has no moves (e.g. after a faint).
"""

import json
import random
import threading
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from typing import Optional

from mcts import MCTS, MCTSNode
from MatchupInfo import BOX, OPPONENT
from ai_flags import (
    BattleContext, PokemonState, MoveInfo, PokeType, Weather, MoveCategory,
    score_move, make_rng,
)
from gen3_data import get_move_info
from ai_switch import select_switch_in



@dataclass
class BattleResult:
    winner: str   # "p1", "p2", or "unknown"
    turns: int
    log: str

    def __repr__(self):
        return f"BattleResult(winner={self.winner}, turns={self.turns}\n{'\n'.join(self.log)}\n)"


def parse_move_actions(moves_str: str) -> list:
    """Parse IPC p1Moves string ("1:2:3:4") into ["move 1", "move 2", ...].

    Indices in the IPC string are 1-based colon-separated integers.
    Falls back to ["move 1","move 2","move 3","move 4"] if no valid entries are parsed.
    """
    actions = []
    for part in str(moves_str).split(':'):
        try:
            actions.append(f"move {int(part.strip()) + 1}")
        except ValueError:
            pass
    return actions if actions else [f"move {i}" for i in range(1, 5)]


def parse_switch_actions(switches_str: str) -> list:
    """Parse IPC p1Switches string ("2:3") into ["switch 2", "switch 3", ...].

    Indices are 1-based colon-separated integers.
    Returns an empty list if no valid entries are found.
    """
    actions = []
    for part in str(switches_str).split(':'):
        try:
            actions.append(f"switch {int(part.strip()) + 1}")
        except ValueError:
            pass
    return actions


def detect_winner(battle_state: dict) -> Optional[str]:
    """Determine the winner by checking which side still has Pokémon with HP > 0.

    Returns "p1", "p2", or None if the result is indeterminate.
    """
    sides = battle_state.get("sides", [])
    if len(sides) < 2:
        return None
    p1_alive = any(p.get("hp", 0) > 0 for p in sides[0].get("pokemon", []))
    p2_alive = any(p.get("hp", 0) > 0 for p in sides[1].get("pokemon", []))
    if p1_alive and not p2_alive:
        return "p1"
    if p2_alive and not p1_alive:
        return "p2"
    return None


def parse_ipc_response(response: dict) -> dict:
    """Convert a raw IPC response into a battle state dict for PokemonMCTS.

    Terminal detection: battle is over when all four action keys (p1Moves,
    p1Switches, p2Moves, p2Switches) are absent. If only p2Switches is present
    (opponent forced switch), the battle is NOT over.

    Returns a dict with keys:
        battle        – raw battle state dict from IPC
        p1_moves      – list of "move N" action strings
        p1_switches   – list of "switch N" action strings
        p2_moves      – list of "move N" action strings for the opponent
        p2_switches   – list of "switch N" action strings for the opponent
        is_over       – True when the battle is finished
        winner        – "p1", "p2", or None
    """
    result = response["result"]
    battle = result["battle"]

    p1_moves_str = result.get("p1Moves")
    p1_switches_str = result.get("p1Switches")
    p2_moves_str = result.get("p2Moves")
    p2_switches_str = result.get("p2Switches")

    p1_moves = parse_move_actions(p1_moves_str) if p1_moves_str is not None else []
    p1_switches = parse_switch_actions(p1_switches_str) if p1_switches_str is not None else []
    p2_moves = parse_move_actions(p2_moves_str) if p2_moves_str is not None else []
    p2_switches = parse_switch_actions(p2_switches_str) if p2_switches_str is not None else []

    is_over = (
        p1_moves_str is None and p1_switches_str is None and
        p2_moves_str is None and p2_switches_str is None
    )
    winner = detect_winner(battle) if is_over else None

    # Damage calculations added by Connection.js (raw HP damage per move action)
    p1_dmg_calcs = result.get("p1DmgCalcs") or {}
    p2_dmg_calcs = result.get("p2DmgCalcs") or {}

    return {
        "battle": battle,
        "p1_moves": p1_moves,
        "p1_switches": p1_switches,
        "p2_moves": p2_moves,
        "p2_switches": p2_switches,
        "is_over": is_over,
        "winner": winner,
        "p1_dmg_calcs": p1_dmg_calcs,
        "p2_dmg_calcs": p2_dmg_calcs,
    }


# ---------------------------------------------------------------------------
# Battle context construction from IPC state
# ---------------------------------------------------------------------------

_PS_TYPE_MAP: dict = {
    "Normal":   PokeType.NORMAL,
    "Fire":     PokeType.FIRE,
    "Water":    PokeType.WATER,
    "Grass":    PokeType.GRASS,
    "Electric": PokeType.ELECTRIC,
    "Ice":      PokeType.ICE,
    "Fighting": PokeType.FIGHTING,
    "Poison":   PokeType.POISON,
    "Ground":   PokeType.GROUND,
    "Flying":   PokeType.FLYING,
    "Psychic":  PokeType.PSYCHIC,
    "Bug":      PokeType.BUG,
    "Rock":     PokeType.ROCK,
    "Ghost":    PokeType.GHOST,
    "Dragon":   PokeType.DRAGON,
    "Dark":     PokeType.DARK,
    "Steel":    PokeType.STEEL,
}

_PS_WEATHER_MAP: dict = {
    "RainDance": Weather.RAIN,
    "SunnyDay":  Weather.SUN,
    "sunnyday":  Weather.SUN,
    "Sandstorm": Weather.SAND,
    "Hail":      Weather.HAIL,
    "hail":      Weather.HAIL,
}

_PS_STATUS_MAP: dict = {
    "slp": "sleep",
    "par": "paralyze",
    "psn": "poison",
    "tox": "badly_poison",
    "brn": "burn",
    "frz": "freeze",
}

# Lowercase PS ability ID → display name used in ai_flags checks
_ABILITY_NAME_MAP: dict = {
    "levitate":    "Levitate",
    "immunity":    "Immunity",
    "insomnia":    "Insomnia",
    "vitalspirit": "Vital Spirit",
    "owntempo":    "Own Tempo",
    "limber":      "Limber",
    "waterveil":   "Water Veil",
    "soundproof":  "Soundproof",
    "clearbody":   "Clear Body",
    "damp":        "Damp",
    "sturdy":      "Sturdy",
    "wonderguard": "Wonder Guard",
    "voltabsorb":  "Volt Absorb",
    "waterabsorb": "Water Absorb",
    "flashfire":   "Flash Fire",
    "oblivious":   "Oblivious",
}


def _get_active_pokemon(side: dict) -> Optional[dict]:
    """Return the first active Pokémon dict from a side dict."""
    for p in side.get("pokemon", []):
        if p.get("isActive"):
            return p
    pokes = side.get("pokemon", [])
    return pokes[0] if pokes else None


def _parse_last_move(pokemon: dict) -> Optional[MoveInfo]:
    """Extract the last move used as a MoveInfo (or None)."""
    last = pokemon.get("lastMove") or pokemon.get("lastMoveUsed")
    if not last:
        return None
    # PS format: {'move': '[Move:return]', ...} or just a move ID string
    raw = last if isinstance(last, str) else last.get("move", "")
    # Strip PS wrapper like '[Move:return]'
    if raw.startswith("[Move:"):
        raw = raw[6:-1]
    return get_move_info(raw.lower().replace(" ", ""))


def _build_pokemon_state(
    pokemon: dict,
    side: dict,
    side_conditions: dict,
) -> PokemonState:
    """Convert an IPC pokemon dict + side conditions into a PokemonState."""
    hp = pokemon.get("hp", 1)
    maxhp = pokemon.get("maxhp", 1) or 1
    hp_pct = int(hp / maxhp * 100)

    types = [_PS_TYPE_MAP[t] for t in pokemon.get("types", ["Normal"])
             if t in _PS_TYPE_MAP]

    ability_id = pokemon.get("ability", "")
    ability = _ABILITY_NAME_MAP.get(ability_id, ability_id.title())

    status_raw = pokemon.get("status", "") or ""
    status = _PS_STATUS_MAP.get(status_raw) if status_raw else None

    move_slots = pokemon.get("moveSlots", [])
    moves = []
    for slot in move_slots:
        mi = get_move_info(slot.get("id", ""))
        if mi is not None:
            moves.append(mi)

    item = pokemon.get("item") or None
    last_item = pokemon.get("lastItem") or None

    gender_raw = pokemon.get("gender", "") or ""
    if gender_raw == "M":
        gender = "male"
    elif gender_raw == "F":
        gender = "female"
    else:
        gender = "genderless"

    level = pokemon.get("set", {}).get("level", 50) if pokemon.get("set") else 50
    speed = pokemon.get("speed", 100)

    is_first_turn = pokemon.get("activeTurns", 1) == 1

    boosts = pokemon.get("boosts", {})
    atk_s  = boosts.get("atk", 0)
    def_s  = boosts.get("def", 0)
    spa_s  = boosts.get("spa", 0)
    spd_s  = boosts.get("spd", 0)
    spe_s  = boosts.get("spe", 0)
    acc_s  = boosts.get("accuracy", 0)
    eva_s  = boosts.get("evasion", 0)

    volatiles = pokemon.get("volatiles", {}) or {}
    stockpile_entry = volatiles.get("stockpile")
    stockpile_count = stockpile_entry.get("level", 0) if isinstance(stockpile_entry, dict) else (1 if stockpile_entry else 0)

    # Side conditions (reflect/lightscreen/safeguard/mist on this pokemon's side)
    sc = side_conditions
    under_reflect      = "reflect" in sc
    under_light_screen = "lightscreen" in sc
    under_safeguard    = "safeguard" in sc
    under_mist         = "mist" in sc

    # Protect consecutive: check if 'stall' volatile is present
    protected_consecutive = 1 if ("stall" in volatiles or "protect" in volatiles) else 0

    has_other_pokemon = any(
        p.get("hp", 0) > 0 and not p.get("isActive")
        for p in side.get("pokemon", [])
    )

    last_move = _parse_last_move(pokemon)

    return PokemonState(
        hp_pct=hp_pct,
        types=types,
        ability=ability,
        status=status,
        moves=moves,
        held_item=item,
        used_item=last_item,
        level=level,
        gender=gender,
        speed=speed,
        is_first_turn=is_first_turn,
        atk_stage=atk_s,
        def_stage=def_s,
        spa_stage=spa_s,
        spd_stage=spd_s,
        spe_stage=spe_s,
        acc_stage=acc_s,
        eva_stage=eva_s,
        confused="confusion" in volatiles,
        infatuated="attract" in volatiles,
        cursed="curse" in volatiles,
        stockpile_count=stockpile_count,
        protected_consecutive=protected_consecutive,
        under_safeguard=under_safeguard,
        under_reflect=under_reflect,
        under_light_screen=under_light_screen,
        under_substitute="substitute" in volatiles,
        under_ingrain="ingrain" in volatiles,
        under_focus_energy="focusenergy" in volatiles,
        under_mist=under_mist,
        under_nightmare="nightmare" in volatiles,
        under_perish_song="perishsong" in volatiles,
        under_leech_seed="leechseed" in volatiles,
        under_disable="disable" in volatiles,
        under_encore="encore" in volatiles,
        under_torment="torment" in volatiles,
        under_foresight=("foresight" in volatiles or "odorsleuth" in volatiles),
        under_mean_look=("meanlook" in volatiles or "spiderweb" in volatiles),
        under_lock_on=("lockon" in volatiles or "mindreader" in volatiles),
        under_attract="attract" in volatiles,
        under_yawn="yawn" in volatiles,
        under_future_sight="futuresight" in volatiles,
        imprison_active="imprison" in volatiles,
        taunted="taunt" in volatiles,
        has_other_pokemon=has_other_pokemon,
        last_move_used=last_move,
    )


def build_battle_context(
    state: dict,
    move_action: str,
    player: int = 1,
) -> Optional[BattleContext]:
    """Build a BattleContext for scoring one move with ai_flags.score_move().

    Args:
        state: dict returned by parse_ipc_response (with p1_dmg_calcs).
        move_action: "move N" string (1-based slot index).
        player: 1 = p1 is the user, 2 = p2 is the user.

    Returns:
        BattleContext, or None if the move cannot be resolved.
    """
    battle = state["battle"]
    sides = battle.get("sides", [])
    if len(sides) < 2:
        return None

    user_side_idx   = 0 if player == 1 else 1
    target_side_idx = 1 - user_side_idx

    user_pokemon   = _get_active_pokemon(sides[user_side_idx])
    target_pokemon = _get_active_pokemon(sides[target_side_idx])
    if user_pokemon is None or target_pokemon is None:
        return None

    # Resolve move slot
    parts = move_action.split()
    if len(parts) != 2 or parts[0] != "move":
        return None
    slot_idx = int(parts[1]) - 1
    move_slots = user_pokemon.get("moveSlots", [])
    if slot_idx < 0 or slot_idx >= len(move_slots):
        return None

    move_id = move_slots[slot_idx].get("id", "")
    move_info = get_move_info(move_id)
    if move_info is None:
        return None

    # Side conditions for each side
    def _sc(side):
        return side.get("sideConditions", {}) or {}

    user_state   = _build_pokemon_state(
        user_pokemon,   sides[user_side_idx],   _sc(sides[user_side_idx]))
    target_state = _build_pokemon_state(
        target_pokemon, sides[target_side_idx], _sc(sides[target_side_idx]))

    # Weather
    weather_str = battle.get("field", {}).get("weather", "") or ""
    weather = _PS_WEATHER_MAP.get(weather_str, Weather.NONE)

    # Damage percentages: convert raw HP damage to % of target's current HP
    calcs_key = "p1_dmg_calcs" if player == 1 else "p2_dmg_calcs"
    raw_calcs = state.get(calcs_key, {})
    target_maxhp = target_pokemon.get("maxhp", 1) or 1
    move_damage_pcts: dict = {}
    for all_slots in user_pokemon.get("moveSlots", []):
        slot_id = all_slots.get("id", "")
        slot_mi = get_move_info(slot_id)
        if slot_mi is None:
            continue
        # Find the matching action key (e.g., "move 1")
        for i, s in enumerate(user_pokemon.get("moveSlots", []), 1):
            if s.get("id") == slot_id:
                raw = raw_calcs.get(f"move {i}", 0) or 0
                move_damage_pcts[slot_mi.name] = raw / target_maxhp * 100
                break

    is_first_battle_turn = battle.get("turn", 1) <= 1

    return BattleContext(
        user=user_state,
        target=target_state,
        move=move_info,
        weather=weather,
        is_first_battle_turn=is_first_battle_turn,
        move_damage_pcts=move_damage_pcts,
    )


def select_move_with_ai_flags(
    state: dict,
    move_actions: list,
    player: int = 1,
    active_flags: list = None,
) -> str:
    """Score all move_actions with ai_flags and return the highest-scoring one.

    Falls back to the first action if no scores can be computed.

    Args:
        state: parse_ipc_response state dict.
        move_actions: list of "move N" action strings.
        player: 1 = p1, 2 = p2.
        active_flags: AI flags to apply (defaults to [0, 1, 2]).
    """
    if active_flags is None:
        active_flags = [0, 1, 2]

    if not move_actions:
        return None

    rng = make_rng()
    best_action = move_actions[0]
    best_score = float("-inf")

    for action in move_actions:
        ctx = build_battle_context(state, action, player)
        if ctx is None:
            continue
        sc = score_move(ctx, active_flags, rng)
        if sc > best_score:
            best_score = sc
            best_action = action

    return best_action


class PokemonMCTS(MCTS):
    """Concrete MCTS for Pokémon battles.

    State representation: dict returned by parse_ipc_response.
    Actions: "move N" or "switch N" strings.
    The opponent uses its first available move, or its first available switch
    if it has no moves (e.g. after a faint), or nothing if it has neither.
    """

    def __init__(self, ipc, node_script_path=None, num_workers=1, **kwargs):
        """
        Args:
            ipc: NodeIPC instance for sending battle commands.
            node_script_path: Path to Connection.js. Required for parallel search.
            num_workers: Number of parallel MCTS search processes (root parallelism).
            **kwargs: Forwarded to MCTS (exploration_weight, simulation_depth_limit).
        """
        super().__init__(**kwargs)
        self.ipc = ipc
        self.node_script_path = node_script_path
        self.num_workers = num_workers

    def search(self, initial_state, iterations, player=1):
        if self.num_workers <= 1 or self.node_script_path is None:
            return super().search(initial_state, iterations, player)

        root = MCTSNode(state=initial_state,
                        untried_actions=self.get_legal_actions(initial_state))
        sem = threading.Semaphore(iterations)

        threads = [
            threading.Thread(
                target=_mcts_tree_worker,
                args=(self.node_script_path, root, sem, player,
                      self.exploration_weight, self.simulation_depth_limit),
                daemon=True,
            )
            for _ in range(self.num_workers)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        if not root.children:
            actions = self.get_legal_actions(initial_state)
            return (actions[0] if actions else None), root

        #Best action is one with highest average score (consistent with prior implementation)
        #It's unlikely that a bad action just got lucky, since good states are visited often
        #Value differences aren't extreme enough for it to be a lucky draw at the end
        best = max(root.children,
                   key=lambda c: c.value / c.visits if c.visits > 0 else float('-inf'))
        return best.action, root

    def get_legal_actions(self, state: dict) -> list:
        return state["p1_moves"] + state["p1_switches"]

    def _pick_p2_move(self, state: dict) -> Optional[str]:
        """Select the best move for p2 using ai_flags, falling back to first move."""
        if state["p2_moves"]:
            scored = select_move_with_ai_flags(state, state["p2_moves"], player=2)
            return scored if scored is not None else state["p2_moves"][0]
        if state["p2_switches"]:
            return select_switch_in(state, state["p2_switches"], player=2)
        return None

    def apply_action(self, state: dict, action: str) -> dict:
        data = {"battle": state["battle"], "p1": action}
        p2 = self._pick_p2_move(state)
        if p2 is not None:
            data["p2"] = p2
        new_state = parse_ipc_response(self.ipc.send(data))
        # If only p2 needs to act (opponent forced switch), advance automatically.
        while (not new_state["is_over"]
               and not new_state["p1_moves"]
               and not new_state["p1_switches"]):
            data = {"battle": new_state["battle"]}
            p2 = self._pick_p2_move(new_state)
            if p2 is not None:
                data["p2"] = p2
            else:
                break
            new_state = parse_ipc_response(self.ipc.send(data))
        return new_state

    def is_terminal(self, state: dict) -> bool:
        return state["is_over"]

    #new reward function, designed to give an immediate heuristic without playouts
    def get_reward(self, state: dict, player: int = 1) -> float:
        sides = state["battle"].get("sides", [])

        def hp_fraction(pokemon):
            maxhp = pokemon.get("maxhp", 1) or 1
            return pokemon.get("hp", 0) / maxhp

        def ppScore(pokemon):
            score = 0
            for move in pokemon["moveSlots"]:
                used = move["maxpp"] - move["pp"]
                score += (used / move["maxpp"]) ** .5

            return score

        p1_pokemon = sides[0].get("pokemon", [])
        p2_pokemon = sides[1].get("pokemon", [])

        p1_hp_sum = sum(hp_fraction(p) for p in p1_pokemon)
        p2_hp_sum = sum(hp_fraction(p) for p in p2_pokemon)
        p1_fainted = sum(1 for p in p1_pokemon if p.get("hp", 0) <= 0)
        p2_fainted = sum(1 for p in p2_pokemon if p.get("hp", 0) <= 0)

        score = 0

        #win
        if p2_fainted == 6:
            score += 10

        #loss
        if p1_fainted == 6:
            score -= 100

        #Remaining health
        score += p1_hp_sum
        score -= p2_hp_sum * 2

        #fainted pokemon.  No benifit from KOing opponent
        #Losing a single pokemon is penalized more than the benifit of winning, but it will lose pokemon to avoid losing
        score -= p1_fainted * 15

        numP1Statuses = sum(p["status"] != "" for p in p1_pokemon)
        numP2Statuses = sum(p["status"] != "" for p in p2_pokemon)
        score -= numP1Statuses / 2
        score += numP2Statuses / 2

        p1PPScore = sum(ppScore(p) for p in p1_pokemon)
        p2PPScore = sum(ppScore(p) for p in p2_pokemon)
        #My pp score is less important since it's less abusable
        score -= p1PPScore / 10
        score += p2PPScore

        return score if player == 1 else -score

    #old function, designed to work with random playouts
    def get_reward_OLD(self, state: dict, player: int = 1) -> float:
        sides = state["battle"].get("sides", [])
        if len(sides) < 2:
            return 0.0

        def hp_fraction(pokemon):
            maxhp = pokemon.get("maxhp", 1) or 1
            return pokemon.get("hp", 0) / maxhp

        p1_pokemon = sides[0].get("pokemon", [])
        p2_pokemon = sides[1].get("pokemon", [])

        p1_hp_sum = sum(hp_fraction(p) for p in p1_pokemon)
        p2_hp_sum = sum(hp_fraction(p) for p in p2_pokemon)
        p1_fainted = sum(1 for p in p1_pokemon if p.get("hp", 0) <= 0)
        p2_fainted = sum(1 for p in p2_pokemon if p.get("hp", 0) <= 0)

        if p2_fainted == 6:
            score = 10 - p1_fainted * 5
        else:
            score = p1_hp_sum - 2 * p2_hp_sum - 5 * p1_fainted

        #Scaling logic moved to mcts
        """minScore = -(2 * 6 + 5 * 6)
        maxScore = 10
        normalizedScore = ((score - minScore) / (maxScore - minScore)) * 5
        return normalizedScore if player == 1 else -normalizedScore"""
        return score if player == 1 else -score

    def _simulate(self, state: dict, player: int) -> float:
        """Rollout with full one-step lookahead: try all p1 actions, pick best by get_reward."""
        current = state
        depth = 0
        while not self.is_terminal(current) and depth < self.simulation_depth_limit:
            p1_actions = current["p1_moves"] + current["p1_switches"]
            if not p1_actions:
                break

            best_next = None
            best_score = float("-inf")
            for action in p1_actions:
                next_state = self.apply_action(current, action)
                score = self.get_reward(next_state, player)
                if score > best_score:
                    best_score = score
                    best_next = next_state

            current = best_next
            depth += 1

        return self.get_reward_OLD(current, player)


def assemble_team_string(ordered_names: list, box: dict) -> str:
    """Build an IPC team string from an ordered list of Pokémon names."""
    return "]".join(box[p] for p in ordered_names)


def assemble_opponent_string(opp_first: str, opp_dict: dict) -> str:
    """Build an IPC opponent team string with opp_first as the lead Pokémon."""
    others = [p for p in opp_dict if p != opp_first]
    return "]".join(opp_dict[p] for p in [opp_first] + others)


def reorder_team(team: tuple, assignment: dict, opp_first: str) -> list:
    """Reorder team so the Pokémon assigned to counter opp_first leads.

    Falls back to the first team member if no assignment match is found.
    """
    lead = next((p for p, opp in assignment.items() if opp == opp_first), team[0])
    rest = [p for p in team if p != lead]
    return [lead] + rest


def _snapshot_party(side: dict) -> list:
    """Return a list of {name, hp, maxhp, hp_pct, fainted} dicts for all Pokémon on a side."""
    result = []
    for p in side.get("pokemon", []):
        hp = p.get("hp", 0)
        maxhp = p.get("maxhp", 1) or 1
        name = p.get("name") or p.get("species", "?")
        result.append({
            "name": name,
            "hp": hp,
            "maxhp": maxhp,
            "hp_pct": hp / maxhp * 100,
            "fainted": hp <= 0,
        })
    return result


def _snapshot_team_info(side: dict) -> list:
    """Capture full team data from the initial IPC battle state.

    Called once at battle start to record level, ability, item, moves, and stats.
    """
    result = []
    for p in side.get("pokemon", []):
        name = p.get("name") or p.get("species", "?")
        level = p.get("set", {}).get("level", "?")
        ability_id = p.get("ability", "")
        ability = _ABILITY_NAME_MAP.get(ability_id, ability_id)
        item = p.get("item", "")
        moves = [slot.get("move", "") for slot in p.get("moveSlots", [])]
        maxhp = p.get("maxhp", 0)
        stats = p.get("storedStats", {})
        result.append({
            "name": name,
            "level": level,
            "ability": ability,
            "item": item,
            "moves": moves,
            "maxhp": maxhp,
            "atk": stats.get("atk", 0),
            "def": stats.get("def", 0),
            "spa": stats.get("spa", 0),
            "spd": stats.get("spd", 0),
            "spe": stats.get("spe", 0),
        })
    return result


def _resolve_action_name(action: str, state: dict, player: int = 1) -> str:
    """Resolve an action string to a human-readable display name.

    "move N"   → move display name (e.g., "Surf")
    "switch N" → "→ PokemonName"
    """
    battle = state.get("battle", {})
    sides = battle.get("sides", [])
    if len(sides) < player:
        return action
    side = sides[player - 1]
    parts = action.split()
    if len(parts) != 2:
        return action
    kind, n_str = parts
    try:
        n = int(n_str)
    except ValueError:
        return action
    if kind == "move":
        active = _get_active_pokemon(side)
        if active is None:
            return action
        move_slots = active.get("moveSlots", [])
        if 1 <= n <= len(move_slots):
            return move_slots[n - 1].get("move", action)
        return action
    if kind == "switch":
        pokemon_list = side.get("pokemon", [])
        if 1 <= n <= len(pokemon_list):
            p = pokemon_list[n - 1]
            name = p.get("name") or p.get("species", action)
            return f"→ {name}"
        return action
    return action


def play_game(ipc, player_team_str: str, opp_team_str: str,
              mcts: PokemonMCTS, mcts_iterations: int = 10,
              record_path: Optional[str] = None) -> BattleResult:
    """Play one full battle, selecting player actions with MCTS.

    Args:
        ipc: NodeIPC instance.
        player_team_str: IPC team string for the player.
        opp_team_str: IPC team string for the opponent.
        mcts: PokemonMCTS instance used for action selection.
        mcts_iterations: Number of MCTS search iterations per move.
        record_path: If set, write raw per-turn MCTS data to ``{record_path}.json``.
            The ``.json`` extension is appended automatically if not present.

    Returns:
        BattleResult with winner ("p1", "p2", or "unknown") and turn count.
    """
    response = ipc.send({"new": True, "team1": player_team_str, "team2": opp_team_str})
    state = parse_ipc_response(response)
    turns = 0
    recording = record_path is not None
    recorded_turns = [] if recording else None
    p1_team_info = []
    p2_team_info = []

    if recording:
        initial_sides = state["battle"].get("sides", [{}, {}])
        p1_team_info = _snapshot_team_info(initial_sides[0] if initial_sides else {})
        p2_team_info = _snapshot_team_info(initial_sides[1] if len(initial_sides) > 1 else {})

    while not mcts.is_terminal(state):
        if recording:
            battle = state["battle"]
            sides = battle.get("sides", [])
            p1_side = sides[0] if len(sides) > 0 else {}
            p2_side = sides[1] if len(sides) > 1 else {}
            p1_active = _get_active_pokemon(p1_side)
            p2_active = _get_active_pokemon(p2_side)
            p1_active_name = (p1_active.get("name") or p1_active.get("species", "?")) if p1_active else "?"
            p2_active_name = (p2_active.get("name") or p2_active.get("species", "?")) if p2_active else "?"
            p1_party = _snapshot_party(p1_side)
            p2_party = _snapshot_party(p2_side)
            log_before = len(battle.get("log", []))
            action, root = mcts.search(state, mcts_iterations)
            chosen_display = _resolve_action_name(action, state)
            stats_raw = mcts.get_action_statistics(root)
            action_stats = [
                {
                    "action": act,
                    "display": _resolve_action_name(act, state),
                    "visits": visits,
                    "avg_value": avg_val,
                    "chosen": act == action,
                }
                for act, visits, avg_val in stats_raw
            ]

            print(turns, action, chosen_display, action_stats)
        else:
            action = mcts.get_best_action(state, mcts_iterations)

        state = mcts.apply_action(state, action)
        turns += 1

        if recording:
            new_log = state["battle"].get("log", [])[log_before:]
            recorded_turns.append({
                "decision": turns,
                "p1_active": p1_active_name,
                "p2_active": p2_active_name,
                "p1_party": p1_party,
                "p2_party": p2_party,
                "action_stats": action_stats,
                "total_iterations": mcts_iterations,
                "chosen_action": action,
                "chosen_display": chosen_display,
                "log_lines": new_log,
            })

    battle_result = BattleResult(
        winner=state["winner"] or "unknown",
        turns=turns,
        log=state["battle"]["log"],
    )

    if recording:
        out_path = record_path if record_path.endswith(".json") else record_path + ".json"
        record = {
            "winner": battle_result.winner,
            "total_turns": turns,
            "p1_team": p1_team_info,
            "p2_team": p2_team_info,
            "turns": recorded_turns,
            "full_log": state["battle"].get("log", []),
        }
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2)

    return battle_result


def _play_game_worker(node_script_path: str, player_str: str, opp_str: str,
                      mcts_iterations: int, depth_limit: int,
                      record_path: Optional[str]) -> BattleResult:
    """Create own NodeIPC and play one game. Used by simulate_team with num_workers > 1."""
    from NodeIPC import NodeIPC as _NodeIPC
    ipc = _NodeIPC(node_script_path)
    try:
        mcts = PokemonMCTS(ipc, simulation_depth_limit=depth_limit, num_workers=1)
        return play_game(ipc, player_str, opp_str, mcts, mcts_iterations,
                         record_path=record_path)
    finally:
        ipc.close()


def _mcts_tree_worker(node_script_path, root, sem, player,
                      exploration_weight, depth_limit):
    """Thread worker: runs MCTS iterations on the shared tree with per-node locking.

    Selection uses hand-over-hand locking (hold parent, acquire child, release parent).
    Virtual loss and backpropagation use brief per-node locks to avoid bidirectional
    deadlock with the downward selection phase.
    Simulation runs without any lock.
    Requires PYTHON_GIL=0 (Python 3.13 free-threading) for true CPU parallelism.
    """
    from NodeIPC import NodeIPC as _NodeIPC
    ipc = _NodeIPC(node_script_path)
    try:
        local = PokemonMCTS(ipc, exploration_weight=exploration_weight,
                            simulation_depth_limit=depth_limit, num_workers=1)

        while sem.acquire(blocking=False):
            # --- Phase 1: Selection with hand-over-hand locking (downward) ---
            # Hold parent lock, acquire child lock, release parent.
            action = None
            node = root
            while True:
                node.lock.acquire()
                game_over = local.is_terminal(node.state)
                has_untried = not game_over and bool(node.untried_actions)

                if game_over or has_untried:
                    # Expansion point or terminal: pop action if available
                    if has_untried:
                        action = node.untried_actions.pop(0)
                    node.lock.release()
                    break

                # Fully expanded: descend to best child
                next_node = node.best_child(local.exploration_weight)
                if next_node is None:
                    # All expansions in-flight, no children yet; release lock and try again without incrementing semaphore
                    node.lock.release()
                    continue

                node.lock.release()
                node = next_node

            # --- Phase 2a: Expansion IPC call (no lock) ---
            new_state = local.apply_action(node.state, action)

            # --- Phase 4: Simulation (no lock) ---
            reward = local._simulate(new_state, player)

            # --- Phase 5: Backpropagate (brief per-node locks, upward) ---
            # visits already incremented by virtual loss — only add reward.
            n = node
            while n is not None:
                with n.lock:
                    n.visits += 1
                    n.value += reward
                n = n.parent

            # --- Phase 2c: Add child (parent's lock) ---
            child = MCTSNode(state=new_state, parent=node, action=action,
                             untried_actions=local.get_legal_actions(new_state))
            child.visits = 1
            child.value = reward
            with node.lock:
                node.children.append(child)

    finally:
        ipc.close()


def simulate_team(ipc, team_result: tuple, n_games: int = 10,
                  mcts_iterations: int = 10, box: dict = None,
                  opp_dict: dict = None, simulation_depth_limit: int = 10,
                  node_script_path: str = None, num_workers: int = 1,
                  record_path_prefix: str = None) -> list:
    """Simulate n_games full battles for a single team.

    Args:
        ipc: NodeIPC instance (used when num_workers == 1).
        team_result: (score, team, assignment) tuple from select_best_team.
        n_games: Number of games to run.
        mcts_iterations: MCTS iterations per move decision.
        box: BOX Pokémon dict (defaults to MatchupInfo.BOX).
        opp_dict: Opponent Pokémon dict (defaults to MatchupInfo.OPPONENT).
        node_script_path: Path to Connection.js. Required when num_workers > 1.
        num_workers: Number of parallel threads for running games.
        record_path_prefix: If set, each game i writes to ``{prefix}_game{i}.json``.

    Returns:
        List of BattleResult objects, one per game.
    """
    if box is None:
        box = BOX
    if opp_dict is None:
        opp_dict = OPPONENT

    score, team, assignment = team_result
    opp_first = next(iter(opp_dict))

    ordered_team = reorder_team(list(team), assignment, opp_first)
    player_str = assemble_team_string(ordered_team, box)
    opp_str = assemble_opponent_string(opp_first, opp_dict)

    def _record_path_for(i):
        if record_path_prefix is None:
            return None
        if n_games == 1:
            return record_path_prefix
        return f"{record_path_prefix}_game{i}"

    if num_workers > 1 and node_script_path is not None:
        with ProcessPoolExecutor(max_workers=num_workers) as pool:
            futures = [
                pool.submit(_play_game_worker, node_script_path, player_str, opp_str,
                            mcts_iterations, simulation_depth_limit, _record_path_for(i))
                for i in range(n_games)
            ]
            return [f.result() for f in futures]

    # Sequential fallback using the provided ipc
    mcts_inst = PokemonMCTS(ipc, simulation_depth_limit=simulation_depth_limit)
    results = []
    for i in range(n_games):
        result = play_game(ipc, player_str, opp_str, mcts_inst, mcts_iterations,
                           record_path=_record_path_for(i))
        results.append(result)
    return results


def print_simulation_results(team_battles: list) -> None:
    """Print win rates and game stats for each team's simulated battles.

    Args:
        team_battles: list of (team_result, list[BattleResult]) pairs.
    """
    for rank, (team_result, game_results) in enumerate(team_battles, 1):
        score, team, assignment = team_result
        wins = sum(1 for r in game_results if r.winner == "p1")
        n = len(game_results)
        avg_turns = sum(r.turns for r in game_results) / n if n > 0 else 0.0
        print(f"Team #{rank} (selection score={score:.4f}): {wins}/{n} wins ({100 * wins / n:.0f}%)")
        print(f"  Members: {', '.join(team)}")
        print(f"  Assignment: " + ", ".join(f"{p} → {opp}" for p, opp in assignment.items()))
        print(f"  Avg turns per game: {avg_turns:.1f}")
        print()


if __name__ == "__main__":
    import pickle
    import sys
    from NodeIPC import NodeIPC
    from test2 import select_best_team

    NODE_SCRIPT = (sys.argv[1] if len(sys.argv) > 1
                   else "/home/Fracture/WebstormProjects/pokemon-showdown-master/Connection.js")

    print("Loading matchup data...")
    with open("matchup_info.pkl", "rb") as f:
        m = pickle.load(f)

    best_teams = select_best_team(m, 1)

    print("Simulating 10 battles for each of the top 3 teams...")
    ipc = NodeIPC(NODE_SCRIPT)
    try:
        team_battles = []
        for team in best_teams:
            results = simulate_team(ipc, team, n_games=1, mcts_iterations=100000, simulation_depth_limit=50)
            team_battles.append((team, results))
            print(team)
            print(results)
    finally:
        ipc.close()

    print_simulation_results(team_battles)
