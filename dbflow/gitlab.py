import base64
import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from .common import FlowError, encoded, digest, identifier


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise FlowError("GitLab redirect refused; configure the final HTTPS instance URL.")


class GitLab:
    def __init__(self, config):
        self.config = config
        cfg = config.get("gitlab", {})
        self.url = cfg.get("url", "").rstrip("/")
        parsed = urllib.parse.urlsplit(self.url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise FlowError("Configure a GitLab HTTPS URL without credentials, query or fragment.")
        self.project = str(cfg.get("project_id", ""))
        if not self.project:
            raise FlowError("GitLab project_id is not configured.")
        self.branch = cfg.get("branch", "main")
        self.token = os.environ.get(cfg.get("token_env", "DBFLOW_GITLAB_TOKEN"), "")
        if not self.token:
            raise FlowError("GitLab token environment variable is unavailable; synchronization remains pending.")
        self.base = self.url + "/api/v4/projects/" + urllib.parse.quote(self.project, safe="")
        self.opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))

    def request(self, method, endpoint, data=None, missing=False):
        req = urllib.request.Request(self.base + endpoint, data=None if data is None else encoded(data).encode(), method=method,
            headers={"PRIVATE-TOKEN": self.token, "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with self.opener.open(req, timeout=20) as response:
                raw = response.read(16000001)
                if len(raw) > 16000000:
                    raise FlowError("GitLab response exceeds configured size limit.")
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and missing:
                return None
            # Do not echo server response bodies, headers or request credentials.
            raise FlowError(f"GitLab returned HTTP {exc.code}; check role, branch permissions and concurrent updates.") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise FlowError("GitLab is unreachable; check network/VPN, TLS trust and configured URL.") from None

    def file(self, path):
        return self.request("GET", "/repository/files/" + urllib.parse.quote(path, safe="") + "?ref=" + urllib.parse.quote(self.branch, safe=""), missing=True)

    def prefix(self):
        return "dbflow/projects/" + identifier(self.config["project_key"])

    def publish(self, event):
        path = self.prefix() + "/events/" + identifier(event["id"]) + ".json"
        existing = self.file(path)
        if existing:
            actual = json.loads(base64.b64decode(existing["content"]))
            if actual != event:
                raise FlowError("Remote event collision; refusing overwrite.")
            return existing["last_commit_id"]
        actions = [{"action": "create", "file_path": path, "content": encoded(event)}]
        if event["kind"] == "revision":
            pointer = self.prefix() + "/demands/" + event["data"]["demand_id"] + "/head.json"
            head = self.file(pointer)
            current = json.loads(base64.b64decode(head["content"]))["revision_id"] if head else None
            if current != event["data"].get("parent_revision"):
                raise FlowError("Concurrent demand revision: remote head differs from expected parent. Reconcile explicitly.")
            action = {"action": "update" if head else "create", "file_path": pointer,
                      "content": encoded({"revision_id": event["data"]["revision_id"]})}
            if head:
                action["last_commit_id"] = head["last_commit_id"]
            actions.append(action)
        result = self.request("POST", "/repository/commits", {"branch": self.branch,
            "commit_message": f"dbflow: {event['kind']} {event['id']}", "actions": actions})
        return result["id"]

    def pull(self):
        prefix = self.prefix() + "/events"
        result = []
        for page in range(1, 101):
            query = urllib.parse.urlencode({"path": prefix, "ref": self.branch, "per_page": 100, "page": page})
            entries = self.request("GET", "/repository/tree?" + query, missing=True) or []
            for item in entries:
                if item.get("type") != "blob" or not item["path"].endswith(".json"):
                    continue
                file = self.file(item["path"])
                result.append((json.loads(base64.b64decode(file["content"])), file["last_commit_id"]))
            if len(entries) < 100:
                return result
        raise FlowError("Registry exceeds 10000 events; incremental indexing is required before continuing.")
