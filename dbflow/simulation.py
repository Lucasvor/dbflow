"""Offline test double for the GitLab ledger API. Never makes HTTP requests."""
import base64
import copy
import json
import tempfile
import threading
import uuid
from pathlib import Path
from unittest.mock import patch
from .common import FlowError, atomic_write, encoded, now
from .gitlab import GitLab
from .core import Control
from .installer import default_config


class SimulatedGitLab(GitLab):
    files = {}
    commits = []
    offline = False
    lock = threading.RLock()

    @classmethod
    def reset(cls):
        cls.files = {}
        cls.commits = []
        cls.offline = False

    def __init__(self, config):
        self.config = config
        self.branch = config.get("gitlab", {}).get("branch", "main")

    def check(self):
        if self.offline:
            raise FlowError("Simulated network unavailable.")

    def file(self, path):
        self.check()
        with self.lock:
            return copy.deepcopy(self.files.get(path))

    def pull(self):
        self.check()
        prefix = self.prefix() + "/events/"
        with self.lock:
            return [(json.loads(base64.b64decode(value["content"])), value["last_commit_id"])
                    for path, value in self.files.items() if path.startswith(prefix)]

    def request(self, method, endpoint, data=None, missing=False):
        self.check()
        if method != "POST" or endpoint != "/repository/commits":
            raise AssertionError("Unexpected simulated API operation")
        with self.lock:
            for action in data["actions"]:
                previous = self.files.get(action["file_path"])
                if action["action"] == "create" and previous:
                    raise FlowError("Simulated GitLab create conflict.")
                if action["action"] == "update" and (not previous or previous["last_commit_id"] != action["last_commit_id"]):
                    raise FlowError("Simulated GitLab last_commit_id conflict.")
            commit = f"simulated-{len(self.commits) + 1:08d}"
            for action in data["actions"]:
                self.files[action["file_path"]] = {"content": base64.b64encode(action["content"].encode()).decode(), "last_commit_id": commit}
            self.commits.append(copy.deepcopy(data))
            return {"id": commit}


def fixture(root, project_key="simulation"):
    root = Path(root)
    config = default_config(root)
    config["project_key"] = project_key
    config["gitlab"].update(url="https://gitlab-simulado.invalid", project_id="simulation")
    config["databases"] = {name: {"engine": "sqlserver", "environments": {
        env: {"instance_alias": "simulated-" + env.lower(), "database": name} for env in ("TI", "HML", "PRD")}}
        for name in ("PRO", "PRI")}
    atomic_write(root / ".db-control/project.json", json.dumps(config, indent=2))
    return config


def change(database="PRO", number=1):
    return {"id": database.lower() + "-column", "database": database, "object": "dbo.Pedido:table", "kind": "alter_table",
            "filename": "pedido.sql", "script": f"ALTER TABLE dbo.Pedido ADD Campo{number} int NULL;\n",
            "after_definition": f"Pedido with Campo{number} int NULL", "verification": f"Check Campo{number} type, nullability and target identity."}


def demonstration(directory=None):
    root = Path(directory or tempfile.mkdtemp(prefix="dbflow-demo-"))
    fixture(root)
    SimulatedGitLab.reset()
    checks = []
    with patch("dbflow.core.GitLab", SimulatedGitLab):
        control = Control(root)
        try:
            a = {"operation_id": "demo-r1", "demand_id": "DEMANDA-123", "title": "Exemplo simulado PRO e PRI",
                 "changes": [change("PRO"), change("PRI")]}
            r1 = control.register(a)["revision_id"]
            r1_retry = control.register(a)["revision_id"]
            checks.append({"scenario": "retry is idempotent", "passed": r1 == r1_retry and len(control.revisions()) == 1})
            bundle = control.prepare({"operation_id": "demo-bundle", "revision_ids": [r1], "environment": "HML"})
            control.dispatch({"operation_id": "demo-email", "bundle_id": bundle["bundle_id"], "reference": "SIMULATION: user reports sent email", "sent_at": now()})
            r2 = control.register({"operation_id": "demo-r2", "demand_id": "DEMANDA-123", "title": "Nova revisão enquanto DBA aguarda",
                                   "parent_revision": r1, "changes": [change("PRO", 2), change("PRI", 2)]})["revision_id"]
            control.confirm({"operation_id": "demo-confirm", "revision_id": r1, "database": "PRO", "environment": "HML",
                "target_fingerprint": bundle["targets"]["PRO"]["fingerprint"], "bundle_id": bundle["bundle_id"],
                "change_ids": ["pro-column"], "kind": "manual_applied", "evidence": "SIMULATION: user reports DBA applied PRO only", "observed_at": now()})
            state1 = control.state(r1, "HML")
            checks.append({"scenario": "R1 PRO confirmed; PRI pending", "passed": state1[0]["execution"] == "manual_applied" and state1[1]["execution"] == "pending"})
            checks.append({"scenario": "R2 remains pending", "passed": all(s["execution"] == "pending" for s in control.state(r2, "HML"))})
            SimulatedGitLab.offline = True
            control.confirm({"operation_id": "demo-outage", "revision_id": r1, "database": "PRI", "environment": "HML",
                "target_fingerprint": bundle["targets"]["PRI"]["fingerprint"], "change_ids": ["pri-column"], "kind": "failed",
                "evidence": "SIMULATION: DBA reports PRI failed", "observed_at": now()})
            checks.append({"scenario": "network failure keeps durable outbox", "passed": control.pending_count() == 1})
        finally:
            control.close()
        SimulatedGitLab.offline = False
        restored = Control(root)
        try:
            status = restored.status()
            checks.append({"scenario": "restart recovers and synchronizes", "passed": restored.pending_count() == 0 and len(restored.revisions()) == 2})
            return {"simulation": True, "real_network_requests": 0, "real_database_connections": 0,
                    "all_passed": all(c["passed"] for c in checks), "checks": checks, "directory": str(root),
                    "simulated_git_commits": len(SimulatedGitLab.commits), "state": status}
        finally:
            restored.close()
