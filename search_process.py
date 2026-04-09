"""
search_process.py

Subprocess wrapper for the shallow-search server.

Intentionally imports ONLY stdlib modules so this file can be imported by
normal CPython (battle_mode.py, MatchupInfo.py) without risk of pulling in
free-threaded (python3.14t / PYTHON_GIL=0) code.
"""

import json
import os
import struct
import subprocess

_HERE = os.path.dirname(os.path.abspath(__file__))

_PYTHON_T       = os.path.join(_HERE, '.venv_t', 'bin', 'python3.14t')
_SHALLOW_SERVER = os.path.join(_HERE, 'shallow_search.py')


class ShallowSearchProcess:
    """Wraps a long-lived .venv_t/bin/python3.14t shallow_search.py subprocess.

    Uses the same 4-byte big-endian length + JSON framing as NodeIPC.
    The subprocess runs with PYTHON_GIL=0 for true free-threaded parallelism, each
    worker thread holding its own NodeIPC connection to a separate Node.js process.

    Args:
        node_script_path: Path to Connection.js.
        num_workers: Number of parallel NodeIPC connections inside the subprocess.
        verbose: When False, suppress per-turn diagnostic output from the subprocess
                 (stderr → DEVNULL). Default True preserves live-battle diagnostics.
    """

    def __init__(self, node_script_path: str, num_workers: int = 4,
                 verbose: bool = True):
        env = os.environ.copy()
        env['PYTHON_GIL'] = '0'
        stderr = None if verbose else subprocess.DEVNULL
        self._proc = subprocess.Popen(
            [_PYTHON_T, _SHALLOW_SERVER],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=stderr,
            cwd=_HERE, env=env, bufsize=0,
            start_new_session=True,
        )
        self._node_script = node_script_path
        self._num_workers = num_workers

    def search(self, state: dict) -> str:
        """Run shallow search in the subprocess. Returns best action string."""
        req  = {'node_script': self._node_script, 'state': state,
                'num_workers': self._num_workers}
        data = json.dumps(req).encode()
        self._proc.stdin.write(struct.pack('>I', len(data)) + data)
        self._proc.stdin.flush()
        length = struct.unpack('>I', self._read_exact(4))[0]
        return json.loads(self._read_exact(length))['action']

    def _read_exact(self, n: int) -> bytes:
        buf = b''
        while len(buf) < n:
            chunk = self._proc.stdout.read(n - len(buf))
            if not chunk:
                raise EOFError('[ShallowSearchProcess] subprocess stdout closed unexpectedly')
            buf += chunk
        return buf

    def close(self):
        try:
            self._proc.stdin.close()
        except OSError:
            pass
        self._proc.terminate()
        self._proc.wait()
