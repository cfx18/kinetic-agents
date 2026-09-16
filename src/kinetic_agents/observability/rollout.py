"""Live fallback for Codex 0.153.4 resumed threads lacking raw notifications.

Read only NEW bytes in the exact owned rollout returned by thread/resume.
Never walk sessions, replay old history, or serialize reasoning records.
"""

import json
import os
import stat
import threading
import time

from kinetic_agents.observability.review import plain

MAX_FRAME = 64 * 1024 * 1024


class LiveRollout:
    def __init__(self, path, sessions, thread_id, transcript):
        self.path, sessions = plain(path), plain(sessions)
        if not self.path.is_relative_to(sessions) or self.path == sessions:
            raise PermissionError("live rollout must be inside this run's native sessions")
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        self.file = os.fdopen(fd, "rb")
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise PermissionError("owned regular rollout required")
            # Only identity metadata is inspected; old messages are not read.
            header = json.loads(self.file.readline(1024 * 1024))
            if (
                header.get("type") != "session_meta"
                or (header.get("payload") or {}).get("id") != thread_id
            ):
                raise PermissionError("resumed rollout has a foreign session identity")
            self.file.seek(0, os.SEEK_END)
            self.offset = self.file.tell()
        except BaseException:
            self.file.close()
            raise
        self.thread_id, self.transcript = thread_id, transcript
        self.source = self.path.relative_to(transcript.root).as_posix()
        self.buffer, self.error = b"", None
        self.stop = threading.Event()
        transcript.record(
            "live_rollout_attached",
            dict(
                source=self.source,
                offset=self.offset,
                threadId=thread_id,
                history_replayed=False,
                reason="resumed app-server does not emit raw code-mode items in qualified CLI",
            ),
        )
        self.thread = threading.Thread(target=self._read, name="live-owned-rollout", daemon=True)
        self.thread.start()

    def drain(self):
        while True:
            if os.fstat(self.file.fileno()).st_size < self.file.tell():
                raise PermissionError("owned live rollout was truncated")
            chunk = self.file.read(1024 * 1024)
            if not chunk:
                return
            self.buffer += chunk
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                offset, self.offset = self.offset, self.offset + len(line) + 1
                if len(line) > MAX_FRAME:
                    raise ValueError("live rollout frame exceeds bound")
                try:
                    event = json.loads(line)
                except ValueError:
                    self.transcript.record(
                        "capture_gap",
                        {
                            "source": self.source,
                            "byte_offset": offset,
                            "reason": "malformed appended JSONL; content not guessed",
                        },
                    )
                    continue
                item = event.get("payload") or {}
                if event.get("type") == "response_item" and item.get("type") in (
                    "function_call",
                    "custom_tool_call",
                    "function_call_output",
                    "custom_tool_call_output",
                ):
                    self.transcript.incoming(
                        {
                            "method": "rawResponseItem/completed",
                            "params": {
                                "item": item,
                                "threadId": self.thread_id,
                                "turnId": None,
                                "source": self.source,
                                "byte_offset": offset,
                                "source_timestamp": event.get("timestamp"),
                                "capture_source": "live_appended_rollout",
                            },
                        },
                        time.time(),
                    )
            if len(self.buffer) > MAX_FRAME:
                raise ValueError("unterminated live rollout frame exceeds bound")

    def _read(self):
        try:
            while not self.stop.is_set():
                self.drain()
                self.stop.wait(0.1)
            self.drain()
        except Exception as exc:
            self.error = exc

    def check(self):
        if self.error is not None:
            raise RuntimeError(
                "live rollout capture failed: " + type(self.error).__name__
            ) from self.error

    def close(self):
        self.stop.set()
        self.thread.join(timeout=3)
        if self.thread.is_alive():
            raise RuntimeError("live rollout capture did not stop")
        self.file.close()
        if self.buffer:
            self.transcript.record(
                "capture_gap",
                {
                    "source": self.source,
                    "byte_offset": self.offset,
                    "reason": "unterminated final JSONL record",
                    "bytes": len(self.buffer),
                },
            )
        self.check()
