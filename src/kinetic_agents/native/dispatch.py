"""Bounded transport reader independent of lifecycle/database I/O.

Only display deltas ignored by DynamicClient are discarded. Requests (any id),
responses, errors, item completion and turn completion retain FIFO order. Raw
model text remains in Codex's own transcript; no text enters these metrics.
"""

from collections import deque
import json
import threading
import time

from kinetic_agents.native.pipes import PipeReceiver


DISPLAY_DELTAS = frozenset(
    {
        "item/agentMessage/delta",
        "item/plan/delta",
        "item/reasoning/summaryTextDelta",
        "item/reasoning/textDelta",
        "item/commandExecution/outputDelta",
        "item/fileChange/outputDelta",
    }
)


class DispatchReceiver:
    def __init__(
        self,
        stdout,
        stderr,
        max_frame_bytes=64 * 1024 * 1024,
        *,
        max_messages=4096,
        max_queue_bytes=128 * 1024 * 1024,
        observer=None
    ):
        if max_messages < 1 or max_queue_bytes < max_frame_bytes:
            raise ValueError("bounded queue must accommodate one maximum frame")
        self.source = PipeReceiver(stdout, stderr, max_frame_bytes)
        self.max_messages, self.max_queue_bytes = max_messages, max_queue_bytes
        self.condition = threading.Condition()
        self.ready = deque()
        self.queue_bytes = self.peak_queue_bytes = self.peak_messages = 0
        self.received = self.display_deltas = 0
        self.stopped = False
        self.error = None
        self.last_receipt = None
        self.observer = observer
        self.thread = threading.Thread(
            target=self._read, name="codex-transport-reader", daemon=True
        )
        self.thread.start()

    def _read(self):
        try:
            while not self.stopped:
                try:
                    message = self.source.receive(time.monotonic() + 0.2)
                except TimeoutError:
                    continue  # Partial frame is retained by the existing decoder.
                received_wall, received_mono = time.time(), time.monotonic()
                # Capture at receipt, before display deltas are discarded. No
                # lifecycle/database operations belong in this hook.
                if self.observer is not None:
                    self.observer(message, received_wall)
                with self.condition:
                    self.received += 1
                    if "id" not in message and message.get("method") in DISPLAY_DELTAS:
                        self.display_deltas += 1
                        continue
                size = len(json.dumps(message, ensure_ascii=False).encode("utf-8"))
                if size > self.max_queue_bytes:
                    raise ValueError("decoded message exceeds queue capacity")
                with self.condition:
                    while not self.stopped and (
                        len(self.ready) >= self.max_messages
                        or self.queue_bytes + size > self.max_queue_bytes
                    ):
                        self.condition.wait(
                            0.2
                        )  # Bounded backpressure, never drop control messages.
                    if self.stopped:
                        break
                    self.ready.append((message, size, received_wall, received_mono))
                    self.queue_bytes += size
                    self.peak_messages = max(self.peak_messages, len(self.ready))
                    self.peak_queue_bytes = max(self.peak_queue_bytes, self.queue_bytes)
                    self.condition.notify_all()
        except Exception as exc:
            with self.condition:
                self.error = exc
                self.condition.notify_all()
        finally:
            self.source.close()

    def receive(self, deadline):
        with self.condition:
            while not self.ready:
                if self.error is not None:
                    raise self.error
                if self.stopped:
                    raise EOFError("transport reader closed")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("transport observation timeout")
                self.condition.wait(remaining)
            message, size, received_wall, received_mono = self.ready.popleft()
            self.queue_bytes -= size
            self.last_receipt = {
                "received_at": received_wall,
                "dispatch_at": time.time(),
                "queue_wait_seconds": max(0.0, time.monotonic() - received_mono),
                # App-server does not attach a reliable generation timestamp to every call.
                "model_generated_at": None,
                "generation_timestamp_available": False,
            }
            self.condition.notify_all()
            return message

    def metrics(self):
        with self.condition:
            return {
                "received_messages": self.received,
                "display_deltas": self.display_deltas,
                "queued_messages": len(self.ready),
                "queued_bytes": self.queue_bytes,
                "peak_messages": self.peak_messages,
                "peak_queue_bytes": self.peak_queue_bytes,
                "reader_alive": self.thread.is_alive(),
            }

    def close(self):
        with self.condition:
            self.stopped = True
            self.condition.notify_all()
        self.thread.join(timeout=2)
        if self.thread.is_alive():
            raise RuntimeError("transport reader failed to close")
