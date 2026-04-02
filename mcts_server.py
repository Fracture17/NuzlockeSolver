"""Long-lived MCTS subprocess server.

Launched by MCTSProcess in battle_mode.py using .venv_t/bin/python3.14t.
Reads binary-framed JSON requests from stdin, runs PokemonMCTS.search(),
writes binary-framed JSON responses to stdout.

Protocol: 4-byte big-endian payload length followed by UTF-8 JSON (same framing as NodeIPC).

Request:  {node_script, state, iterations, num_workers}
Response: {action, stats: [{action, visits, avg}, ...]}
"""
import sys
import json
import struct


def _read_exact(n: int) -> bytes:
    buf = b''
    while len(buf) < n:
        chunk = sys.stdin.buffer.read(n - len(buf))
        if not chunk:
            raise EOFError
        buf += chunk
    return buf


def _send(obj: dict) -> None:
    data = json.dumps(obj).encode()
    sys.stdout.buffer.write(struct.pack('>I', len(data)) + data)
    sys.stdout.buffer.flush()


def main():
    from battle_sim import PokemonMCTS
    from NodeIPC import NodeIPC

    while True:
        try:
            length = struct.unpack('>I', _read_exact(4))[0]
        except EOFError:
            break
        req = json.loads(_read_exact(length))

        ipc = NodeIPC(req['node_script'])
        try:
            mcts = PokemonMCTS(ipc, node_script_path=req['node_script'],
                               num_workers=req['num_workers'])
            best_action, root = mcts.search(req['state'], req['iterations'])
        finally:
            ipc.close()

        stats = [
            {'action': c.action,
             'visits': c.visits,
             'avg':    c.value / c.visits if c.visits else 0.0}
            for c in sorted(root.children, key=lambda c: c.visits, reverse=True)
        ]
        _send({'action': best_action, 'stats': stats})


if __name__ == '__main__':
    main()
