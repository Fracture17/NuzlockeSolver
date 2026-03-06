from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Optional

from NodeIPC import NodeIPC


@dataclass
class TurnOutcome:
    myHP: float
    opponentHP: float
    myStrategy: str
    opponentStrategy: str


@dataclass
class PrunedMatchupInfo:
    myPokemon: str
    opponentPokemon: str
    none: list
    move1: list
    move2: list
    move3: list
    move4: list

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


def get_teams_at_level(multiplier: float = 1.0) -> tuple:
    """Return (box_dict, opp_dict) with each pokemon's level = int(natural_level * multiplier).

    Each pokemon's natural level is read from the second-to-last pipe-delimited field of
    its data string. Empty fields default to 100 (Pokemon Showdown default level).
    """
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
    return apply_level(_BOX_RAW, multiplier), apply_level(_OPP_RAW)


# Backward-compatible module-level dicts at natural levels (multiplier=1.0)
BOX, OPPONENT = get_teams_at_level(1.0)


# Enumerates all move possibilities:
# first number = move used on first turn, second = move used on subsequent turns
STRATEGIES = [x + y for x in "1234" for y in "1234"]


# ---------------------------------------------------------------------------
# Standalone worker functions (module-level so they can be used with threads)
# ---------------------------------------------------------------------------

def _run_matchup_ipc(response, s, s2, ipc):
    """Standalone version of _runMatchup — uses a passed ipc instance."""
    hpResults = []
    for i in range(10):
        battleState = response["result"]["battle"]
        data = {"battle": battleState, "forceAverageRandom": True}

        if "p1Moves" in response["result"]:
            choice = int(s[0]) if i == 0 else int(s[1])
            data["p1"] = "move " + str(choice)

        if "p2Moves" in response["result"]:
            choice = int(s2[0]) if i == 0 else int(s2[1])
            data["p2"] = "move " + str(choice)

        response = ipc.send(data)
        battleState = response["result"]["battle"]

        sides = battleState["sides"]
        p1State = sides[0]["pokemon"][0]
        p1HPPercent = p1State["hp"] / p1State["maxhp"]
        p2State = sides[1]["pokemon"][0]
        p2HPPercent = p2State["hp"] / p2State["maxhp"]
        hpResults.append((p1HPPercent, p2HPPercent))

    return hpResults


def _run_pair(node_script_path, p, p2, box, opp, strategies):
    """Worker: creates its own IPC and runs all strategy combos for one (p1, p2) pair."""
    ipc = NodeIPC(node_script_path)
    try:
        results = {s: {s2: {} for s2 in strategies} for s in strategies}
        for s in strategies:
            for s2 in strategies:
                # Direct matchup
                resp = ipc.send({"new": True, "team1": box[p], "team2": opp[p2]})
                results[s][s2][None] = _run_matchup_ipc(resp, s, s2, ipc)

                # Switch-in matchup
                resp = ipc.send({"new": True, "team1": box[p] + ']' + box[p], "team2": opp[p2]})
                bs = resp["result"]["battle"]
                for i in range(1, 5):
                    data = {"battle": bs, "p1": "switch 2",
                            "p2": f"move {i}", "forceAverageRandom": True}
                    resp = ipc.send(data)
                    results[s][s2][i] = _run_matchup_ipc(resp, s, s2, ipc)

        return p, p2, results
    finally:
        ipc.close()


# ---------------------------------------------------------------------------
# MatchupInfo class
# ---------------------------------------------------------------------------

class MatchupInfo:
    def __init__(self, node_script_path: str, level_multiplier: float = 1.0,
                 num_workers: int = 1):
        """Generate full matchup data for all BOX vs OPPONENT combinations.

        Args:
            node_script_path: Path to Connection.js (Node IPC script).
            level_multiplier: Scale factor applied to BOX pokemon's natural level.
                                Opponent pokemon's natural level is unchanged.
                              0.6 → 60% of natural level, 1.0 → natural level.
            num_workers: Number of parallel threads. Each thread creates its own IPC.
        """
        self.node_script_path = node_script_path
        self.level_multiplier = level_multiplier
        self.box, self.opp = get_teams_at_level(level_multiplier)

        self.rawMatchupInfo = {p: {p2: {} for p2 in self.opp} for p in self.box}

        print(f"Generating matchup info (multiplier={level_multiplier}, workers={num_workers})...")
        with ProcessPoolExecutor(max_workers=num_workers) as pool:
            futures = {
                pool.submit(_run_pair, node_script_path, p, p2,
                            self.box, self.opp, STRATEGIES): (p, p2)
                for p in self.box for p2 in self.opp
            }
            completed = 0
            total = len(futures)
            for future in as_completed(futures):
                p, p2, pair_results = future.result()
                self.rawMatchupInfo[p][p2] = pair_results
                completed += 1
                if completed % 10 == 0 or completed == total:
                    print(f"  {completed}/{total} pairs done")

        self.prunedMatchupInfo = self.makePrunedMatchupInfo()

    def makePrunedMatchupInfo(self):
        result = []
        for p1, d1 in self.rawMatchupInfo.items():
            for p2, d2 in d1.items():
                contexts = {}
                for switchContext in (None, 1, 2, 3, 4):
                    contexts[switchContext] = self.pruneSingleMatchup(d2, switchContext)

                result.append(PrunedMatchupInfo(
                    myPokemon=p1,
                    opponentPokemon=p2,
                    none=contexts[None],
                    move1=contexts[1],
                    move2=contexts[2],
                    move3=contexts[3],
                    move4=contexts[4],
                ))

        return result

    def pruneSingleMatchup(self, matchupData, switchContext):
        turns = []
        for i in range(10):
            bestStrategy = self.pruneSingleTurn(matchupData, switchContext, i)
            outcome, myStrategy, opponentStrategy = bestStrategy
            turns.append(TurnOutcome(
                myHP=outcome[0],
                opponentHP=outcome[1],
                myStrategy=myStrategy,
                opponentStrategy=opponentStrategy,
            ))

        return turns

    def pruneSingleTurn(self, matchupData, switchContext, turn):
        # For each of my strategies, find the worst outcome the opponent can inflict.
        # The best of all the worst outcomes is the best outcome I can force (minimax).
        bestStrategy = None
        bestStrategyRatio = -float('inf')
        for myStrategy, d1 in matchupData.items():
            worstOutcome = None
            worstOutcomeRatio = float('inf')
            for opponentStrategy, d2 in d1.items():
                outcome = d2[switchContext][turn]
                hpRatio = (1 - outcome[1]) / (1 - outcome[0] + .01)
                if hpRatio < worstOutcomeRatio:
                    worstOutcomeRatio = hpRatio
                    worstOutcome = (outcome, myStrategy, opponentStrategy)

            if worstOutcomeRatio > bestStrategyRatio:
                bestStrategyRatio = worstOutcomeRatio
                bestStrategy = worstOutcome

        return bestStrategy

    def __getstate__(self):
        state = self.__dict__.copy()
        # node_script_path is safe to pickle (it's a string)
        return state

    def __setstate__(self, state):
        self.__dict__.update(state)
