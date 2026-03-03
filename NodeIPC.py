import subprocess
import json
import struct
import threading


class NodeIPC:
    def __init__(self, script_path):
        self.proc = subprocess.Popen(
            ["node", script_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,  # let Node print errors to terminal
            bufsize=0
        )
        self.lock = threading.Lock()

    def _read_exact(self, n):
        data = b''
        while len(data) < n:
            chunk = self.proc.stdout.read(n - len(data))
            if not chunk:
                raise RuntimeError("Pipe closed")
            data += chunk
        return data

    def send(self, obj):
        data = json.dumps(obj).encode("utf-8")
        header = struct.pack(">I", len(data))

        with self.lock:
            self.proc.stdin.write(header + data)
            self.proc.stdin.flush()

            reply_len_bytes = self._read_exact(4)
            reply_len = struct.unpack(">I", reply_len_bytes)[0]

            payload = self._read_exact(reply_len)

        return json.loads(payload.decode("utf-8"))

    def close(self):
        self.proc.terminate()
        self.proc.wait()
