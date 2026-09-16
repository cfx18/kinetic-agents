"""Live, host-owned observable I/O. No model calls, database reads or log replay.

JSONL is canonical; Markdown is appended in the same operation. Receipt times
are local observations, not provider generation times. Auth RPCs and reasoning
are excluded BEFORE serialization. This is not a raw HTTP/credential archive.
"""

from datetime import datetime
from datetime import timezone
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import threading
import time
import uuid

from kinetic_agents.observability.review import scrub
from kinetic_agents.observability.review import plain

PUBLIC_ITEMS = frozenset(
    {
        "userMessage",
        "agentMessage",
        "plan",
        "commandExecution",
        "fileChange",
        "mcpToolCall",
        "dynamicToolCall",
        "webSearch",
        "imageView",
        "contextCompaction",
        "collabAgentToolCall",
        "subAgentActivity",
        "customToolCall",
        "functionCall",
    }
)
PUBLIC_DELTAS = frozenset(
    {
        "item/agentMessage/delta",
        "item/plan/delta",
        "item/commandExecution/outputDelta",
        "item/fileChange/outputDelta",
    }
)
REQUESTS = frozenset(
    {"thread/start", "thread/resume", "turn/start", "turn/interrupt", "thread/compact/start"}
)
PRIVATE_KEYS = re.compile(r"^(?:analysis|reasoning|encrypted_content|encryptedContent)$")
SECRET_KEYS = re.compile(
    r"password|api.?key|authorization|credential|secret|(?:access|refresh|id)[_-]?token", re.I
)


def clean(value):
    if isinstance(value, dict):
        return {
            k: "[REDACTED]" if SECRET_KEYS.search(k) else clean(v)
            for k, v in value.items()
            if not PRIVATE_KEYS.match(k)
        }
    if isinstance(value, list):
        return [clean(v) for v in value]
    value = scrub(value)
    if isinstance(value, str):
        value = re.sub(
            r"""(?i)((?:access[_-]?token|refresh[_-]?token|id[_-]?token)["']?\s*[:=]\s*["']?)[^\s,"';}]+""",
            r"\1[REDACTED]",
            value,
        )
    return value


def open_append(path):
    path = plain(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        os.close(fd)
        raise PermissionError("transcript requires an owned regular file")
    return os.fdopen(fd, "a", encoding="utf-8")


class LiveTranscript:
    def __init__(self, root, *, max_event_bytes=8 * 1024 * 1024):
        self.root = plain(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.max_event_bytes = max_event_bytes
        self.lock = threading.RLock()
        self.stream = uuid.uuid4().hex
        self.sequence = 0
        self.requests, self.tools, self.items, self.native_calls = {}, {}, set(), set()
        self.streams = []
        self.actor_streams = {}
        try:
            for name in ("transcript.lock", "transcript.jsonl", "transcript.md"):
                self.streams.append(open_append(self.root / name))
        except BaseException:
            for stream in self.streams:
                stream.close()
            raise
        self.guard, self.jsonl, self.markdown = self.streams
        self.closed = False
        partial = False
        with self._file_lock():
            if os.fstat(self.jsonl.fileno()).st_size:
                with (self.root / "transcript.jsonl").open("rb") as previous:
                    previous.seek(-1, os.SEEK_END)
                    partial = previous.read(1) != b"\n"
                if partial:
                    # Preserve the damaged bytes; only separate the next event.
                    self.jsonl.write("\n")
                    self.jsonl.flush()
        if partial:
            self.record(
                "capture_gap",
                {"reason": "previous JSONL ended without newline; original bytes retained"},
            )
        self.record(
            "capture_started",
            {
                "scope": "本次接口可观察的输入、公开回复、工具请求/返回和中断；不是私有推理或 HTTP 全量镜像",
                "ordering": "文件追加顺序；stream_sequence 仅在同一捕获会话内递增；observed_at 是本地观察时间",
                "limits": [
                    "认证 RPC/隐藏推理不记录；已被上游截断的内容不能恢复",
                    "公开流式片段与完成文本均保留，不应相加统计回复长度",
                    "原生内部未暴露的子工具 I/O 不可补造；未识别事件只记类型",
                    "文本凭据过滤是防御措施，不代表日志已可直接公开",
                ],
                "max_event_bytes": max_event_bytes,
            },
        )

    def record(self, kind, payload, *, observed_at=None):
        with self.lock:
            if self.closed:
                return
            data = clean(payload)
            rendered = json.dumps(data, ensure_ascii=False, allow_nan=False)
            raw = rendered.encode("utf-8")
            truncated = len(raw) > self.max_event_bytes
            if truncated:
                data = {
                    "capture_truncated": True,
                    "original_sanitized_bytes": len(raw),
                    "text_prefix": raw[: self.max_event_bytes].decode("utf-8", errors="ignore"),
                }
            self.sequence += 1
            event = dict(
                schema="live-transcript.v1",
                stream=self.stream,
                stream_sequence=self.sequence,
                observed_at=datetime.fromtimestamp(
                    observed_at or time.time(), timezone.utc
                ).isoformat(),
                kind=kind,
                capture_truncated=truncated,
                payload=data,
            )
            actor = self.actor_id(payload)
            event["actor_id"] = actor
            rendered = json.dumps(data, ensure_ascii=False, indent=2)
            fence = "`" * max(
                3, max((len(m[0]) + 1 for m in re.finditer(r"`+", rendered)), default=3)
            )
            with self._file_lock():
                line = json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n"
                block = (
                    f"\n## {self.stream[:8]}:{self.sequence} · {kind}\n\n"
                    f"Agent：{actor or 'host'}；本地观察时间：{event['observed_at']}\n\n{fence}json\n{rendered}\n{fence}\n"
                )
                self.jsonl.write(line)
                self.jsonl.flush()
                self.markdown.write(block)
                self.markdown.flush()
                if actor:
                    if actor not in self.actor_streams:
                        folder = plain(self.root / "agents" / actor)
                        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
                        pair = []
                        try:
                            for name in ("transcript.jsonl", "transcript.md"):
                                pair.append(open_append(folder / name))
                            if os.fstat(pair[0].fileno()).st_size:
                                with (folder / "transcript.jsonl").open("rb") as previous:
                                    previous.seek(-1, os.SEEK_END)
                                    if previous.read(1) != b"\n":
                                        pair[0].write("\n")
                                        pair[0].flush()
                        except BaseException:
                            for stream in pair:
                                stream.close()
                            raise
                        self.actor_streams[actor] = pair
                    for stream, text in zip(self.actor_streams[actor], (line, block)):
                        stream.write(text)
                        stream.flush()

    @staticmethod
    def actor_id(payload):
        params = payload.get("params") or payload.get("call") or payload
        actor = params.get("threadId") or params.get("thread_id")
        if not actor and payload.get("method") == "thread/started":
            actor = params.get("id")
        return (
            actor
            if isinstance(actor, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", actor)
            else None
        )

    def _file_lock(self):
        from contextlib import contextmanager

        @contextmanager
        def locked():
            fcntl.flock(self.guard, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(self.guard, fcntl.LOCK_UN)

        return locked()

    def outgoing(self, message):
        with self.lock:
            method, ident = message.get("method"), message.get("id")
            if method in REQUESTS:
                self.requests[ident] = method
                self.record("model_input" if method == "turn/start" else "harness_request", message)
            elif method is None and ident in self.tools:
                source = self.tools.pop(ident)
                self.record(
                    "tool_return",
                    {
                        "call": source,
                        "response": message,
                        "delivery": "write_attempt_to_native; delivery_error_recorded_separately",
                    },
                )

    def incoming(self, message, received_at=None):
        with self.lock:
            method, ident = message.get("method"), message.get("id")
            params = message.get("params") or {}
            if method is None:
                # Never serialize auth/model catalog responses or thread histories.
                request = self.requests.pop(ident, None)
                if request:
                    result = message.get("result") or {}
                    self.record(
                        "harness_ack",
                        {
                            "request_id": ident,
                            "method": request,
                            "error": message.get("error"),
                            "model": result.get("model"),
                            "thread_id": (result.get("thread") or {}).get("id"),
                            "turn_id": (result.get("turn") or {}).get("id"),
                        },
                        observed_at=received_at,
                    )
                return
            if method.startswith(("account/", "codex/event/")) or "reasoning" in method.lower():
                return
            if method in ("rawResponseItem/completed", "item/rawResponseItem/completed"):
                item = params.get("item") or {}
                if item.get("type") in (
                    "function_call",
                    "custom_tool_call",
                    "function_call_output",
                    "custom_tool_call_output",
                ):
                    key = (params.get("threadId"), params.get("turnId"), item.get("call_id"))
                    if item["type"].endswith("_output"):
                        self.native_calls.discard(key)
                    else:
                        self.native_calls.add(key)
                    self.record("native_tool_io", message, observed_at=received_at)
                elif item.get("type") != "reasoning":
                    self.record(
                        "raw_item_not_exported", {"type": item.get("type")}, observed_at=received_at
                    )
                return
            if method == "item/tool/call":
                self.tools[ident] = {
                    k: params.get(k) for k in ("threadId", "turnId", "callId", "tool")
                }
                self.record("tool_request", message, observed_at=received_at)
            elif method in ("item/started", "item/completed"):
                item = params.get("item") or {}
                typ = item.get("type", "")
                if (
                    typ == "reasoning"
                    or item.get("phase") == "analysis"
                    or item.get("channel") == "analysis"
                ):
                    return
                key = (params.get("threadId"), params.get("turnId"), item.get("id"))
                if typ in PUBLIC_ITEMS:
                    if method == "item/started":
                        self.items.add(key)
                    else:
                        self.items.discard(key)
                    self.record(
                        (
                            "public_item_started"
                            if method == "item/started"
                            else "public_item_completed"
                        ),
                        message,
                        observed_at=received_at,
                    )
                else:
                    self.record(
                        "unsupported_item",
                        {
                            "method": method,
                            "item_type": typ,
                            "threadId": key[0],
                            "turnId": key[1],
                            "itemId": key[2],
                            "payload_captured": False,
                        },
                        observed_at=received_at,
                    )
            elif method in PUBLIC_DELTAS:
                self.record("public_stream_fragment", message, observed_at=received_at)
            elif method in ("thread/started", "turn/started", "turn/completed"):
                obj = params.get("turn") or params.get("thread") or {}
                self.record(
                    "lifecycle",
                    {
                        "method": method,
                        "threadId": params.get("threadId"),
                        "id": obj.get("id"),
                        "status": obj.get("status"),
                        "error": obj.get("error"),
                    },
                    observed_at=received_at,
                )
            elif method in (
                "error",
                "thread/tokenUsage/updated",
                "model/rerouted",
                "turn/plan/updated",
                "turn/diff/updated",
            ):
                self.record("native_notification", message, observed_at=received_at)
            else:
                self.record(
                    "unsupported_notification",
                    {"method": method, "payload_captured": False},
                    observed_at=received_at,
                )

    def close(self):
        with self.lock:
            if self.closed:
                return
            try:
                self.record(
                    "capture_closed",
                    {
                        "unreturned_tools": list(self.tools.values()),
                        "unacknowledged_requests": list(self.requests.values()),
                        "unfinished_items": sorted(self.items, key=str),
                        "unreturned_native_calls": sorted(self.native_calls, key=str),
                        "note": "关闭捕获不代表科研成功；异常进程退出时此事件可能缺失",
                    },
                )
            finally:
                self.closed = True
                for stream in self.streams:
                    stream.close()
                for pair in self.actor_streams.values():
                    for stream in pair:
                        stream.close()


class TranscriptTransport:
    """Opt-in adapter mixin. Host auth clients never inherit this class."""

    def send(self, value):
        capture = getattr(self, "transcript", None)
        if capture:
            capture.outgoing(value)
        try:
            return super().send(value)
        except Exception as exc:
            if capture:
                capture.record("transport_send_failed", {"error_type": type(exc).__name__})
            raise

    def close(self):
        try:
            return super().close()
        finally:
            capture = getattr(self, "transcript", None)
            try:
                tail = getattr(self, "rollout_capture", None)
                if tail:
                    tail.close()
            finally:
                if capture:
                    capture.close()
