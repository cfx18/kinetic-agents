"""Extracted reusable implementation; historical launchers intentionally excluded."""

import json, os, tempfile, time
from pathlib import Path
from kinetic_agents.native.rpc import MetadataClient
from kinetic_agents.core.encoding import MODEL, encoded


class DynamicClient(MetadataClient):
    METHODS = MetadataClient.METHODS | {"thread/start", "turn/start", "turn/interrupt"}

    def __init__(self, command, cwd, timeout=120):
        super().__init__(command, cwd, timeout)
        self.thread_id = None
        self.turn_id = None
        self.tools = {}
        self.calls = []
        self.completed = None
        self.usage = None
        self.unexpected_items = []
        self.rejected_requests = []

    def request(self, method, params):
        if method == "thread/start" and self.thread_id is not None:
            raise PermissionError("one independent thread per client")
        if method in {"turn/start", "turn/interrupt"}:
            if self.thread_id is None or params.get("threadId") != self.thread_id:
                raise PermissionError("foreign thread")
        if method in {"thread/start", "turn/start"} and params.get("model") != MODEL:
            raise PermissionError("model switching prohibited")
        if method == "thread/start" and params.get("ephemeral") is not True:
            raise PermissionError("clean ephemeral thread required")
        return super().request(method, params)

    def handle_message(self, message):
        method = message.get("method")
        params = message.get("params", {})
        if "id" in message:
            if (
                method != "item/tool/call"
                or params.get("threadId") != self.thread_id
                or params.get("tool") not in self.tools
                or params.get("namespace") not in (None, "")
            ):
                self.rejected_requests.append(method)
                return super().handle_message(message)
            key = (params.get("turnId"), params.get("callId"))
            if not all(key):
                return super().handle_message(message)
            previous = next((r for r in self.calls if (r["turn_id"], r["call_id"]) == key), None)
            if previous:
                if (
                    previous["tool"] != params["tool"]
                    or previous["arguments"] != params["arguments"]
                ):
                    return super().handle_message(message)
                result = previous["result"]
            else:
                try:
                    value = self.tools[params["tool"]](params["arguments"])
                    result = {
                        "success": True,
                        "contentItems": [{"type": "inputText", "text": json.dumps(value)}],
                    }
                except Exception as exc:
                    # Public argument validators use curated messages; do not
                    # forward arbitrary filesystem/provider exception details.
                    detail = (
                        str(exc)[:300]
                        if isinstance(exc, (ValueError, PermissionError))
                        else "Internal tool error; recorded by supervisor."
                    )
                    result = {
                        "success": False,
                        "contentItems": [
                            {
                                "type": "inputText",
                                "text": json.dumps(
                                    {"error_type": type(exc).__name__, "message": detail}
                                ),
                            }
                        ],
                    }
                self.calls.append(
                    dict(
                        turn_id=key[0],
                        call_id=key[1],
                        tool=params["tool"],
                        arguments=params["arguments"],
                        result=result,
                    )
                )
            self.send({"id": message["id"], "result": result})
        elif params.get("threadId") == self.thread_id:
            if method == "turn/completed":
                self.completed = params.get("turn")
            elif method == "thread/tokenUsage/updated":
                self.usage = params.get("tokenUsage")
            elif method == "item/completed":
                kind = params.get("item", {}).get("type")
                if kind not in {"userMessage", "agentMessage", "reasoning", "dynamicToolCall"}:
                    self.unexpected_items.append(kind)

    def start(self, directory, tools):
        self.request(
            "initialize",
            {
                "clientInfo": {"name": "kinetic_agents_dynamic", "version": "0.1.0"},
                "capabilities": {"experimentalApi": True},
            },
        )
        self.send({"method": "initialized", "params": {}})
        account = self.request("account/read", {"refreshToken": False})
        if (account.get("account") or {}).get("type") != "chatgpt":
            raise PermissionError("managed ChatGPT account required")
        result = self.request(
            "thread/start",
            dict(
                model=MODEL,
                cwd=str(directory),
                ephemeral=True,
                approvalPolicy="never",
                sandbox="read-only",
                experimentalRawEvents=False,
                config={
                    "model_reasoning_effort": "high",
                    "project_doc_max_bytes": 0,
                    "features.memories": False,
                    "features.multi_agent": False,
                    "features.shell_tool": False,
                    "features.code_mode": False,
                    "web_search": "disabled",
                    "tools.view_image": False,
                },
                dynamicTools=tools,
            ),
        )
        thread = result["thread"]
        if thread.get("turns") or thread.get("forkedFromId"):
            raise PermissionError("new thread unexpectedly contains history")
        self.thread_id = thread["id"]
        return {
            "model": result.get("model"),
            "model_provider": result.get("modelProvider"),
            "reasoning_effort": result.get("reasoningEffort"),
            "instruction_source_count": len(result.get("instructionSources", [])),
            "ephemeral": thread.get("ephemeral"),
            "initial_turn_count": len(thread.get("turns", [])),
        }

    def turn(self, text):
        self.completed = None
        result = self.request(
            "turn/start",
            {
                "threadId": self.thread_id,
                "model": MODEL,
                "effort": "high",
                "input": [{"type": "text", "text": text}],
            },
        )
        self.turn_id = result["turn"]["id"]
        deadline = time.monotonic() + self.timeout
        while self.completed is None and time.monotonic() < deadline:
            self.handle_message(self.receive(deadline))
        if self.completed is None:
            raise TimeoutError("turn did not complete")
        return self.completed
