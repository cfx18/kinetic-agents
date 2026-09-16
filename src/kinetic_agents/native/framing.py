"""Bounded incremental JSON-line framing for the proposed recovery transport.

Not wired into the original experiment. Callers must consume each feed iterator
before feeding more bytes. The limit is per message, not per received batch.
Errors deliberately exclude payloads: diagnostics can contain private data.
"""

import json


class FrameError(ValueError):
    pass


class JsonLineDecoder:
    def __init__(self, max_frame_bytes=64 * 1024 * 1024):
        if not isinstance(max_frame_bytes, int) or max_frame_bytes <= 0:
            raise ValueError("positive frame limit required")
        self.limit = max_frame_bytes
        self.buffer = bytearray()
        self.failed = False

    def _fail(self, message):
        self.failed = True
        self.buffer.clear()
        raise FrameError(message) from None

    def feed(self, chunk):
        if self.failed:
            raise FrameError("decoder failed; replace transport explicitly")
        start = 0
        while start < len(chunk):
            end = chunk.find(b"\n", start)
            stop = len(chunk) if end < 0 else end
            if len(self.buffer) + stop - start > self.limit:
                self._fail("JSON frame exceeds configured byte limit")
            self.buffer.extend(chunk[start:stop])
            start = stop + 1
            if end < 0:
                break
            if not self.buffer.strip():
                self.buffer.clear()
                continue
            try:
                value = json.loads(self.buffer)
            except (ValueError, UnicodeError, RecursionError):
                self._fail("invalid JSON frame")
            self.buffer.clear()
            if not isinstance(value, dict):
                self._fail("JSON RPC frame must be an object")
            yield value

    def finish(self):
        if self.failed:
            raise FrameError("decoder failed; replace transport explicitly")
        if self.buffer.strip():
            self._fail("stream ended with an incomplete JSON frame")
        self.buffer.clear()
