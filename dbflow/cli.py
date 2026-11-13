import argparse
import json
import os
import sqlite3
import sys
import tomllib
import uuid
from pathlib import Path
from .common import FlowError, atomic_write, identifier, load_config, encoded
from .core import Control
from .installer import install, uninstall
from .audit import scan, hook


def doctor(root):
    config = load_config(root)
    checks = []
    def managed_hook(handler, client, event):
        command = handler.get("command", "")
        mode = "start" if event in ("SessionStart", "sessionStart") else "stop" if event in ("Stop", "stop") else "after"
        return (".db-control/runtime/dbctl.py" in command
                and f"--client {client} --event {mode}" in command)
    required_hook_events = {
        "codex": ("SessionStart", "PostToolUse", "Stop"),
        "claude": ("SessionStart", "PostToolUse", "Stop"),
        "cursor": ("sessionStart", "afterFileEdit", "afterShellExecution", "afterMCPExecution", "stop"),
    }
    client_hooks = {client: {"configured": False, "configured_events": [], "missing_events": list(events),
                            "last_helper_invocation": None}
                    for client, events in required_hook_events.items()}
    for path in (".mcp.json", ".claude/settings.json", ".cursor/mcp.json", ".cursor/hooks.json", ".codex/config.toml"):
        file = root / path
        if file.exists():
            try:
                parsed = (tomllib.loads if path.endswith(".toml") else json.loads)(file.read_text(encoding="utf-8-sig"))
                if path == ".codex/config.toml":
                    codex_hooks = parsed.get("hooks", {})
                    configured_events = [event for event in required_hook_events["codex"] if any(
                        managed_hook(handler, "codex", event)
                        for group in codex_hooks.get(event, [])
                        for handler in group.get("hooks", [])
                    )]
                    client_hooks["codex"].update(configured_events=configured_events,
                        missing_events=[event for event in required_hook_events["codex"] if event not in configured_events],
                        configured=len(configured_events) == len(required_hook_events["codex"]))
                    client_hooks["codex"]["disabled_by_config"] = parsed.get("features", {}).get("hooks") is False
                elif path == ".claude/settings.json":
                    claude_hooks = parsed.get("hooks", {})
                    configured_events = [event for event in required_hook_events["claude"] if any(
                        managed_hook(handler, "claude", event)
                        for group in claude_hooks.get(event, [])
                        for handler in group.get("hooks", [])
                    )]
                    client_hooks["claude"].update(configured_events=configured_events,
                        missing_events=[event for event in required_hook_events["claude"] if event not in configured_events],
                        configured=len(configured_events) == len(required_hook_events["claude"]))
                elif path == ".cursor/hooks.json":
                    cursor_hooks = parsed.get("hooks", {})
                    configured_events = [event for event in required_hook_events["cursor"] if any(
                        managed_hook(handler, "cursor", event)
                        for handler in cursor_hooks.get(event, [])
                    )]
                    client_hooks["cursor"].update(configured_events=configured_events,
                        missing_events=[event for event in required_hook_events["cursor"] if event not in configured_events],
                        configured=len(configured_events) == len(required_hook_events["cursor"]))
                checks.append({"file": path, "syntax": "valid"})
            except ValueError:
                checks.append({"file": path, "syntax": "invalid"})
    journal = root / ".db-control/local/journal.sqlite3"
    if journal.is_file():
        db = None
        try:
            db = sqlite3.connect(journal.resolve().as_uri() + "?mode=ro", uri=True)
            for client in client_hooks:
                row = db.execute("SELECT value FROM checks WHERE key=?", ("hook_invocation:" + client,)).fetchone()
                if row:
                    client_hooks[client]["last_helper_invocation"] = json.loads(row[0])
        except sqlite3.Error:
            pass
        finally:
            if db is not None:
                db.close()
    cfg = config.get("gitlab", {})
    return {"python": sys.version.split()[0], "project": str(root), "mode": config.get("mode"), "configuration": checks,
        "gitlab_endpoint_configured": bool(cfg.get("url") and cfg.get("project_id")),
        "token_available_to_this_process": bool(os.environ.get(cfg.get("token_env", "DBFLOW_GITLAB_TOKEN"))),
        "databases": list(config.get("databases", {})), "client_recognition": "requires_opening_trusted_project_in_client",
        "gitlab_connectivity": "not_tested_by_doctor", "database_access": "not_implemented_by_design",
        "mcp_runtime": "net8.0", "encrypted_local_credentials_present": (root / '.db-control/local/credentials.dpapi.json').exists(),
        "client_hooks": client_hooks,
        "codex_hooks": ("disabled_by_features.hooks=false" if client_hooks["codex"].get("disabled_by_config")
                        else "configured; requires trusted project and hook trust review" if client_hooks["codex"]["configured"]
                        else "not_configured; run dbctl install --clients codex --apply"),
        "coverage": "Configured file patterns and submitted evidence only; external database edits need user tools or exports."}


def main():
    parser = argparse.ArgumentParser(description="DBFlow — automatic database-change records, no business database connections")
    parser.add_argument("--project", default=".")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("install")
    p.add_argument("--clients", default="codex,claude,cursor")
    p.add_argument("--apply", action="store_true")
    p.add_argument('--repo-url')
    p.add_argument('--instance-url')
    p.add_argument('--mcp-url')
    p.add_argument('--mode', choices=('local', 'remote'))
    p.add_argument('--auth-type', choices=('private_token', 'oauth_access_token'))
    p.add_argument('--token-env')
    p.add_argument('--service-token-env')
    p = sub.add_parser("uninstall")
    p.add_argument("--apply", action="store_true")
    sub.add_parser("doctor")
    p = sub.add_parser("demo")
    p.add_argument("--output")
    p = sub.add_parser("configure")
    p.add_argument("--gitlab-url")
    p.add_argument("--gitlab-project")
    p.add_argument("--branch")
    p.add_argument("--project-key")
    p.add_argument("--mode", choices=("local", "remote"))
    p.add_argument("--remote-url")
    p.add_argument('--repo-url')
    p.add_argument('--instance-url')
    p.add_argument('--auth-type', choices=('private_token', 'oauth_access_token'))
    p.add_argument('--token-env')
    p.add_argument('--service-token-env')
    p = sub.add_parser('openshift')
    p.add_argument('--output', required=True)
    p.add_argument('--namespace', default='dbflow')
    p.add_argument('--image', default='')
    p.add_argument('--route-host', default='')
    p.add_argument('--ca-bundle')
    p.add_argument('--apply', action='store_true')
    p = sub.add_parser("map-database")
    p.add_argument("--database", required=True)
    p.add_argument("--engine", required=True, choices=("sqlserver", "oracle", "mongodb", "postgresql", "mysql", "other"))
    p.add_argument("--environment", required=True)
    p.add_argument("--instance-alias", required=True)
    p.add_argument("--physical-name", required=True)
    for command in ("mcp", "status", "audit"):
        sub.add_parser(command)
    p = sub.add_parser("serve")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p = sub.add_parser("hook")
    p.add_argument("--client", choices=("claude", "cursor", "codex"), required=True)
    p.add_argument("--event", choices=("start", "after", "stop"), required=True)
    p = sub.add_parser("call")
    p.add_argument("tool")
    p.add_argument("--input", required=True, help="JSON argument file; '-' reads stdin")
    args = parser.parse_args()
    root = Path(args.project).resolve()
    try:
        if args.command == "install":
            output = install(root, args.clients.split(",") if args.clients else (), args.apply, configuration={
                'repo_url': args.repo_url, 'instance_url': args.instance_url, 'mcp_url': args.mcp_url, 'mode': args.mode,
                'auth_type': args.auth_type, 'token_env': args.token_env, 'service_token_env': args.service_token_env})
        elif args.command == "uninstall":
            output = uninstall(root, args.apply)
        elif args.command == "doctor":
            output = doctor(root)
        elif args.command == 'openshift':
            from .configuration import openshift
            output = openshift(load_config(root), args.output, args.namespace, args.image, args.route_host, args.ca_bundle, args.apply)
        elif args.command == "demo":
            from .simulation import demonstration
            output = demonstration()
            if args.output:
                atomic_write(Path(args.output), json.dumps(output, ensure_ascii=False, indent=2))
        elif args.command in ("configure", "map-database"):
            config = load_config(root)
            if args.command == "configure":
                for key, value in (("url", args.gitlab_url), ("project_id", args.gitlab_project), ("branch", args.branch)):
                    if value is not None:
                        config["gitlab"][key] = value
                if args.project_key:
                    identifier(args.project_key)
                    journal = root / ".db-control/local/journal.sqlite3"
                    if journal.exists() and args.project_key != config["project_key"]:
                        control = Control(root)
                        try:
                            if control.store.events():
                                raise FlowError("project_key cannot change after events exist; create a new registry scope.")
                        finally:
                            control.close()
                    config["project_key"] = args.project_key
                if args.mode:
                    config["mode"] = args.mode
                if args.remote_url:
                    config["remote_url"] = args.remote_url
                from .configuration import settings
                config = settings(config, repo_url=args.repo_url, instance_url=args.instance_url, mcp_url=args.remote_url,
                    auth_type=args.auth_type, token_env=args.token_env, service_token_env=args.service_token_env)
            else:
                identifier(args.database)
                identifier(args.environment)
                entry = config["databases"].setdefault(args.database, {"engine": args.engine, "environments": {}})
                if entry["engine"] != args.engine:
                    raise FlowError("Logical database already has another engine; choose a new identifier.")
                entry["environments"][args.environment] = {"instance_alias": args.instance_alias, "database": args.physical_name}
            atomic_write(root / ".db-control/project.json", json.dumps(config, ensure_ascii=False, indent=2) + "\n")
            output = {"configured": True, "project_key": config["project_key"], "credential_values_written": False}
        elif args.command == "mcp":
            from .dotnet import stdio as native_stdio
            raise SystemExit(native_stdio(root))
        elif args.command == "serve":
            raise FlowError('HTTP service moved to .NET 8. Generate OpenShift configuration and run dotnet DbFlow.Server.dll --config server-config.json.')
        elif args.command == "call":
            data = json.loads(sys.stdin.read() if args.input == "-" else Path(args.input).read_text(encoding="utf-8-sig"))
            from .dotnet import call
            output = call(root, args.tool, data)
        else:
            control = Control(root)
            try:
                if args.command == "status":
                    from .dotnet import call
                    output = call(root, 'dbflow_status', {})
                elif args.command == "audit":
                    output = scan(control)
                else:
                    payload = json.load(sys.stdin)
                    output = hook(control, args.client, args.event, payload)
            finally:
                control.close()
        print(json.dumps(output, ensure_ascii=False, indent=None if args.command == "hook" else 2))
    except (FlowError, ValueError, OSError) as exc:
        message = str(exc) if isinstance(exc, FlowError) else "Invalid file/configuration or local I/O failure; no success is implied."
        if args.command == "hook":
            print(json.dumps({"systemMessage": "DBFlow audit failed; run doctor and report incomplete coverage."}) if args.client == "claude" else "{}")
            print("DBFlow audit failed; coverage is incomplete.", file=sys.stderr)
        else:
            print(json.dumps({"error": message}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
