from dataclasses import dataclass

from NodeIPC import NodeIPC


@dataclass
class MatchupResult:
    """Result from running MCTS on a single matchup context."""
    score: float        # average get_reward over num_runs MCTS searches
    action_stats: dict  # action str → avg reward (aggregated across all runs)
    num_runs: int
    variance: float


@dataclass
class PrunedMatchupInfo:
    myPokemon: str
    opponentPokemon: str
    none: MatchupResult   # direct 1v1 matchup
    move1: MatchupResult  # switch-in, opponent uses move 1 on turn 1
    move2: MatchupResult  # switch-in, opponent uses move 2 on turn 1
    move3: MatchupResult  # switch-in, opponent uses move 3 on turn 1
    move4: MatchupResult  # switch-in, opponent uses move 4 on turn 1

    def __repr__(self):
        return f"PrunedMatchupInfo({self.myPokemon} vs {self.opponentPokemon})"


_BOX_RAW = """
Zangoose||Leftovers|Immunity|return,shadowball,swordsdance,quickattack||85,85,85,85,85,85||||79|
Whiscash||Leftovers|Oblivious|toxic,protect,earthquake,icebeam||85,85,85,85,85,85||||83|
Beautifly||Leftovers|Swarm|toxic,psychic,hiddenpowerfire,morningsun||85,,85,85,85,85||,2,,30,,30|||
Politoed||ChestoBerry|WaterAbsorb|icebeam,toxic,rest,surf||85,,85,85,85,85||,0,,,,||84|
Exeggutor||Leftovers|Chlorophyll|sleeppowder,synthesis,hiddenpowerfire,psychic||85,,85,85,85,85||,2,,30,,30||83|
Latias||SoulDew|Levitate|recover,calmmind,dragonclaw,refresh||85,,85,85,85,85|F|,0,,,,||67|
Tyranitar||Leftovers|SandStream|icebeam,rockslide,thunderwave,crunch||85,85,85,85,85,85||||74|
Ariados||Leftovers|Insomnia|batonpass,agility,sludgebomb,signalbeam||85,85,85,85,85,85||||98|
Gligar||Leftovers|HyperCutter|earthquake,quickattack,hiddenpowerflying,swordsdance||85,85,85,85,85,85||30,30,30,30,30,||83|
Vigoroth||Leftovers|VitalSpirit|slackoff,return,bulkup,earthquake||85,85,85,85,85,85||||84|
Hypno||Leftovers|Insomnia|calmmind,batonpass,firepunch,psychic||85,,85,85,85,85||,0,,,,||88|
Jirachi||Leftovers|SereneGrace|toxic,psychic,bodyslam,firepunch||85,85,85,85,85,85|N|||73|
Zapdos||Leftovers|Pressure|sleeptalk,thunderbolt,hiddenpowerice,rest||85,,85,85,85,85|N|,2,30,,,||74|
Nosepass||Leftovers|MagnetPull|toxic,explosion,earthquake,rockslide||85,85,85,85,85,85|||||
Magneton||Leftovers|MagnetPull|thunderbolt,protect,hiddenpowerice,toxic||85,,85,85,85,85|N|,2,30,,,||85|
Flygon||Leftovers|Levitate|earthquake,toxic,protect,fireblast||85,85,85,85,85,85||||78|
Claydol||Leftovers|Levitate|earthquake,explosion,psychic,rapidspin||85,85,85,85,85,85|N|||81|
""".strip().split('\n')

_OPP_RAW = """
Sceptile||PetayaBerry|Overgrow|substitute,hiddenpowerice,leafblade,thunderpunch||81,,85,85,85,85||,2,30,,,||82|
Bellossom||Leftovers|Chlorophyll|hiddenpowerfire,magicalleaf,moonlight,leechseed||85,,85,85,85,85||,2,,30,,30||93|
Lapras||Leftovers|WaterAbsorb|toxic,healbell,thunderbolt,icebeam||85,,85,85,85,85||,0,,,,||78|
Walrein||Leftovers|ThickFat|surf,icebeam,protect,toxic||85,,85,85,85,85||,0,,,,||80|
Noctowl||Leftovers|Insomnia|toxic,whirlwind,return,hiddenpowerfire||85,85,85,85,85,85||,30,,30,,30||92|
Linoone||SilkScarf|Pickup|bellydrum,hiddenpowerground,shadowball,extremespeed||81,85,85,85,85,85||,,,30,30,||82|
""".strip().split('\n')


def get_teams_at_level(multiplier: float = 1.0,
                       box_raw=None, opp_raw=None) -> tuple:
    """Return (box_dict, opp_dict) with each pokemon's level = int(natural_level * multiplier).

    Each pokemon's natural level is read from the second-to-last pipe-delimited field of
    its data string. Empty fields default to 100 (Pokemon Showdown default level).

    box_raw / opp_raw: optional list of pipe strings to use instead of _BOX_RAW / _OPP_RAW.
    """
    if box_raw is None:
        box_raw = _BOX_RAW
    if opp_raw is None:
        opp_raw = _OPP_RAW

    def apply_level(raw_list, multiplier=1.0):
        result = []
        for x in raw_list:
            y = x.split('|')
            try:
                natural = int(y[-2]) if y[-2].strip() else 100
            except ValueError:
                natural = 100
            y[-2] = str(max(1, int(natural * multiplier)))
            result.append('|'.join(y))
        return {x.split('|')[0]: x for x in result}
    #No opponent multiplier, since only box should be affected
    return apply_level(box_raw, multiplier), apply_level(opp_raw)


# Backward-compatible module-level dicts at natural levels (multiplier=1.0)
BOX, OPPONENT = get_teams_at_level(1.0)


# ---------------------------------------------------------------------------
# Matchup-specific reward function
# ---------------------------------------------------------------------------

def matchup_reward(state: dict, player: int = 1) -> float:
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

    p1 = p1_pokemon[0]
    p2 = p2_pokemon[0]

    p1_hp_sum = hp_fraction(p1)
    p2_hp_sum = hp_fraction(p2)
    p1_fainted = p1_hp_sum == 0
    p2_fainted = p2_hp_sum == 0

    score = 0

    #Winning and losing are less important metrics for 1v1
    #win
    if p2_fainted:
        score += 1

    #loss
    if p1_fainted:
        score -= 1

    #Remaining health
    score += p1_hp_sum
    score -= p2_hp_sum * 2

    numP1Statuses = p1["status"] != ""
    numP2Statuses = p2["status"] != ""
    score -= numP1Statuses / 2
    score += numP2Statuses / 2

    p1PPScore = ppScore(p1)
    p2PPScore = ppScore(p2)
    score -= p1PPScore / 10
    score += p2PPScore

    numP1Boosts = sum(p1["boosts"].values())
    numP2Boosts = sum(p2["boosts"].values())
    score += numP1Boosts * .2
    score -= numP2Boosts * .2

    return score if player == 1 else -score


# ---------------------------------------------------------------------------
# Standalone worker functions
# ---------------------------------------------------------------------------

#Don't need to pass both mcts and mcts_forbidden, just pass the one that should be used
def _run_mcts_context(ipc, mcts, team1, team2,
                      setup_p1, setup_p2, mcts_iterations, num_runs,
                      max_runs=8, sem_threshold=0.1,
                      turn_limit=10, convergence_penalty=1.0):
    """Play out battles using MCTS until the score estimate converges. Returns MatchupResult.

    Runs at least num_runs times, then continues until the standard error of the mean
    (SEM = sample_std_dev / sqrt(n)) falls below sem_threshold, or max_runs is reached.
    Each individual battle is cut off at turn_limit turns to prevent stalling.
    If the result does not converge, avg_score is penalised by convergence_penalty * final_sem.

    Each run:
      - Starts a fresh battle and applies the optional setup turn.
      - Loops: MCTS search → apply best action → advance state, up to turn_limit turns.
        Also stops when the battle ends or no legal actions remain.
      - Scores the final state with matchup_reward.
    Action stats are captured from the first MCTS search (opening position).

    Args:
        ipc: NodeIPC instance.
        mcts: PokemonMCTS instance (with reward_fn=matchup_reward for matchup contexts).
        team1: IPC team string for player.
        team2: IPC team string for opponent.
        setup_p1: Player action for setup turn (None for direct matchup).
        setup_p2: Opponent action for setup turn (None for direct matchup).
        mcts_iterations: MCTS iterations per search.
        num_runs: Minimum number of runs before checking convergence.
        max_runs: Hard cap on total runs regardless of SEM.
        sem_threshold: Stop early when SEM of scores drops below this value.
        turn_limit: Maximum turns per battle before forcing evaluation.
        convergence_penalty: Multiplier applied to final_sem when result did not converge.
    """
    import math
    from battle_sim import parse_ipc_response

    scores = []
    aggregated = {}  # action -> [total_value, total_visits]

    while True:
        resp = ipc.send({"new": True, "team1": team1, "team2": team2})
        state = parse_ipc_response(resp)

        if setup_p1 is not None:
            data = {"battle": state["battle"], "p1": setup_p1, "p2": setup_p2}
            state = parse_ipc_response(ipc.send(data))

        first_search = True
        turn = 0
        while not state["is_over"]:
            if not mcts.get_legal_actions(state) or turn >= turn_limit:
                break

            best_action, root = mcts.search(state, mcts_iterations)

            if first_search:
                for child in root.children:
                    if child.action not in aggregated:
                        aggregated[child.action] = [0.0, 0]
                    aggregated[child.action][0] += child.value
                    aggregated[child.action][1] += child.visits
                first_search = False

            state = mcts.apply_action(state, best_action)
            turn += 1

        scores.append(matchup_reward(state))
        n = len(scores)

        if n >= max_runs:
            break
        if n >= num_runs and n >= 2:
            mean = sum(scores) / n
            variance = sum((s - mean) ** 2 for s in scores) / (n - 1)
            sem = math.sqrt(variance / n)
            if sem < sem_threshold:
                break

    n = len(scores)
    avg_score = sum(scores) / n
    if n >= 2:
        mean = avg_score
        variance = sum((s - mean) ** 2 for s in scores) / (n - 1)
        final_sem = math.sqrt(variance / n)
    else:
        final_sem = 0.0
    if final_sem >= sem_threshold:
        avg_score -= convergence_penalty * final_sem

    action_stats = {
        a: vals[0] / vals[1]
        for a, vals in aggregated.items() if vals[1] > 0
    }
    return MatchupResult(score=avg_score, action_stats=action_stats, num_runs=len(scores), variance=round(final_sem, 4))


# ---------------------------------------------------------------------------
# MatchupInfo class
# ---------------------------------------------------------------------------

class MatchupInfo:
    def __init__(self, node_script_path: str, level_multiplier: float = 1.0,
                 num_workers: int = 1, mcts_iterations: int = 100, num_runs: int = 3,
                 max_runs: int = 8, sem_threshold: float = 0.1,
                 turn_limit: int = 10, convergence_penalty: float = 1.0,
                 box_raw=None, opp_raw=None):
        """Generate full matchup data for all BOX vs OPPONENT combinations using MCTS.

        Args:
            node_script_path: Path to Connection.js (Node IPC script).
            level_multiplier: Scale factor applied to BOX pokemon's natural level.
                              0.6 → 60% of natural level, 1.0 → natural level.
            num_workers: Number of MCTS worker threads per search (passed to PokemonMCTS).
            mcts_iterations: MCTS iterations per run per matchup context.
            num_runs: Minimum runs per matchup before checking for convergence.
            max_runs: Hard cap on runs per matchup regardless of SEM.
            sem_threshold: Stop adding runs when SEM of scores drops below this value.
            turn_limit: Maximum turns per battle before forcing evaluation of current state.
            convergence_penalty: Multiplier on final SEM applied when a matchup does not converge.
            box_raw: Optional list of PS pipe strings overriding _BOX_RAW.
            opp_raw: Optional list of PS pipe strings overriding _OPP_RAW.
        """
        from battle_sim import PokemonMCTS
        self.node_script_path = node_script_path
        self.level_multiplier = level_multiplier
        self.box, self.opp = get_teams_at_level(level_multiplier, box_raw=box_raw, opp_raw=opp_raw)

        self.rawMatchupInfo = {p: {p2: {} for p2 in self.opp} for p in self.box}

        print(f"Generating matchup info (multiplier={level_multiplier}, "
              f"mcts_workers={num_workers}, mcts_iterations={mcts_iterations}, "
              f"min_runs={num_runs}, max_runs={max_runs}, sem_threshold={sem_threshold})...")

        ipc = NodeIPC(node_script_path)
        try:
            mcts = PokemonMCTS(ipc, node_script_path=node_script_path,
                               simulation_depth_limit=10, num_workers=num_workers,
                               reward_fn=matchup_reward, rollout_reward_fn=matchup_reward)
            #Need to disable "switch 2" and not 1, because when the first switch happens the Pokemon actually swap slots
            mcts_forbidden = PokemonMCTS(ipc, node_script_path=node_script_path,
                                         simulation_depth_limit=10, num_workers=num_workers,
                                         forbidden_actions={"switch 2"},
                                         reward_fn=matchup_reward, rollout_reward_fn=matchup_reward)
            completed = 0
            total = len(self.box) * len(self.opp)
            for p in self.box:
                for p2 in self.opp:
                    contexts = {}
                    contexts[None] = _run_mcts_context(
                        ipc, mcts,
                        team1=self.box[p], team2=self.opp[p2],
                        setup_p1=None, setup_p2=None,
                        mcts_iterations=mcts_iterations, num_runs=num_runs,
                        max_runs=max_runs, sem_threshold=sem_threshold,
                        turn_limit=turn_limit, convergence_penalty=convergence_penalty,
                    )
                    print(p, p2, contexts[None])

                    #Ignore switch in context for now
                    """for move_i in range(1, 5):
                        contexts[move_i] = _run_mcts_context(
                            ipc, mcts_forbidden,
                            team1=self.box[p] + ']' + self.box[p], team2=self.opp[p2],
                            setup_p1="switch 2", setup_p2=f"move {move_i}",
                            mcts_iterations=mcts_iterations, num_runs=num_runs,
                        )"""
                    self.rawMatchupInfo[p][p2] = contexts
                    completed += 1
                    if completed % 10 == 0 or completed == total:
                        print(f"  {completed}/{total} pairs done")
        finally:
            ipc.close()

        self.prunedMatchupInfo = self.makePrunedMatchupInfo()

    def makePrunedMatchupInfo(self):
        result = []
        for p1, d1 in self.rawMatchupInfo.items():
            for p2, contexts in d1.items():
                result.append(PrunedMatchupInfo(
                    myPokemon=p1,
                    opponentPokemon=p2,
                    none=contexts[None],
                    #not using switch-in contexts for now
                    move1=None, move2=None, move3=None, move4=None,
                    #move1=contexts[1],
                    #move2=contexts[2],
                    #move3=contexts[3],
                    #move4=contexts[4],
                ))
        return result

    def __getstate__(self):
        state = self.__dict__.copy()
        return state

    def __setstate__(self, state):
        self.__dict__.update(state)
