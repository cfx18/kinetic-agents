from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json, threading, time


class ScriptedResponses:
    def __init__(self, script, model="deepseek-flash"):
        self.script, self.requests, self.faults = script, [], []
        self.lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                try:
                    if self.path not in {"/responses", "/v1/responses"}:
                        self.send_error(400)
                        return
                    body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    with owner.lock:
                        owner.requests.append(body)
                        serial = len(owner.requests)
                    output = owner.script(body, dict(self.headers), serial)
                    rid = "resp_" + str(serial)
                    response = {
                        "id": rid,
                        "object": "response",
                        "model": model or body["model"],
                        "status": "in_progress",
                        "output": [],
                    }
                    events = [{"type": "response.created", "response": response}]
                    for index, item in enumerate(output):
                        events.append(
                            {
                                "type": "response.output_item.added",
                                "output_index": index,
                                "item": item,
                            }
                        )
                        events.append(
                            {
                                "type": "response.output_item.done",
                                "output_index": index,
                                "item": item,
                            }
                        )
                    events.append(
                        {
                            "type": "response.completed",
                            "response": {
                                **response,
                                "status": "completed",
                                "output": output,
                                "usage": {
                                    "input_tokens": 10,
                                    "output_tokens": 10,
                                    "total_tokens": 20,
                                },
                            },
                        }
                    )
                    blob = b"".join(("data: " + json.dumps(e) + "\n\n").encode() for e in events)
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(blob)))
                    self.end_headers()
                    self.wfile.write(blob)
                except Exception as exc:
                    owner.faults.append({"type":type(exc).__name__, "message":str(exc)[:3000]})
                    self.send_error(500)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


def function(name, args, serial):
    return {
        "type": "function_call",
        "id": f"fc_{serial}",
        "call_id": f"call_{serial}",
        "name": name,
        "arguments": json.dumps(args),
    }


def final(serial):
    return {
        "type": "message",
        "id": f"msg_{serial}",
        "role": "assistant",
        "status": "completed",
        "content": [
            {
                "type": "output_text",
                "text": "Synthetic routing complete; not a science result.",
                "annotations": [],
            }
        ],
    }
