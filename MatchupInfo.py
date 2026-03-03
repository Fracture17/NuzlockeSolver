from collections import defaultdict


BOX = """
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
BOX = {x.split('|')[0]: x for x in BOX}

OPPONENT = """
Sceptile||PetayaBerry|Overgrow|substitute,hiddenpowerice,leafblade,thunderpunch||81,,85,85,85,85||,2,30,,,||82|
Bellossom||Leftovers|Chlorophyll|hiddenpowerfire,magicalleaf,moonlight,leechseed||85,,85,85,85,85||,2,,30,,30||93|
Lapras||Leftovers|WaterAbsorb|toxic,healbell,thunderbolt,icebeam||85,,85,85,85,85||,0,,,,||78|
Walrein||Leftovers|ThickFat|surf,icebeam,protect,toxic||85,,85,85,85,85||,0,,,,||80|
Noctowl||Leftovers|Insomnia|toxic,whirlwind,return,hiddenpowerfire||85,85,85,85,85,85||,30,,30,,30||92|
Linoone||SilkScarf|Pickup|bellydrum,hiddenpowerground,shadowball,extremespeed||81,85,85,85,85,85||,,,30,30,||82|
""".strip().split('\n')
OPPONENT = {x.split('|')[0]: x for x in OPPONENT}


#enumerates all move possibilities
#first number is move used on first turn, second is move used on subsequent turns
STRATEGIES = [x + y for x in "1234" for y in "1234"]


class MatchupInfo:
    def __init__(self, ipc):
        self.ipc = ipc
        self.rawMatchupInfo = {}
        for p in BOX:
            print(p)
            self.rawMatchupInfo[p] = {}
            for p2 in OPPONENT:
                self.rawMatchupInfo[p][p2] = {}
                for s in STRATEGIES:
                    self.rawMatchupInfo[p][p2][s] = {}
                    for s2 in STRATEGIES:
                        self.rawMatchupInfo[p][p2][s][s2] = {}

                        self.runMatchup(p, s, p2, s2)
                        self.runMatchupSwitchin(p, s, p2, s2)

        self.prunedMatchupInfo = self.makePrunedMatchupInfo()

    def runMatchup(self, p, s, p2, s2):
        # make new battle
        response = self.ipc.send({"new": True, "team1": BOX[p], "team2": OPPONENT[p2]})
        hpResults = self._runMatchup(response, s, s2)
        self.rawMatchupInfo[p][p2][s][s2][None] = hpResults

    def runMatchupSwitchin(self, p, s, p2, s2):
        response = self.ipc.send({"new": True, "team1": BOX[p] + ']' + BOX[p], "team2": OPPONENT[p2]})
        battleState = response["result"]["battle"]

        for i in range(1, 5):
            #perform switch against each possible move
            data = {"battle": battleState}
            data["p1"] = "switch 2"
            data["p2"] = f"move {i}"
            response = self.ipc.send(data)

            hpResults = self._runMatchup(response, s, s2)
            self.rawMatchupInfo[p][p2][s][s2][i] = hpResults

    def _runMatchup(self, response, s, s2):
        # TODO: change to when battle ends or 10 turns pass
        hpResults = []
        for i in range(10):
            battleState = response["result"]["battle"]
            data = {"battle": battleState}

            if "p1Moves" in response["result"]:
                if i == 0:
                    choice = int(s[0])
                else:
                    choice = int(s[1])
                data["p1"] = "move " + str(choice)

            if "p2Moves" in response["result"]:
                if i == 0:
                    choice = int(s2[0])
                else:
                    choice = int(s2[1])
                data["p2"] = "move " + str(choice)

            response = self.ipc.send(data)
            battleState = response["result"]["battle"]

            sides = battleState["sides"]
            p1State = sides[0]["pokemon"][0]
            p1HPPrecent = p1State["hp"] / p1State["maxhp"]
            p2State = sides[1]["pokemon"][0]
            p2HPPrecent = p2State["hp"] / p2State["maxhp"]
            hpResults.append((p1HPPrecent, p2HPPrecent))

        return hpResults

    def __getstate__(self):
        state = self.__dict__.copy()
        del state['ipc']
        return state

    def __setstate__(self, state):
        self.__dict__.update(state)
        self.ipc = None

    def makePrunedMatchupInfo(self):
        prunedMatchupInfo = defaultdict(dict)
        for p1, d1 in self.rawMatchupInfo.items():
            for p2, d2 in d1.items():
                for switchContext in (None, 1, 2, 3, 4):
                    prunedData = self.pruneSingleMatchup(d2, switchContext)
                    prunedMatchupInfo[p1 + '|' + p2][switchContext] = prunedData

        return prunedMatchupInfo

    def pruneSingleMatchup(self, matchupData, switchContext):
        prunedData = {}
        for i in range(10):
            bestStrategy = self.pruneSingleTurn(matchupData, switchContext, i)
            prunedData[i] = bestStrategy

        return prunedData

    def pruneSingleTurn(self, matchupData, switchContext, turn):
        #for each of my strategies, find the worst outcome that my oppoennt can inflict
        #The best of all the worst outcomes is the best outcome that I can force (minimax)

        bestStrategy = None
        bestStrategyRatio = -float('inf')
        for myStrategy, d1 in matchupData.items():
            worstOutcome = None
            worstOutcomeRatio = float('inf')
            for opponentStrategy, d2 in d1.items():
                outcome = d2[switchContext][turn]
                #ratio of their lost hp to mine, higher is better
                hpRatio = (1 - outcome[1]) / (1 - outcome[0] + .01)
                if hpRatio < worstOutcomeRatio:
                    worstOutcomeRatio = hpRatio
                    worstOutcome = (outcome, myStrategy, opponentStrategy)

            if worstOutcomeRatio > bestStrategyRatio:
                bestStrategyRatio = worstOutcomeRatio
                bestStrategy = worstOutcome

        return bestStrategy
