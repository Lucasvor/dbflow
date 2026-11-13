import base64
import copy
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from dbflow.common import FlowError, atomic_write, digest, now
from dbflow.core import Control
from dbflow.audit import scan, dismiss, hook
from dbflow.installer import install, uninstall
from dbflow.protocol import rpc, bridge, http_server, result
from dbflow.simulation import SimulatedGitLab, fixture, change, demonstration

PACKAGE = Path(__file__).resolve().parents[1]


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dbflow-test-")
        self.root = Path(self.temp.name)
        fixture(self.root)
        SimulatedGitLab.reset()
        self.patcher = patch("dbflow.core.GitLab", SimulatedGitLab)
        self.patcher.start()
        self.c = Control(self.root)

    def tearDown(self):
        self.c.close()
        self.patcher.stop()
        self.temp.cleanup()

    def register(self, op="r1", demand="D1", parent=None, number=1, changes=None):
        args = {"operation_id": op, "demand_id": demand, "title": "Demanda de teste", "changes": changes or [change("PRO", number), change("PRI", number)]}
        if parent:
            args["parent_revision"] = parent
        return self.c.register(args)["revision_id"]

    def test_demo(self):
        output = demonstration(self.root / "demo")
        self.assertTrue(output["all_passed"])
        self.assertEqual(output["real_network_requests"], 0)

    def test_transfer_limit_counts_actual_envelope(self):
        value = {"bundle_files": {"script.sql": "\\" * 1900000}}
        response = result(value)
        self.assertEqual(response["structuredContent"], value)
        self.assertLess(len(json.dumps(response).encode()), 10000000)

    def test_idempotency_and_argument_collision(self):
        rid = self.register()
        self.assertEqual(rid, self.register())
        self.assertEqual(len(self.c.revisions()), 1)
        self.assertEqual(len(SimulatedGitLab.commits), 1)
        with self.assertRaises(FlowError):
            self.register(number=2)

    def test_revision_parent_required(self):
        self.register()
        with self.assertRaises(FlowError):
            self.register(op="r2", number=2)

    def test_confirm_exact_revision_target_and_steps(self):
        r1 = self.register()
        bundle = self.c.prepare({"operation_id": "bundle", "revision_ids": [r1], "environment": "HML"})
        r2 = self.register(op="r2", parent=r1, number=2)
        self.c.confirm({"operation_id": "confirm", "revision_id": r1, "database": "PRO", "environment": "HML",
            "change_ids": ["pro-column"], "kind": "manual_applied", "evidence": "User reports DBA applied PRO.", "observed_at": now(),
            "target_fingerprint": bundle["targets"]["PRO"]["fingerprint"]})
        s1, s2 = self.c.state(r1, "HML"), self.c.state(r2, "HML")
        self.assertEqual(s1[0]["execution"], "manual_applied")
        self.assertEqual(s1[1]["execution"], "pending")
        self.assertTrue(all(s["execution"] == "pending" for s in s2))
        self.assertTrue(all(s["execution"] == "pending" for s in self.c.state(r1, "PRD")))

    def test_remapping_does_not_reuse_confirmation(self):
        rid = self.register()
        fingerprint = self.c.destination("PRO", "HML")["fingerprint"]
        self.c.confirm({"operation_id": "confirm", "revision_id": rid, "database": "PRO", "environment": "HML", "change_ids": ["pro-column"],
            "kind": "manual_applied", "evidence": "User confirmation", "observed_at": now(), "target_fingerprint": fingerprint})
        self.c.config["databases"]["PRO"]["environments"]["HML"]["instance_alias"] = "different-server"
        self.assertEqual(self.c.state(rid, "HML")[0]["execution"], "pending")

    def test_partial_snapshot_does_not_imply_delete_or_apply(self):
        rid = self.register()
        output = self.c.snapshot({"operation_id": "snap", "database": "PRO", "environment": "HML", "revision_id": rid,
            "target_fingerprint": self.c.destination("PRO", "HML")["fingerprint"],
            "objects": {}, "covered_objects": [], "captured_at": now(), "source": "user export", "limitations": ["Missing permission"]})
        self.assertEqual(output["comparisons"][0]["comparison"], "inconclusive")
        self.assertFalse(output["application_status_changed"])
        self.assertEqual(self.c.state(rid, "HML")[0]["execution"], "pending")

    def test_dispatch_is_not_application(self):
        rid = self.register()
        bundle = self.c.prepare({"operation_id": "bundle", "revision_ids": [rid], "environment": "HML"})
        self.c.dispatch({"operation_id": "mail", "bundle_id": bundle["bundle_id"], "reference": "User says sent", "sent_at": now()})
        self.assertFalse(self.c.is_confirmed(rid, "HML"))

    def test_dependencies_and_object_overlap(self):
        first = change("PRO")
        second = change("PRI")
        second["depends_on"] = [first["id"]]
        rid = self.register(changes=[second, first])
        self.assertEqual(self.c.revision(rid)["changes"][0]["database"], "PRO")
        r2 = self.register(op="another", demand="D2")
        with self.assertRaises(FlowError):
            self.c.prepare({"operation_id": "bundle", "revision_ids": [rid, r2], "environment": "HML"})

    def test_cycle_and_unsafe_path_are_rejected(self):
        a, b = change("PRO"), change("PRI")
        a["depends_on"], b["depends_on"] = [b["id"]], [a["id"]]
        with self.assertRaises(FlowError):
            self.register(changes=[a, b])
        a.pop("depends_on")
        a["filename"] = "../../escape.sql"
        with self.assertRaises(FlowError):
            self.register(changes=[a])

    def test_outage_survives_restart(self):
        SimulatedGitLab.offline = True
        rid = self.register()
        self.assertEqual(self.c.pending_count(), 1)
        self.c.close()
        self.c = Control(self.root)
        self.assertIn(rid, self.c.revisions())
        SimulatedGitLab.offline = False
        self.assertEqual(self.c.sync()["state"], "synced")
        self.assertEqual(self.c.pending_count(), 0)

    def test_conflict_preserves_both_and_can_reconcile(self):
        first = self.register()
        other_root = self.root / "other"
        fixture(other_root)
        other = Control(other_root)
        try:
            other.sync()
            SimulatedGitLab.offline = True
            local = other.register({"operation_id": "offline", "demand_id": "D1", "title": "Offline work", "parent_revision": first, "changes": [change("PRO", 2)]})["revision_id"]
            SimulatedGitLab.offline = False
            remote = self.register(op="online", parent=first, number=3)
            self.assertEqual(other.sync()["state"], "pending")
            self.assertEqual(len(other.heads()["D1"]), 2)
            other.abandon({"operation_id": "abandon", "revision_id": local, "reason": "Reconcile offline content onto the actual remote parent."})
            self.assertEqual(other.heads()["D1"], [remote])
            self.assertIn(local, other.revisions())
            merged = other.register({"operation_id": "reconciled", "demand_id": "D1", "title": "Reconciled", "parent_revision": remote, "changes": [change("PRO", 4)]})
            self.assertEqual(merged["sync"]["state"], "synced")
        finally:
            other.close()

    def test_cannot_abandon_published_revision(self):
        rid = self.register()
        with self.assertRaises(FlowError):
            self.c.abandon({"operation_id": "abandon", "revision_id": rid, "reason": "Attempting unsafe abandonment."})

    def test_retrieve_revision_bundle_and_dispatch_history(self):
        rid = self.register()
        bundle = self.c.prepare({"operation_id": "bundle", "revision_ids": [rid], "environment": "HML"})
        self.c.dispatch({"operation_id": "email", "bundle_id": bundle["bundle_id"], "reference": "email reported", "sent_at": now()})
        self.assertIn("script", self.c.get_revision({"revision_id": rid})["revision"]["changes"][0])
        read = self.c.get_bundle({"bundle_id": bundle["bundle_id"]})
        self.assertEqual(len(read["dispatch_reports"]), 1)
        self.assertIn("manifest.json", read["bundle_files"])

    def test_cancelled_bundle_cannot_be_dispatched(self):
        first = self.register()
        other_root = self.root / "other"
        fixture(other_root)
        other = Control(other_root)
        try:
            other.sync()
            SimulatedGitLab.offline = True
            local = other.register({"operation_id": "offline", "demand_id": "D1", "title": "offline", "parent_revision": first, "changes": [change("PRO", 2)]})["revision_id"]
            bundle = other.prepare({"operation_id": "bundle", "revision_ids": [local], "environment": "HML"})
            SimulatedGitLab.offline = False
            self.register(op="remote", parent=first, number=3)
            other.abandon({"operation_id": "abandon", "revision_id": local, "reason": "Offline branch superseded; bundle was never sent."})
            with self.assertRaises(FlowError):
                other.dispatch({"operation_id": "email", "bundle_id": bundle["bundle_id"], "reference": "email", "sent_at": now()})
            self.assertTrue(other.get_bundle({"bundle_id": bundle["bundle_id"]})["cancelled"])
        finally:
            other.close()

    def test_import_rejects_path_traversal(self):
        rid = self.register()
        event = copy.deepcopy(self.c.store.events("revision")[0])
        event["data"]["changes"][0]["filename"] = "../../outside.sql"
        with self.assertRaises(FlowError):
            self.c.validate_remote_event(event)

    def test_candidate_changes_reappear_after_registration(self):
        path = self.root / "migration.sql"
        content = change()["script"]
        atomic_write(path, content)
        candidate = scan(self.c)["candidates"][0]
        item = change()
        item.update(source_path="migration.sql", source_sha256=candidate["hash"])
        self.register(changes=[item])
        self.assertFalse(scan(self.c)["candidates"])
        atomic_write(path, content + "-- another edit\n")
        self.assertEqual(len(scan(self.c)["candidates"]), 1)

    def test_exclusion_is_hash_bound_and_stop_is_bounded(self):
        atomic_write(self.root / "migration.sql", "-- documentation sample\n")
        state = scan(self.c)
        first = hook(self.c, "claude", "stop", {"session_id": "s"})
        self.assertEqual(first["decision"], "block")
        self.assertEqual(hook(self.c, "claude", "stop", {"session_id": "s", "stop_hook_active": True}), {})
        candidate = state["candidates"][0]
        dismiss(self.c, candidate["path"], candidate["hash"], "Documentation example only; not executed.")
        self.assertFalse(scan(self.c)["candidates"])
        atomic_write(self.root / "migration.sql", "ALTER TABLE dbo.X ADD N int;\n")
        self.assertEqual(len(scan(self.c)["candidates"]), 1)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dbflow-install-")
        self.root = Path(self.temp.name) / "Project with spaces & accents ç"
        self.root.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_dry_run_preserves_files(self):
        before = list(self.root.iterdir())
        self.assertTrue(install(self.root, source=PACKAGE)["files_to_write"])
        self.assertEqual(before, list(self.root.iterdir()))

    def test_install_twice_preserves_existing_config_and_uninstall(self):
        original = b"# Existing instructions\r\nDo useful work.\r\n"
        (self.root / "AGENTS.md").write_bytes(original)
        atomic_write(self.root / ".claude/settings.json", '{"permissions":{"allow":["Read"]}}')
        atomic_write(self.root / ".cursor/mcp.json", '{"mcpServers":{"existing":{"command":"test"}}}')
        atomic_write(self.root / ".codex/config.toml", 'model = "example"\n')
        install(self.root, apply=True, source=PACKAGE)
        self.assertEqual(install(self.root, apply=True, source=PACKAGE)["files_to_write"], [])
        settings = json.loads((self.root / ".claude/settings.json").read_text())
        self.assertEqual(settings["permissions"]["allow"], ["Read"])
        self.assertEqual(len(settings["hooks"]["Stop"]), 1)
        cursor = json.loads((self.root / ".cursor/mcp.json").read_text())
        self.assertIn("existing", cursor["mcpServers"])
        uninstall(self.root, apply=True)
        self.assertEqual((self.root / "AGENTS.md").read_bytes(), original)

    def test_reinstall_then_uninstall_preserves_user_additions(self):
        settings_path = self.root / ".claude/settings.json"
        atomic_write(settings_path, '{"permissions":{"allow":["Read"]}}')
        install(self.root, apply=True, source=PACKAGE)
        settings = json.loads(settings_path.read_text())
        settings["permissions"]["deny"] = ["Bash(rm:*)"]
        atomic_write(settings_path, json.dumps(settings))
        install(self.root, apply=True, source=PACKAGE)
        result = uninstall(self.root, apply=True)
        self.assertIn(".claude/settings.json", result["preserved_because_modified"])
        self.assertEqual(json.loads(settings_path.read_text())["permissions"]["deny"], ["Bash(rm:*)"])

    def test_malformed_json_fails_before_writes(self):
        atomic_write(self.root / ".cursor/hooks.json", "{bad")
        with self.assertRaises(FlowError):
            install(self.root, apply=True, source=PACKAGE)
        self.assertFalse((self.root / "AGENTS.md").exists())

    def test_stdio_and_hook_on_path_with_spaces(self):
        install(self.root, apply=True, source=PACKAGE)
        launcher = self.root / ".db-control/runtime/dbctl.py"
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
        messages[0]['params'].update(capabilities={}, clientInfo={'name': 'test', 'version': '1'})
        process = subprocess.Popen([sys.executable, str(launcher), "--project", str(self.root), "mcp"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8')
        try:
            process.stdin.write(json.dumps(messages[0]) + '\n'); process.stdin.flush()
            replies = [json.loads(process.stdout.readline())]
            process.stdin.write(json.dumps(messages[1]) + '\n' + json.dumps(messages[2]) + '\n'); process.stdin.flush()
            replies.append(json.loads(process.stdout.readline()))
        finally:
            process.stdin.close()
            process.wait(timeout=15)
            process.stdout.close(); process.stderr.close()
        self.assertIn("dbflow_register_revision", [t["name"] for t in replies[1]["result"]["tools"]])
        run = subprocess.run([sys.executable, str(launcher), "--project", ".", "hook", "--client", "claude", "--event", "start"],
            cwd=self.root, input='{"session_id":"test"}', text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(run.stdout)["hookSpecificOutput"]["hookEventName"], "SessionStart")


class RemoteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dbflow-remote-")
        self.root = Path(self.temp.name)
        self.server_root = self.root / "server"
        self.client_root = self.root / "client"
        fixture(self.server_root)
        config = fixture(self.client_root)
        self.token = "a" * 40
        self.env = patch.dict(os.environ, {"DBFLOW_SERVICE_TOKENS": json.dumps({self.token: "developer-one"}), "DBFLOW_SERVICE_TOKEN": self.token})
        self.env.start()
        SimulatedGitLab.reset()
        self.fake = patch("dbflow.core.GitLab", SimulatedGitLab)
        self.fake.start()
        self.server = http_server(self.server_root, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        config.update(mode="remote", remote_url=f"http://127.0.0.1:{self.server.server_port}/mcp")
        atomic_write(self.client_root / ".db-control/project.json", json.dumps(config))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.fake.stop()
        self.env.stop()
        self.temp.cleanup()

    def call(self, name, arguments):
        return bridge(self.client_root, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}})["result"]

    def test_remote_rejection_does_not_poison_status(self):
        bad = change()
        bad["database"] = "UNKNOWN"
        output = self.call("dbflow_register_revision", {"operation_id": "bad", "demand_id": "D1", "title": "bad", "changes": [bad]})
        self.assertTrue(output["isError"])
        status = self.call("dbflow_status", {})
        self.assertFalse(status["isError"])
        self.assertEqual(status["structuredContent"]["local_remote_outbox"][0]["state"], "rejected")

    def test_remote_bundle_download_and_actor(self):
        response = self.call("dbflow_register_revision", {"operation_id": "r1", "demand_id": "D1", "title": "Remote", "changes": [change()]})
        rid = response["structuredContent"]["revision_id"]
        bundle = self.call("dbflow_prepare_bundle", {"operation_id": "b1", "revision_ids": [rid], "environment": "HML"})
        self.assertFalse(bundle["isError"])
        path = Path(bundle["structuredContent"]["local_directory"])
        self.assertTrue(path.is_relative_to(self.client_root))
        self.assertTrue((path / "manifest.json").exists())
        control = Control(self.server_root)
        try:
            self.assertEqual(control.store.events("revision")[0]["actor"], "service:developer-one")
        finally:
            control.close()

    def test_http_rejects_bad_auth_and_origin(self):
        url = f"http://127.0.0.1:{self.server.server_port}/mcp"
        for headers, expected in (({}, 401), ({"Authorization": "Bearer " + self.token, "Origin": "https://untrusted.invalid"}, 403)):
            request = urllib.request.Request(url, data=b'{"jsonrpc":"2.0","id":1,"method":"ping"}', headers={"Content-Type": "application/json", **headers})
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request)
            self.assertEqual(caught.exception.code, expected)


if __name__ == "__main__":
    unittest.main()
