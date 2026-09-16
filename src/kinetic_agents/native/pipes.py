"""Opt-in recovery receiver; original experiment transport remains unchanged.

Owns only selector registrations, not process lifetime or model/tool dispatch.
Observation timeouts preserve decoder state. Only actual stdout EOF is terminal.
"""

from collections import deque
import os
import selectors
import time

from kinetic_agents.native.framing import JsonLineDecoder


class PipeReceiver:
    def __init__(self, stdout, stderr, max_frame_bytes=64 * 1024 * 1024):
        self.selector = selectors.DefaultSelector()
        self.selector.register(stdout, selectors.EVENT_READ, "stdout")
        self.selector.register(stderr, selectors.EVENT_READ, "stderr")
        self.decoder = JsonLineDecoder(max_frame_bytes)
        self.ready = deque()
        self.stderr_bytes = 0
        self.stdout_eof = False

    def receive(self, deadline):
        while True:
            if self.ready:
                return self.ready.popleft()
            if self.stdout_eof:
                raise EOFError("JSON transport stdout closed")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("JSON transport observation timeout")
            for key, _ in self.selector.select(min(0.2, remaining)):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    self.selector.unregister(key.fileobj)
                    if key.data == "stdout":
                        self.stdout_eof = True
                        self.decoder.finish()
                elif key.data == "stderr":
                    # Never retain or print potentially sensitive diagnostics.
                    self.stderr_bytes += len(chunk)
                else:
                    self.ready.extend(self.decoder.feed(chunk))

    def close(self):
        self.selector.close()
