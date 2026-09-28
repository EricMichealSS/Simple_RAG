"""Captures the RAW JSON-RPC wire exchange with the real (unmodified)
package-registry server -- deliberately bypassing mcp.ClientSession/
stdio_client entirely, and instead speaking newline-delimited JSON directly
over the subprocess's real stdin/stdout pipes. This is not a re-serialization
of what the SDK does internally -- it IS what goes over the wire, captured
at the point closest to the actual bytes, against the actual server process.

Produces wire.json: the 3 required phases (initialize, tools/list,
tools/call), each with the literal request/response pair plus hand-written
annotations of every top-level field.
"""

import json
import subprocess
import sys
from pathlib import Path

SERVER_PATH = str(Path(__file__).parent / "package_registry_server.py")


class RawJsonRpcClient:
    def __init__(self, command, args):
        self.proc = subprocess.Popen(
            [command, *args],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1,
        )
        self._id = 0

    def _next_id(self):
        self._id += 1
        return self._id

    def send_request(self, method, params=None):
        req = {"jsonrpc": "2.0", "id": self._next_id(), "method": method, "params": params or {}}
        line = json.dumps(req)
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()
        resp_line = self.proc.stdout.readline()
        return req, json.loads(resp_line)

    def send_notification(self, method, params=None):
        note = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        self.proc.stdin.write(json.dumps(note) + "\n")
        self.proc.stdin.flush()
        return note  # notifications get no response by definition

    def close(self):
        self.proc.stdin.close()
        self.proc.terminate()


def main():
    client = RawJsonRpcClient("python3", [SERVER_PATH])
    capture = {"_note": "Raw JSON-RPC captured directly over the real subprocess stdin/stdout "
                         "pipes to package_registry_server.py -- not re-serialized from SDK objects.",
               "phases": []}

    # --- Phase A: initialize ---
    init_req, init_resp = client.send_request("initialize", {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "wire-capture-script", "version": "0.1"},
    })
    capture["phases"].append({
        "phase": "initialize",
        "request": init_req,
        "response": init_resp,
        "annotations": {
            "request.jsonrpc": "Protocol version tag -- MCP rides on JSON-RPC 2.0 verbatim, no custom envelope.",
            "request.id": "Correlates this request with its response; the client picks it, increments per call.",
            "request.method": "'initialize' -- always the first call; nothing else is legal before this completes.",
            "request.params.protocolVersion": "The MCP protocol version (not this server's app version) the client speaks.",
            "request.params.capabilities": "What optional protocol features the CLIENT supports (empty here -- we support nothing extra).",
            "request.params.clientInfo": "Free-form self-identification, for logging/debugging on the server side.",
            "response.result.protocolVersion": "The version the SERVER agreed to use for the rest of the session.",
            "response.result.capabilities": "What optional features the SERVER supports (e.g. whether it supports resources, prompts, notifications).",
            "response.result.serverInfo": "The server's own self-identification (name/version) -- 'package-registry' per FastMCP(name=...).",
        },
    })

    # Required: client must send this notification before anything else -- no response expected.
    initialized_note = client.send_notification("notifications/initialized")
    capture["phases"].append({
        "phase": "initialized (notification, no response)",
        "request": initialized_note,
        "response": None,
        "annotations": {
            "method": "'notifications/initialized' -- a notification (no 'id' field), meaning the server "
                      "must not and cannot reply to it. This tells the server the client has finished "
                      "processing the initialize response and the session is now live.",
        },
    })

    # --- Phase B: tools/list ---
    list_req, list_resp = client.send_request("tools/list", {})
    capture["phases"].append({
        "phase": "tools/list",
        "request": list_req,
        "response": list_resp,
        "annotations": {
            "request.method": "'tools/list' -- the actual discovery call. Nothing about these 3 tools "
                              "is known to the client before this response arrives.",
            "response.result.tools": "Array of tool definitions: name, description (this IS what the "
                                     "model reads to decide whether to call it), and inputSchema (a real "
                                     "JSON Schema object, auto-generated by FastMCP from the Python "
                                     "function's type hints -- this is why str vs Literal[...] typing "
                                     "matters, see PHASE1/PHASE3 docs).",
        },
    })

    # --- Phase C: tools/call ---
    call_req, call_resp = client.send_request("tools/call", {
        "name": "check_package_deprecated",
        "arguments": {"name": "octokit", "version": "2.4.0"},
    })
    capture["phases"].append({
        "phase": "tools/call",
        "request": call_req,
        "response": call_resp,
        "annotations": {
            "request.params.name": "Which discovered tool to invoke -- must match a name from tools/list exactly.",
            "request.params.arguments": "Arguments the CALLER (the LLM, via the host) chose -- validated by "
                                        "the server against that tool's inputSchema before the function body runs.",
            "response.result.content": "Array of content blocks (here: one 'text' block, a JSON-encoded string "
                                       "-- this server's Python function returned a dict, so FastMCP wrapped it "
                                       "as exactly one text block; see PHASE2 doc for what happens when a tool "
                                       "returns a list instead).",
            "response.result.isError": "Protocol-level error flag, separate from any 'error'/'found' key inside "
                                       "the tool's own JSON payload -- this call succeeded at the protocol level "
                                       "even though the underlying package IS deprecated; that's an application-"
                                       "level fact, not a protocol failure.",
        },
    })

    client.close()

    out_path = Path(__file__).parent / "wire.json"
    out_path.write_text(json.dumps(capture, indent=2), encoding="utf-8")
    print(f"Captured {len(capture['phases'])} phases -> {out_path}")

    print("\n" + "=" * 70)
    print("REQUIRED ONE-LINE STATEMENT (where the model runs vs. where this JSON-RPC runs):")
    print("=" * 70)
    print(
        "This entire initialize -> tools/list -> tools/call exchange happens locally, over a "
        "subprocess pipe, between this script and package_registry_server.py, with no LLM involved "
        "at any point; the actual model call (deciding to invoke check_package_deprecated in the "
        "first place) happens separately, on the host side in mcp_agent.py, as a normal HTTPS "
        "request to Groq's API -- the server never sees that call and never makes one itself."
    )


if __name__ == "__main__":
    main()
