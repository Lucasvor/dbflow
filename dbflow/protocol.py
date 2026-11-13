"""Bounded MCP tools over stdio and stateless Streamable HTTP (JSON responses)."""
import hmac
import json
import os
import sys
import uuid
import urllib.request
import urllib.error
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from .common import FlowError, encoded, load_config, safe_path, atomic_write, digest
from .core import Control
from .audit import scan, dismiss, acknowledge

VERSION = "2025-11-25"
SUPPORTED = [VERSION, "2025-06-18", "2025-03-26", "2024-11-05"]
MUTATIONS = {"dbflow_register_revision", "dbflow_prepare_bundle", "dbflow_report_dispatch", "dbflow_confirm", "dbflow_record_snapshot", "dbflow_abandon_local_revision"}


def schema(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


TEXT = {"type": "string"}
ARRAY = {"type": "array", "items": TEXT}
CHANGE = schema({k: TEXT for k in ("id", "database", "object", "kind", "filename", "script", "verification", "before_definition", "after_definition", "source_path", "source_sha256")}
                | {"depends_on": ARRAY}, ("id", "database", "object", "kind", "filename", "script", "verification"))
TOOLS = [
    ("dbflow_abandon_local_revision", "Resolve a pending unpublished revision conflict by preserving it as abandoned. Never deletes history or abandons published revisions.",
     schema({k: TEXT for k in ("operation_id", "revision_id", "reason")}, ("operation_id", "revision_id", "reason"))),
    ("dbflow_cancel_remote_request", "Cancel retry of a rejected/pending local remote request with a reason; preserves its audit record. Does not undo server operations.",
     schema({k: TEXT for k in ("operation_id", "reason")}, ("operation_id", "reason"))),
    ("dbflow_status", "Read registry revisions and environment confirmations; synchronize GitLab when configured. No database connection.",
     schema({"demand_id": TEXT, "offset": {"type": "integer"}, "limit": {"type": "integer"}})),
    ("dbflow_get_revision", "Retrieve complete immutable revision scripts and dependencies to resume a demand.", schema({"revision_id": TEXT}, ("revision_id",))),
    ("dbflow_get_bundle", "Retrieve an existing bundle, dispatch history and files without creating a delivery. Cancelled bundles remain audit-only.", schema({"bundle_id": TEXT}, ("bundle_id",))),
    ("dbflow_scan", "Scan configured local file patterns for unregistered candidates; not a business-database scan.", schema({})),
    ("dbflow_register_revision", "Persist an immutable database-change revision and attempt GitLab synchronization. Queued is NOT remotely synced or applied.",
     schema({"operation_id": TEXT, "demand_id": TEXT, "title": TEXT, "parent_revision": {"type": ["string", "null"]},
             "changes": {"type": "array", "items": CHANGE}, "dependencies": ARRAY}, ("operation_id", "demand_id", "title", "changes"))),
    ("dbflow_prepare_bundle", "Freeze explicit revisions into a delivery bundle and export DBA scripts/email draft. Does not send email or execute SQL.",
     schema({"operation_id": TEXT, "revision_ids": ARRAY, "environment": TEXT}, ("operation_id", "revision_ids", "environment"))),
    ("dbflow_report_dispatch", "Record user-reported email dispatch of an exact bundle; does not mark database application.",
     schema({k: TEXT for k in ("operation_id", "bundle_id", "reference", "sent_at")}, ("operation_id", "bundle_id", "reference", "sent_at"))),
    ("dbflow_confirm", "Record manual application, execution evidence or observed state for exact revision/database/environment/change IDs. Observation is not execution proof.",
     schema({k: TEXT for k in ("operation_id", "revision_id", "database", "environment", "kind", "evidence", "observed_at", "target_fingerprint", "bundle_id")} | {"change_ids": ARRAY},
            ("operation_id", "revision_id", "database", "environment", "kind", "evidence", "observed_at", "change_ids", "target_fingerprint"))),
    ("dbflow_record_snapshot", "Store definitions collected with USER tools or exports. Text comparison only; partial inventory never proves deletion; no application status update.",
     schema({k: TEXT for k in ("operation_id", "database", "environment", "captured_at", "source", "revision_id", "target_fingerprint")} |
            {"objects": {"type": "object", "additionalProperties": TEXT}, "covered_objects": ARRAY, "limitations": ARRAY},
            ("operation_id", "database", "environment", "captured_at", "source", "objects", "covered_objects", "limitations", "target_fingerprint"))),
    ("dbflow_exclude_candidate", "Exclude one exact local file hash with an explicit reason. Edited files become candidates again.",
     schema({k: TEXT for k in ("path", "sha256", "reason")}, ("path", "sha256", "reason"))),
]


def result(value, error=False):
    output = {"content": [{"type": "text", "text": encoded(value)}], "structuredContent": value, "isError": error}
    if len(encoded(output).encode("utf-8")) > 9000000:
        output["content"] = [{"type": "text", "text": "Large result: complete data is in structuredContent. No artifact content was omitted from that field."}]
    if len(encoded(output).encode("utf-8")) > 9500000:
        raise FlowError("Result exceeds MCP transfer limit; narrow the requested scope.")
    return output


def tool_call(root, name, args, actor=None):
    definitions = {n: s for n, _, s in TOOLS}
    if name not in definitions or not isinstance(args, dict):
        raise FlowError("Unknown tool or invalid arguments.")
    s = definitions[name]
    if set(args) - set(s["properties"]) or set(s["required"]) - set(args):
        raise FlowError("Missing required or unexpected tool arguments.")
    control = Control(root, actor)
    try:
        methods = {"dbflow_status": lambda: control.status(**args), "dbflow_scan": lambda: scan(control),
                   "dbflow_get_revision": lambda: control.get_revision(args), "dbflow_get_bundle": lambda: control.get_bundle(args),
                   "dbflow_abandon_local_revision": lambda: control.abandon(args),
                   "dbflow_register_revision": lambda: control.register(args), "dbflow_prepare_bundle": lambda: control.prepare(args),
                   "dbflow_report_dispatch": lambda: control.dispatch(args), "dbflow_confirm": lambda: control.confirm(args),
                   "dbflow_record_snapshot": lambda: control.snapshot(args),
                   "dbflow_exclude_candidate": lambda: dismiss(control, args["path"], args["sha256"], args["reason"])}
        if name == "dbflow_cancel_remote_request":
            if len(args["reason"].strip()) < 10:
                raise FlowError("Cancellation requires a concrete reason.")
            with control.store.db:
                control.store.db.execute("UPDATE requests SET state='cancelled',error=? WHERE id=? AND state IN ('pending','rejected')",
                    (args["reason"], args["operation_id"]))
            return result({"cancelled_retry": args["operation_id"], "server_operation_undone": False})
        return result(methods[name]())
    finally:
        control.close()


def rpc(root, message, actor=None):
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
        return {"jsonrpc": "2.0", "id": message.get("id") if isinstance(message, dict) else None,
                "error": {"code": -32600, "message": "Invalid request"}}
    if "id" not in message:
        return None
    method, params = message["method"], message.get("params", {})
    response = {"jsonrpc": "2.0", "id": message["id"]}
    try:
        if method == "initialize":
            version = params.get("protocolVersion")
            value = {"protocolVersion": version if version in SUPPORTED else VERSION,
                     "capabilities": {"tools": {"listChanged": False}},
                     "serverInfo": {"name": "dbflow", "version": "0.1.0"},
                     "instructions": "Track only database-change artifacts. This server has NO business database access. Inspect sync state; local persistence is not GitLab confirmation."}
        elif method == "ping":
            value = {}
        elif method == "tools/list":
            value = {"tools": [{"name": n, "description": d, "inputSchema": s,
                "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": n in MUTATIONS, "openWorldHint": True}}
                for n, d, s in TOOLS]}
        elif method == "tools/call":
            value = tool_call(root, params.get("name"), params.get("arguments", {}), actor)
        else:
            response["error"] = {"code": -32601, "message": "Method not found"}
            return response
        response["result"] = value
    except FlowError as exc:
        response["result"] = result({"error": str(exc)}, True)
    except Exception:
        # Never put raw payloads, credentials, traceback paths or SQL into protocol errors.
        response["result"] = result({"error": "Operation failed. Inspect local configuration and run doctor; no success is implied."}, True)
    return response


def remote_rpc(config, message):
    url = config.get("remote_url", "")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost", "::1")):
        raise FlowError("Remote service requires HTTPS (HTTP is allowed only for loopback tests).")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise FlowError("Remote URL must not contain credentials, query or fragment.")
    token = os.environ.get(config.get("remote_token_env", "DBFLOW_SERVICE_TOKEN"), "")
    if not token:
        raise FlowError("Remote service token is unavailable.")
    from .gitlab import NoRedirect
    request = urllib.request.Request(url, data=encoded(message).encode(), method="POST", headers={
        "Authorization": "Bearer " + token, "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": VERSION})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=25) as response:
            raw = response.read(10000001)
            if len(raw) > 10000000:
                raise FlowError("Remote response too large.")
            response = json.loads(raw) if raw else None
            if "id" in message:
                if not isinstance(response, dict) or response.get("id") != message["id"] or "error" in response:
                    raise FlowError("Remote JSON-RPC response is invalid or rejected; request remains pending.")
                if message.get("method") == "tools/call":
                    payload = response.get("result")
                    if not isinstance(payload, dict) or not isinstance(payload.get("content"), list):
                        raise FlowError("Remote tool response is incomplete; request remains pending.")
            return response
    except (OSError, urllib.error.URLError):
        raise FlowError("Remote service unavailable or rejected credentials; request remains pending locally.") from None


def bridge(root, message):
    config = load_config(root)
    params = message.get("params", {})
    name = params.get("name")
    if message.get("method") != "tools/call" or name in ("dbflow_scan", "dbflow_exclude_candidate", "dbflow_cancel_remote_request"):
        return rpc(root, message)
    control = Control(root)
    try:
        if name in MUTATIONS:
            op = params.get("arguments", {}).get("operation_id")
            if not op:
                return rpc(root, message)
            body = encoded(message)
            old = control.store.db.execute("SELECT body FROM requests WHERE id=?", (op,)).fetchone()
            if old and json.loads(old[0]).get("params") != params:
                raise FlowError("operation_id already queued with different arguments.")
            with control.store.db:
                control.store.db.execute("INSERT OR IGNORE INTO requests VALUES(?,?,'pending',NULL)", (op, body))
        # Ordered retry also handles a response lost after the remote commit succeeded.
        for queued in list(control.store.db.execute("SELECT * FROM requests WHERE state='pending' ORDER BY rowid")):
            saved = json.loads(queued["body"])
            response = remote_rpc(config, saved)
            payload = response.get("result", {})
            if payload.get("isError"):
                with control.store.db:
                    control.store.db.execute("UPDATE requests SET state='rejected',error=? WHERE id=?",
                        ("Server rejected the operation; inspect arguments and reconcile before retrying with a new operation_id.", queued["id"]))
                continue
            data = payload.get("structuredContent", {})
            with control.store.db:
                control.store.db.execute("UPDATE requests SET state='done',error=NULL WHERE id=?", (queued["id"],))
            if data.get("revision_id") and data.get("artifacts"):
                acknowledge(control, data["artifacts"], data["revision_id"])
        response = remote_rpc(config, message)
        data = response.get("result", {}).get("structuredContent", {})
        if name in ("dbflow_prepare_bundle", "dbflow_get_bundle") and data.get("bundle_files") and not response.get("result", {}).get("isError"):
            directory = safe_path(root, ".db-control/local/packages/" + data["bundle_id"])
            for relative, content in data.get("bundle_files", {}).items():
                path = safe_path(directory, relative)
                if path.exists() and digest(path.read_bytes()) != digest(content):
                    raise FlowError("Local package was edited; refusing overwrite.")
                atomic_write(path, content)
            data["local_directory"] = str(directory)
        if name == "dbflow_status":
            data["local_remote_outbox"] = [dict(r) for r in control.store.db.execute("SELECT id,state,error FROM requests WHERE state IN ('pending','rejected')")]
        if response.get("result") and not response["result"].get("isError"):
            response["result"] = result(data)
        return response
    except FlowError as exc:
        return {"jsonrpc": "2.0", "id": message.get("id"), "result": result({"error": str(exc),
            "pending_remote_requests": control.store.db.execute("SELECT count(*) FROM requests WHERE state IN ('pending','rejected')").fetchone()[0]}, True)}
    finally:
        control.close()


def stdio(root):
    config = load_config(root)
    for raw in sys.stdin.buffer:
        try:
            if len(raw) > 10000000:
                raise ValueError()
            message = json.loads(raw)
            response = bridge(root, message) if config.get("mode") == "remote" else rpc(root, message)
        except (ValueError, UnicodeDecodeError):
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        if response is not None:
            sys.stdout.buffer.write((encoded(response) + "\n").encode("utf-8"))
            sys.stdout.buffer.flush()


def http_server(root, host="127.0.0.1", port=8765):
    config = load_config(root)
    if config.get("mode") == "remote":
        raise FlowError("A shared server requires local mode with its own GitLab configuration.")
    try:
        tokens = json.loads(os.environ.get("DBFLOW_SERVICE_TOKENS", "{}"))
    except ValueError:
        raise FlowError("DBFLOW_SERVICE_TOKENS must map long random tokens to authenticated actor names.") from None
    if not isinstance(tokens, dict) or not tokens or any(len(k) < 32 or not isinstance(v, str) or not v for k, v in tokens.items()):
        raise FlowError("Configure DBFLOW_SERVICE_TOKENS with distinct tokens of at least 32 characters and actor names.")
    origins = config.get("allowed_origins", [])
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def authenticated_actor(self):
            origin = self.headers.get("Origin")
            if origin and origin not in origins:
                self.send_error(403)
                return None
            supplied = self.headers.get("Authorization", "")
            for token, actor in tokens.items():
                if hmac.compare_digest(supplied, "Bearer " + token):
                    return "service:" + actor
            self.send_error(401)
            return None

        def do_GET(self):
            if self.authenticated_actor():
                self.send_response(405)
                self.send_header("Allow", "POST")
                self.end_headers()

        def do_POST(self):
            actor = self.authenticated_actor()
            if not actor:
                return
            if self.path != "/mcp":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 10000000 or "application/json" not in self.headers.get("Content-Type", ""):
                    self.send_error(400)
                    return
                self.connection.settimeout(30)
                message = json.loads(self.rfile.read(length))
                response = rpc(root, message, actor)
            except (ValueError, OSError):
                self.send_error(400)
                return
            self.send_response(202 if response is None else 200)
            self.send_header("Content-Type", "application/json")
            body = b"" if response is None else encoded(response).encode()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    return ThreadingHTTPServer((host, port), Handler)
