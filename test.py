from NodeIPC import NodeIPC
from MatchupInfo import MatchupInfo


if __name__ == "__main__":
    ipc = NodeIPC("/home/Fracture/WebstormProjects/pokemon-showdown-master/Connection.js")


    m = MatchupInfo(ipc)

    quit()

    response = ipc.send({"new": True})

    for i in range(2000):
        for k, v in response["result"].items():
            print(k, v)
        print()

        data = {"battle": response["result"]["battle"]}
        if "p1Moves" in response["result"]:
            choice = int(response["result"]["p1Moves"].split(':')[0]) + 1
            data["p1"] = "move " + str(choice)
        elif "p1Switches" in response["result"]:
            choice = int(response["result"]["p1Switches"].split(':')[0]) + 1
            data["p1"] = "switch " + str(choice)

        if "p2Moves" in response["result"]:
            choice = int(response["result"]["p2Moves"].split(':')[0]) + 1
            data["p2"] = "move " + str(choice)
        elif "p2Switches" in response["result"]:
            choice = int(response["result"]["p2Switches"].split(':')[0]) + 1
            data["p2"] = "switch " + str(choice)

        response = ipc.send(data)

    for k, v in response["result"].items():
        print(k, v)

    print(response)

    #response = ipc.send({"command": "another_call"})
    #print("Response:", response)

    ipc.close()
