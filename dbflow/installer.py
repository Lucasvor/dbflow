import base64
import copy
import json
import re
import sys
import shutil
import tomllib
from pathlib import Path
from .common import FlowError, atomic_write, digest, identifier, now, safe_path
from .audit import DEFAULT_GLOBS

START = "<!-- DBFLOW:BEGIN -->"
END = "<!-- DBFLOW:END -->"
RULE = """## Controle automático de alterações de banco — DBFlow

Este projeto usa `.db-control/project.json` e o MCP `dbflow`.
Ao iniciar/retomar trabalho, consulte `dbflow_status` e `dbflow_scan` sem esperar invocação de skill.
Quando houver mudança destinada a banco (SQL, migrations ou scripts de dados de qualquer motor), leia
`.db-control/WORKFLOW.md` e registre uma revisão completa pelo MCP após cada conjunto coerente de alterações.
Identifique a demanda pelo contexto; consulte registros existentes antes de criar um ID. Pergunte só quando
a identidade ou o destino permanecerem ambíguos. Não atribua divergências antigas à demanda atual por suposição.
Antes de concluir, faça a auditoria local e reconcilie candidatos. Uma falha de sincronização permanece pendente.
Ao preparar entrega, delegue uma revisão independente a `dbflow-auditor` (ou um subagente equivalente) com o
diff, relatório de auditoria, revisões e destinos. Se o cliente não suportar subagentes, informe a limitação.
Não delegue recursivamente: um auditor não chama outro auditor.
O escopo é apenas banco. Não faça commits de aplicação como efeito deste processo.
MCP não acessa bancos. Use somente ferramentas autorizadas do usuário ou exportações para leitura.
Desenvolvedor altera TI; DBA aplica HML/PRD. Gere pacote e rascunho de email; só envie se explicitamente solicitado.
Confirmações são por revisão + banco + ambiente + etapas. Diferencie aplicação informada de estado observado.
Nunca marque aplicado porque houve commit, preparo ou envio de email. R1 confirmada não confirma R2.
Não declare completude se houver leitura parcial, execução externa não observável ou configuração faltante.
"""

AUDITOR = """Você é o auditor independente do fluxo DBFlow, somente leitura sobre produto e registro central.
Receba do agente principal o diff, relatório de `dbflow_scan`, `dbflow_status`, revisões e pacote selecionado.
Leia `.db-control/WORKFLOW.md`. Procure scripts esquecidos, fonte alterada após registro, destinos ambíguos,
dependências faltantes, sobreposição de objetos, sincronização pendente e confirmações de revisão errada.
Verifique que dados de aplicação não foram enviados como inventário e que snapshots declaram cobertura.
Não conecte bancos, não registre confirmações, não envie email, não altere arquivos nem aprove uma entrega.
Reporte achados com evidência, severidade e ação; diferencie ausência de problema de cobertura inconclusiva.
Não chame outros subagentes. O agente principal resolve os achados e preserva pendências externas.
"""


def merge_block(text, body, start=START, end=END):
    count_a, count_b = text.count(start), text.count(end)
    if count_a != count_b or count_a > 1:
        raise FlowError("Malformed managed markers; no changes were applied.")
    block = start + "\n" + body.rstrip() + "\n" + end
    if count_a:
        return re.sub(re.escape(start) + r".*?" + re.escape(end), lambda _: block, text, flags=re.S)
    return text + ("\n\n" if text and not text.endswith("\n\n") else "") + block + "\n"


def default_config(root):
    key = re.sub(r"[^A-Za-z0-9_.-]", "-", root.name).strip(".-")[:100] or "project"
    return {"version": 1, "project_key": key.lower(), "mode": "local",
            "gitlab": {"url": "", "project_id": "", "branch": "main", "token_env": "DBFLOW_GITLAB_TOKEN"},
            "remote_url": "", "remote_token_env": "DBFLOW_SERVICE_TOKEN", "databases": {},
            "watch": {"include": DEFAULT_GLOBS}, "allowed_origins": []}


def install(root, clients=("codex", "claude", "cursor"), apply=False, source=None, configuration=None):
    root = Path(root).resolve()
    if root.exists() and not root.is_dir():
        raise FlowError("Project path is not a directory.")
    if set(clients) - {"codex", "claude", "cursor"}:
        raise FlowError("Unknown client.")
    source = Path(source or Path(__file__).resolve().parents[1])
    planned = {}
    def read(rel):
        path = safe_path(root, rel)
        return path.read_text(encoding="utf-8-sig") if path.exists() else ""
    def read_json(rel):
        try:
            value = json.loads(read(rel) or "{}")
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except ValueError:
            raise FlowError(f"{rel} is not a JSON object. No changes were applied.") from None
    def put_json(rel, value):
        planned[rel] = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
    existing_manifest = read_json(".db-control/install-manifest.json")
    config = read_json(".db-control/project.json") or default_config(root)
    if configuration:
        from .configuration import settings
        config = settings(config, **configuration)
    put_json(".db-control/project.json", config)
    python = str(Path(sys.executable).resolve())
    launch = str(root / ".db-control/runtime/dbctl.py")
    dotnet = shutil.which('dotnet') or 'dotnet'
    server = {"command": dotnet, "args": [str(root / '.db-control/runtime/dotnet/DbFlow.Server.dll'), '--project', str(root), '--stdio']}
    # Relative project paths avoid shell interpolation of the user-selected directory.
    executable = python.replace("\\", "/")
    if any(c in executable for c in ('"', '$', '`', '\n', '\r')):
        raise FlowError("Python executable path contains unsupported shell metacharacters.")
    hook_command = f'"{executable}" ".db-control/runtime/dbctl.py" --project "." hook'
    for client in clients:
        if client in ("claude", "codex"):
            rel = "CLAUDE.md" if client == "claude" else ("AGENTS.override.md" if (root / "AGENTS.override.md").exists() else "AGENTS.md")
            planned[rel] = merge_block(read(rel), RULE).encode()
        if client == "claude":
            cfg = read_json(".mcp.json")
            servers = cfg.setdefault("mcpServers", {})
            if "dbflow" in servers and ".mcp.json" not in existing_manifest.get("files", {}):
                raise FlowError("An unmanaged dbflow MCP entry already exists; choose a different name before installing.")
            servers["dbflow"] = {"type": "stdio", **server}
            put_json(".mcp.json", cfg)
            settings = read_json(".claude/settings.json")
            hooks = settings.setdefault("hooks", {})
            for event, mode, matcher in (("SessionStart", "start", None), ("PostToolUse", "after", ".*"), ("Stop", "stop", None)):
                entries = hooks.setdefault(event, [])
                cleaned = []
                for entry in entries:
                    item = copy.deepcopy(entry)
                    item["hooks"] = [h for h in item.get("hooks", []) if ".db-control/runtime/dbctl.py" not in h.get("command", "")]
                    if item["hooks"]:
                        cleaned.append(item)
                item = {"hooks": [{"type": "command", "command": hook_command + " --client claude --event " + mode, "timeout": 15}]}
                if matcher:
                    item["matcher"] = matcher
                hooks[event] = cleaned + [item]
            put_json(".claude/settings.json", settings)
            planned[".claude/agents/dbflow-auditor.md"] = ("---\nname: dbflow-auditor\ndescription: Audita pendências de mudanças de banco antes de entregas ao DBA.\ntools: Read, Glob, Grep\n---\n" + AUDITOR).encode()
        elif client == "cursor":
            cfg = read_json(".cursor/mcp.json")
            servers = cfg.setdefault("mcpServers", {})
            if "dbflow" in servers and ".cursor/mcp.json" not in existing_manifest.get("files", {}):
                raise FlowError("An unmanaged dbflow Cursor MCP entry already exists.")
            servers["dbflow"] = server
            put_json(".cursor/mcp.json", cfg)
            hooks = read_json(".cursor/hooks.json")
            if hooks.get("version", 1) != 1:
                raise FlowError("Unsupported Cursor hooks schema version.")
            hooks["version"] = 1
            events = hooks.setdefault("hooks", {})
            for event, mode in (("sessionStart", "start"), ("afterFileEdit", "after"), ("afterShellExecution", "after"), ("afterMCPExecution", "after"), ("stop", "stop")):
                entries = [h for h in events.get(event, []) if ".db-control/runtime/dbctl.py" not in h.get("command", "")]
                item = {"command": hook_command + " --client cursor --event " + mode}
                if event == "stop":
                    item["loop_limit"] = 1
                events[event] = entries + [item]
            put_json(".cursor/hooks.json", hooks)
            planned[".cursor/rules/dbflow.mdc"] = ("---\ndescription: Controle automático de mudanças de banco\nalwaysApply: true\n---\n" + RULE).encode()
            planned[".cursor/agents/dbflow-auditor.md"] = ("---\nname: dbflow-auditor\ndescription: Audita pendências de mudanças de banco antes de entregas ao DBA.\nreadonly: true\n---\n" + AUDITOR).encode()
        elif client == "codex":
            rel = ".codex/config.toml"
            contents = read(rel)
            start, end = "# DBFLOW:BEGIN", "# DBFLOW:END"
            parsed = tomllib.loads(contents)
            if "dbflow" in parsed.get("mcp_servers", {}) and start not in contents:
                raise FlowError("An unmanaged dbflow Codex MCP entry already exists.")
            # JSON quoted strings are also valid TOML basic strings for these values.
            body = ("[mcp_servers.dbflow]\ncommand = " + json.dumps(dotnet) + "\nargs = " + json.dumps(server["args"]) + "\n\n"
                + "[[hooks.SessionStart]]\n[[hooks.SessionStart.hooks]]\ntype = \"command\"\n"
                + "command = " + json.dumps(hook_command + " --client codex --event start") + "\ntimeout = 15\n\n"
                + "[[hooks.PostToolUse]]\nmatcher = \".*\"\n[[hooks.PostToolUse.hooks]]\ntype = \"command\"\n"
                + "command = " + json.dumps(hook_command + " --client codex --event after") + "\ntimeout = 15\n\n"
                + "[[hooks.Stop]]\n[[hooks.Stop.hooks]]\ntype = \"command\"\n"
                + "command = " + json.dumps(hook_command + " --client codex --event stop") + "\ntimeout = 15\n")
            merged = merge_block(contents, body, start, end)
            tomllib.loads(merged)
            planned[rel] = merged.encode()
            planned[".codex/agents/dbflow-auditor.toml"] = ("name = \"dbflow-auditor\"\ndescription = \"Audita pendências de banco antes da entrega ao DBA.\"\n"
                + "developer_instructions = " + json.dumps(AUDITOR, ensure_ascii=False) + "\n").encode()
    planned[".gitignore"] = merge_block(read(".gitignore"), ".db-control/local/\n.db-control/backups/\n.db-control/install-manifest.json\n**/__pycache__/", "# DBFLOW:BEGIN", "# DBFLOW:END").encode()
    for path in [source / "dbctl.py", *sorted((source / "dbflow").glob("*.py"))]:
        planned[".db-control/runtime/" + path.relative_to(source).as_posix()] = path.read_bytes()
    published = source / 'server/publish'
    if not published.exists():
        published = source / 'dotnet'
    if not (published / 'DbFlow.Server.dll').exists():
        if apply:
            raise FlowError('Build the .NET 8 server first: ./Build.ps1. No installation changes were applied.')
    else:
        for path in sorted(published.rglob('*')):
            if path.is_file():
                planned['.db-control/runtime/dotnet/' + path.relative_to(published).as_posix()] = path.read_bytes()
    for path in sorted((source / "templates").glob("*")):
        planned[".db-control/" + path.name] = path.read_bytes()
        planned[".db-control/runtime/templates/" + path.name] = path.read_bytes()
    # Validate all destinations and conflicts before any write.
    originals = {}
    for rel, content in planned.items():
        path = safe_path(root, rel)
        originals[rel] = path.read_bytes() if path.exists() else None
        if path.exists() and rel not in existing_manifest.get("files", {}) and rel.startswith((".db-control/runtime/", ".cursor/rules/dbflow", ".cursor/agents/dbflow", ".claude/agents/dbflow", ".codex/agents/dbflow")):
            raise FlowError("Unmanaged file would be overwritten: " + rel)
        managed = existing_manifest.get("files", {}).get(rel)
        mergeable = rel in ("AGENTS.md", "AGENTS.override.md", "CLAUDE.md", ".gitignore", ".mcp.json", ".cursor/mcp.json", ".claude/settings.json", ".cursor/hooks.json", ".codex/config.toml", ".db-control/project.json")
        if managed and path.exists() and digest(originals[rel]) != managed["installed_sha256"] and not mergeable:
            raise FlowError("Managed file was edited; preserve or reconcile it before updating: " + rel)
    changes = [rel for rel in planned if originals[rel] != planned[rel]]
    summary = {"project": str(root), "clients": list(clients), "mode": "apply" if apply else "dry_run", "files_to_write": changes,
               "gitlab": "configured" if config['gitlab'].get('url') else "requires_repository_configuration", "database_connections": "none", "client_runtime_recognition": "not_tested", "mcp_runtime": "net8.0", "dotnet_build_required": not (published / 'DbFlow.Server.dll').exists()}
    if not apply or not changes:
        return summary
    manifest = copy.deepcopy(existing_manifest) or {"version": 1, "files": {}}
    stamp = now().replace(":", "-")
    written = []
    try:
        for rel in changes:
            previous = originals[rel]
            if previous is not None:
                atomic_write(safe_path(root, ".db-control/backups/" + stamp + "/" + rel), previous)
            info = manifest["files"].setdefault(rel, {"original_base64": base64.b64encode(previous).decode() if previous is not None else None})
            old_info = existing_manifest.get("files", {}).get(rel)
            if old_info and previous is not None and digest(previous) != old_info["installed_sha256"]:
                # An update may merge user changes. Never later restore an obsolete pre-install version over them.
                info["preserve_on_uninstall"] = True
            info["installed_sha256"] = digest(planned[rel])
            atomic_write(safe_path(root, rel), planned[rel])
            written.append(rel)
        manifest["updated_at"] = now()
        manifest["clients"] = sorted(set(manifest.get("clients", [])) | set(clients))
        atomic_write(root / ".db-control/install-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    except Exception:
        for rel in reversed(written):
            path = safe_path(root, rel)
            if originals[rel] is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, originals[rel])
        raise
    return summary


def uninstall(root, apply=False):
    root = Path(root).resolve()
    path = root / ".db-control/install-manifest.json"
    if not path.exists():
        raise FlowError("No installation manifest found.")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    restored, conflicts = [], []
    for rel, info in manifest["files"].items():
        dest = safe_path(root, rel)
        if info.get("preserve_on_uninstall") or (dest.exists() and digest(dest.read_bytes()) != info["installed_sha256"]):
            conflicts.append(rel)
            continue
        restored.append(rel)
        if apply:
            if info["original_base64"] is None:
                dest.unlink(missing_ok=True)
            else:
                atomic_write(dest, base64.b64decode(info["original_base64"]))
    if apply:
        manifest["files"] = {k: v for k, v in manifest["files"].items() if k in conflicts}
        atomic_write(path, json.dumps(manifest, indent=2))
    return {"restored_or_removed": restored, "preserved_because_modified": conflicts, "local_journal": "preserved", "applied": apply}
