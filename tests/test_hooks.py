import json
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from dbflow.audit import hook
from dbflow.core import Control
from dbflow.cli import doctor
from dbflow.installer import install
from dbflow.simulation import SimulatedGitLab, fixture


PACKAGE = Path(__file__).resolve().parents[1]


class CodexHookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dbflow-codex-hooks-")
        self.root = Path(self.temp.name) / "project with spaces & accents ç"
        self.root.mkdir()
        fixture(self.root)
        self.patch = patch("dbflow.core.GitLab", SimulatedGitLab)
        self.patch.start()
        self.control = Control(self.root)

    def tearDown(self):
        if self.control is not None:
            self.control.close()
        self.patch.stop()
        self.temp.cleanup()

    def test_install_emits_valid_native_hooks_preserves_existing_hooks_and_is_idempotent(self):
        path = self.root / ".codex/config.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('model = "example"\n\n[[hooks.PostToolUse]]\nmatcher = "Bash"\n'
                        '[[hooks.PostToolUse.hooks]]\ntype = "command"\ncommand = "echo existing"\n', encoding="utf-8")
        install(self.root, clients=("codex",), apply=True, source=PACKAGE)
        config = tomllib.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(config["model"], "example")
        self.assertEqual(config["hooks"]["PostToolUse"][0]["hooks"][0]["command"], "echo existing")
        for event in ("SessionStart", "PostToolUse", "Stop"):
            handler = next(handler for group in config["hooks"][event] for handler in group["hooks"]
                           if "--client codex --event" in handler.get("command", ""))
            self.assertEqual(handler["type"], "command")
            self.assertIn("--client codex --event", handler["command"])
        self.assertEqual(install(self.root, clients=("codex",), apply=True, source=PACKAGE)["files_to_write"], [])

    def test_codex_hook_outputs_use_documented_event_specific_schema(self):
        started = hook(self.control, "codex", "start", {"session_id": "s"})
        self.assertEqual(started["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn("additionalContext", started["hookSpecificOutput"])

        (self.root / "pending.sql").write_text("select 1;", encoding="utf-8")
        stopped = hook(self.control, "codex", "stop", {"session_id": "s"})
        self.assertEqual(stopped["decision"], "block")
        self.assertIn("reason", stopped)
        self.assertEqual(hook(self.control, "codex", "stop", {"session_id": "s", "stop_hook_active": True}), {})

    def test_cli_helper_handles_codex_event_and_doctor_reports_invocation_not_activation(self):
        install(self.root, clients=("codex",), apply=True, source=PACKAGE)
        (self.root / "pending.sql").write_text("select 1;", encoding="utf-8")
        launcher = self.root / ".db-control/runtime/dbctl.py"
        result = subprocess.run([sys.executable, str(launcher), "--project", str(self.root), "hook",
                                 "--client", "codex", "--event", "stop"],
                                input='{"session_id":"private-session-id"}', text=True,
                                capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout)["decision"], "block")
        self.control.close()
        self.control = None

        config = self.root / ".codex/config.toml"
        report = doctor(self.root)
        self.assertTrue(report["client_hooks"]["codex"]["configured"])
        content = config.read_text(encoding="utf-8")
        config.write_text(content.replace("--client codex --event stop", "--client codex --event removed"), encoding="utf-8")
        incomplete = doctor(self.root)["client_hooks"]["codex"]
        self.assertFalse(incomplete["configured"])
        self.assertEqual(incomplete["missing_events"], ["Stop"])

        config.write_text(config.read_text(encoding="utf-8") + "\n[features]\nhooks = false\n", encoding="utf-8")
        report = doctor(self.root)
        codex = report["client_hooks"]["codex"]
        self.assertFalse(codex["configured"])
        self.assertTrue(codex["disabled_by_config"])
        self.assertEqual(codex["last_helper_invocation"]["event"], "stop")
        self.assertNotIn("private-session-id", json.dumps(codex["last_helper_invocation"]))
        self.assertEqual(report["codex_hooks"], "disabled_by_features.hooks=false")


if __name__ == "__main__":
    unittest.main()
