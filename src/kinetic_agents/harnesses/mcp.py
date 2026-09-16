"""Small stateless Streamable HTTP MCP endpoint, scoped to one owned actor.

No inference, persistent global server, shared SSH credential or evaluator tool.
Native clients manage their own loop; MCP adapts the existing authoritative APIs.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import threading


class ToolServer:
    def __init__(self, specs, call):
        self.specs, self.call = specs, call
        self.token = secrets.token_urlsafe(32)

    def start(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                if self.path != "/mcp" or not secrets.compare_digest(
                    self.headers.get("Authorization", ""), "Bearer " + owner.token
                ):
                    self.send_error(403)
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 65536:
                        self.send_error(413)
                        return
                    request = json.loads(self.rfile.read(size))
                    method, params = request.get("method"), request.get("params", {})
                    if "id" not in request:
                        self.send_response(202)
                        self.end_headers()
                        return
                    if method == "initialize":
                        result = dict(
                            protocolVersion="2025-03-26",
                            capabilities={"tools": {}},
                            serverInfo={"name": "kinetic-research", "version": "0.2.0"},
                        )
                    elif method == "ping":
                        result = {}
                    elif method == "tools/list":
                        result = {
                            "tools": [
                                {k: s[k] for k in ("name", "description", "inputSchema")}
                                for s in owner.specs
                            ]
                        }
                    elif method == "tools/call":
                        result = owner.call(
                            params["name"],
                            params.get("arguments", {}),
                            str(request["id"]),
                        )
                    else:
                        raise ValueError("unsupported MCP method")
                    response = {"jsonrpc": "2.0", "id": request["id"], "result": result}
                except Exception as exc:
                    response = {
                        "jsonrpc": "2.0",
                        "id": request.get("id") if "request" in locals() else None,
                        "error": {"code": -32602, "message": type(exc).__name__},
                    }
                body = json.dumps(response).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return f"http://127.0.0.1:{self.server.server_port}/mcp"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
