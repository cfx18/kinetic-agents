"""Extracted reusable implementation; historical launchers intentionally excluded."""

import json, os, selectors, subprocess, time
from pathlib import Path
from kinetic_agents.core.encoding import encoded


class MetadataClient:
    METHODS = {"initialize", "account/read", "model/list"}

    def __init__(self, command, cwd, timeout=30, env=None):
        self.process = subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ, "stdout")
        self.selector.register(self.process.stderr, selectors.EVENT_READ, "stderr")
        self.timeout, self.buffer, self.serial = timeout, b"", 0
        self.stderr_bytes = 0

    def send(self, value):
        self.process.stdin.write(encoded(value))
        self.process.stdin.flush()

    def request(self, method, params):
        if method not in self.METHODS:
            raise PermissionError("metadata probe cannot access threads or execute turns")
        self.serial += 1
        request_id = self.serial
        self.send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            message = self.receive(deadline)
            if "method" in message:
                self.handle_message(message)
            elif message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError(
                        f"app-server rejected method {method}: code={message['error'].get('code')}"
                    )
                return message["result"]
        raise TimeoutError(f"app-server request timeout: {method}")

    def handle_message(self, message):
        if "id" in message:
            self.send(
                {
                    "id": message["id"],
                    "error": {"code": -32601, "message": "metadata probe rejects server requests"},
                }
            )

    def receive(self, deadline):
        while time.monotonic() < deadline:
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                if not line.strip():
                    continue
                return json.loads(line)
            for key, _ in self.selector.select(min(0.2, max(0.0, deadline - time.monotonic()))):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    self.selector.unregister(key.fileobj)
                    continue
                if key.data == "stdout":
                    self.buffer += chunk
                    if len(self.buffer) > 8_000_000:
                        raise RuntimeError("metadata response exceeded bound")
                else:
                    # Count only: account/provider diagnostics could contain PII.
                    self.stderr_bytes += len(chunk)
            if self.process.poll() is not None and not self.buffer:
                raise RuntimeError("app-server exited before metadata response")
        raise TimeoutError("app-server receive timeout")

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.selector.close()
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()
