from BattleState import BattleState
from ActionInfo import ActionInfo

import json
from websocket import create_connection


#battleState contains battle state information for the current turn
#actionInfo contains information required to play out the turn in the desired way
#TODO: flesh out info classes once I understand all required information for simulator
def simulateTurn(battleState: BattleState, actionInfo: ActionInfo):
    payload = battleState.serialize() + actionInfo.serialize()
    result = _runSimulator(payload=payload)
    newBattleState = BattleState.deserialize(result[0])
    newActionInfo = ActionInfo.deserialize(result[1])
    return newBattleState, newActionInfo


#Sends battle information to the simulator and lets it simulate the next turn
#Returns the result of the simulation, which should be the new battle state and
#certain information required to decide which actions to take
def _runSimulator(payload, url="ws://localhost:8765", timeout=None):
    ws = create_connection(url, timeout=timeout)
    try:
        # Send JSON message
        ws.send(json.dumps(payload))

        # Block until a reply arrives
        reply = ws.recv()   # This call blocks

        # Attempt JSON parsing
        try:
            return json.loads(reply)
        except json.JSONDecodeError:
            return reply

    finally:
        ws.close()
