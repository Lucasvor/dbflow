import fnmatch
import json
import os
from pathlib import Path
from .common import FlowError, digest, encoded, now, safe_path


EXCLUDED = {".git", ".db-control", "node_modules", ".venv", "venv", "__pycache__", "bin", "obj"}
DEFAULT_GLOBS = ["*.sql", "**/*.sql", "*.ddl", "**/*.ddl", "*.pks", "**/*.pks", "*.pkb", "**/*.pkb",
                 "*.sqlproj", "**/*.sqlproj", "migrations/**", "**/migrations/**", "database/**", "**/database/**",
                 "mongo/**", "**/mongo/**"]


def scan(control):
    config, root, store = control.config, control.root, control.store
    patterns = config.get("watch", {}).get("include", DEFAULT_GLOBS)
    present, inaccessible = {}, []
    scanned = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if d not in EXCLUDED and not (Path(directory) / d).is_symlink()]
        for name in files:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink() or not any(fnmatch.fnmatchcase(relative.lower(), p.lower()) for p in patterns):
                continue
            scanned += 1
            if scanned > 10000:
                inaccessible.append("Scan limited to 10000 matching files; narrow watch.include.")
                break
            try:
                if path.stat().st_size > 1000000:
                    inaccessible.append(relative + ": exceeds 1 MB")
                    continue
                present[relative] = digest(path.read_bytes())
            except OSError:
                inaccessible.append(relative + ": unreadable")
        if scanned > 10000:
            break
    with store.db:
        known = {r["path"]: dict(r) for r in store.db.execute("SELECT * FROM files")}
        for path, sha in present.items():
            if path not in known or sha != known[path]["hash"]:
                store.db.execute("INSERT OR REPLACE INTO files VALUES(?,?,'candidate',NULL,NULL,?)", (path, sha, now()))
        if not inaccessible:
            for path, previous in known.items():
                # Only check deletions inside the current watch scope.
                if path not in present and previous["hash"] != "DELETED" and any(fnmatch.fnmatchcase(path.lower(), p.lower()) for p in patterns):
                    store.db.execute("UPDATE files SET hash='DELETED',disposition='candidate',revision=NULL,reason=NULL,updated=? WHERE path=?", (now(), path))
    store.set_check("coverage", {"checked_at": now(), "limitations": inaccessible,
        "scope": patterns, "external_database_changes": "not_observable_by_hooks"})
    return report(control)


def report(control):
    store = control.store
    return {"candidates": [dict(r) for r in store.db.execute("SELECT * FROM files WHERE disposition='candidate' ORDER BY path")],
            "pending_sync": control.pending_count(),
            "pending_remote_requests": store.db.execute("SELECT count(*) FROM requests WHERE state IN ('pending','rejected')").fetchone()[0],
            "coverage": store.get_check("coverage", {"limitations": ["No scan performed."]}),
            "registry_configured": bool(control.config.get("gitlab", {}).get("url") and control.config.get("gitlab", {}).get("project_id"))
                if control.config.get("mode") != "remote" else bool(control.config.get("remote_url")),
            "database_mapping_configured": bool(control.config.get("databases")),
            "verification_pending": [r for r in control.status(sync=False)["revisions"]]}


def dismiss(control, path, sha, reason):
    safe_path(control.root, path)
    if not isinstance(reason, str) or len(reason.strip()) < 10:
        raise FlowError("Provide a concrete reason (at least 10 characters) for excluding this exact file hash.")
    with control.store.db:
        cursor = control.store.db.execute("UPDATE files SET disposition='excluded',reason=?,updated=? WHERE path=? AND hash=?",
            (reason, now(), path, sha))
        if cursor.rowcount != 1:
            raise FlowError("Candidate changed or is unknown. Rescan and use its exact hash.")
    return {"path": path, "sha256": sha, "disposition": "excluded", "reason": reason}


def acknowledge(control, artifacts, revision_id):
    with control.store.db:
        for artifact in artifacts:
            if artifact.get("source_path") and artifact.get("source_sha256"):
                control.store.db.execute("UPDATE files SET disposition='registered',revision=?,reason=NULL WHERE path=? AND hash=?",
                    (revision_id, artifact["source_path"], artifact["source_sha256"]))


def hook(control, client, event, payload):
    state = scan(control)
    # This is an observation of helper invocation only, not proof that a client hook ran.
    control.store.set_check("hook_invocation:" + client, {"event": event, "last_seen": now()})
    actionable = bool(state["candidates"] or state["pending_sync"] or state["pending_remote_requests"] or state["coverage"].get("limitations"))
    text = ("DBFlow: " + encoded({k: state[k] for k in ("candidates", "pending_sync", "pending_remote_requests", "registry_configured", "database_mapping_configured", "coverage")})[:7000]
            + "\nConsulte dbflow_status. Registre alterações de banco, classifique candidatos e reporte pendências; não invente confirmação de aplicação. "
            "Se houver falta de configuração/acesso, informe o bloqueio uma vez e preserve a pendência. Consulte .db-control/WORKFLOW.md.")
    if event == "start":
        if client == "claude":
            return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}
        if client == "codex":
            return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}
        return {"additional_context": text}
    if event == "stop":
        session = str(payload.get("session_id") or payload.get("conversation_id") or "unknown")
        key = "stop:" + client + ":" + session
        signature = digest({k: state[k] for k in ("candidates", "pending_sync", "pending_remote_requests", "registry_configured", "database_mapping_configured")})
        previous = control.store.get_check(key)
        # Never trap the model in an endless loop awaiting credentials or a DBA.
        if actionable and not payload.get("stop_hook_active") and payload.get("loop_count", 0) == 0 and previous != signature:
            control.store.set_check(key, signature)
            if client in ("claude", "codex"):
                return {"decision": "block", "reason": text}
            return {"followup_message": text}
        return {}
    if actionable and client in ("claude", "codex"):
        return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}}
    return {}
