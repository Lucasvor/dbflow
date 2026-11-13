import getpass
import json
import uuid
from pathlib import Path
from .common import FlowError, atomic_write, digest, encoded, identifier, load_config, now, required_text, safe_path, target
from .store import Store
from .gitlab import GitLab


class Control:
    def __init__(self, root, actor=None):
        self.root = Path(root).resolve()
        self.config = load_config(self.root)
        self.store = Store(self.root)
        self.actor = actor or "local:" + getpass.getuser()

    def close(self):
        self.store.close()

    def event(self, kind, data, op):
        event = {"id": op, "schema_version": 1, "project_key": self.config["project_key"],
                 "kind": kind, "data": data, "actor": self.actor, "created_at": now()}
        self.store.add(event)
        return event

    def revisions(self):
        revisions = {e["data"]["revision_id"]: e["data"] for e in self.store.events("revision")}
        for event in self.store.events("abandonment"):
            original = event["data"].get("preserved_local_event", {}).get("data")
            if original:
                revisions.setdefault(original["revision_id"], original)
        return revisions

    def revision(self, revision_id):
        item = self.revisions().get(revision_id)
        if not item:
            raise FlowError("Revision is unknown. Synchronize the registry first.")
        return item

    def destination(self, database, environment):
        db = target(self.config, database, environment)
        identity = {"database_key": database, "engine": db["engine"], "environment": environment,
                    **db["environments"][environment]}
        return {**identity, "fingerprint": digest(identity)}

    def heads(self):
        revs = self.revisions()
        abandoned = {e["data"]["revision_id"] for e in self.store.events("abandonment")}
        active = {rid: r for rid, r in revs.items() if rid not in abandoned}
        parents = {r["parent_revision"] for r in active.values() if r.get("parent_revision")}
        heads = {}
        for rid, revision in active.items():
            if rid not in parents:
                heads.setdefault(revision["demand_id"], []).append(rid)
        return heads

    def mutate(self, kind, args, callback):
        op = identifier(args.get("operation_id") or str(uuid.uuid4()), "operation_id")
        result = self.store.operation(op, {"kind": kind, **args}, lambda: callback(op))
        sync = self.sync()
        result = {**result, "sync": sync}
        return result

    def register(self, args):
        # Refresh first, but offline registration is durable and explicitly pending.
        self.sync()
        def apply(op):
            demand = identifier(args.get("demand_id"), "demand_id")
            title = required_text(args.get("title"), "title", 500)
            parent = args.get("parent_revision")
            heads = self.heads().get(demand, [])
            if heads and heads != [parent]:
                raise FlowError("Provide the current parent_revision; competing revisions need reconciliation.")
            if not heads and parent:
                raise FlowError("Parent revision is not present locally.")
            changes = args.get("changes")
            if not isinstance(changes, list) or not 1 <= len(changes) <= 100:
                raise FlowError("Provide 1 to 100 changes.")
            clean, seen = [], set()
            for change in changes:
                cid = identifier(change.get("id"), "change id")
                if cid in seen:
                    raise FlowError("Duplicate change id.")
                seen.add(cid)
                database = identifier(change.get("database"), "database")
                db = target(self.config, database)
                script = required_text(change.get("script"), "script")
                name = identifier(change.get("filename"), "filename")
                obj = required_text(change.get("object"), "object identity", 500)
                item = {"id": cid, "database": database, "engine": db["engine"], "object": obj,
                        "filename": name, "script": script, "script_sha256": digest(script),
                        "kind": required_text(change.get("kind"), "change kind", 100),
                        "depends_on": change.get("depends_on", []),
                        "verification": required_text(change.get("verification"), "verification procedure", 10000)}
                for key in ("before_definition", "after_definition"):
                    if key in change:
                        item[key] = required_text(change[key], key)
                if change.get("source_path"):
                    source = change["source_path"]
                    safe_path(self.root, source)
                    sha = change.get("source_sha256")
                    if not isinstance(sha, str) or len(sha) != 64:
                        raise FlowError("source_sha256 is required when source_path is supplied.")
                    # Content-source binding; remote service never opens client files.
                    if sha != digest(script):
                        raise FlowError("Source hash must match the exact submitted script bytes (UTF-8).")
                    item.update(source_path=source, source_sha256=sha)
                clean.append(item)
            ordered = self.order_changes(clean)
            deps = args.get("dependencies", [])
            if not isinstance(deps, list):
                raise FlowError("dependencies must be revision IDs.")
            for dependency in deps:
                self.revision(dependency)
            data = {"demand_id": demand, "title": title, "parent_revision": parent,
                    "changes": ordered, "dependencies": deps}
            if len(encoded(data).encode()) > 4000000:
                raise FlowError("Revision exceeds 4 MB; split into explicit dependent revisions/demands.")
            data["revision_id"] = "r-" + digest(data)[:24]
            self.event("revision", data, op)
            for change in clean:
                if change.get("source_path"):
                    self.store.db.execute("UPDATE files SET disposition='registered',revision=?,reason=NULL WHERE path=? AND hash=?",
                        (data["revision_id"], change["source_path"], change["source_sha256"]))
            return {"revision_id": data["revision_id"], "demand_id": demand,
                    "artifacts": [{k: c[k] for k in ("id", "source_path", "source_sha256") if k in c} for c in clean],
                    "application": "not_implied_by_registration"}
        return self.mutate("revision", args, apply)

    @staticmethod
    def order_changes(changes):
        by_id = {c["id"]: c for c in changes}
        done, visiting, ordered = set(), set(), []
        def visit(cid):
            if cid not in by_id:
                raise FlowError("Unknown change dependency: " + str(cid))
            if cid in visiting:
                raise FlowError("Cyclic change dependencies.")
            if cid in done:
                return
            visiting.add(cid)
            deps = by_id[cid].get("depends_on", [])
            if not isinstance(deps, list):
                raise FlowError("depends_on must be a list.")
            for dep in deps:
                visit(dep)
            visiting.remove(cid)
            done.add(cid)
            ordered.append(by_id[cid])
        for cid in by_id:
            visit(cid)
        return ordered

    def prepare(self, args):
        def apply(op):
            rids = args.get("revision_ids", [])
            if not isinstance(rids, list) or not rids or len(rids) != len(set(rids)):
                raise FlowError("Select explicit, distinct revision_ids.")
            environment = identifier(args.get("environment"), "environment")
            revisions = [self.revision(rid) for rid in rids]
            abandoned = {e["data"]["revision_id"] for e in self.store.events("abandonment")}
            if set(rids) & abandoned:
                raise FlowError("An abandoned local revision cannot be delivered.")
            if len({r["demand_id"] for r in revisions}) != len(revisions):
                raise FlowError("Select only one revision per demand.")
            objects = set()
            for rev in revisions:
                for dependency in rev["dependencies"]:
                    if dependency not in rids and not self.is_confirmed(dependency, environment):
                        raise FlowError("Dependency is neither selected nor confirmed in destination: " + dependency)
                for c in rev["changes"]:
                    target(self.config, c["database"], environment)
                    key = (c["database"], c["object"])
                    if key in objects:
                        raise FlowError("Overlapping objects across selected revisions. Reconcile into an explicit tested revision first.")
                    objects.add(key)
            # Stable dependency order across demands.
            ordered = []
            def add(rid):
                if rid in ordered:
                    return
                for dep in self.revision(rid)["dependencies"]:
                    if dep in rids:
                        add(dep)
                ordered.append(rid)
            for rid in rids:
                add(rid)
            data = {"revision_ids": ordered, "environment": environment,
                    "targets": {c["database"]: self.destination(c["database"], environment)
                                for r in revisions for c in r["changes"]}}
            data["bundle_id"] = "b-" + digest(data)[:24]
            self.event("bundle", data, op)
            return {**data, "dispatch": "not_sent", "application": "pending"}
        result = self.mutate("bundle", args, apply)
        directory = self.export_bundle(result["bundle_id"])
        result["local_directory"] = str(directory)
        result["bundle_files"] = {p.relative_to(directory).as_posix(): p.read_text(encoding="utf-8")
                                  for p in directory.rglob("*") if p.is_file()}
        if len(encoded(result).encode()) > 4000000:
            raise FlowError("Bundle exceeds transfer size; prepare smaller dependent deliveries.")
        return result

    def abandon(self, args):
        self.sync()
        def apply(op):
            rid = args.get("revision_id")
            revision = self.revision(rid)
            rows = [r for r in self.store.db.execute("SELECT * FROM events WHERE sync='pending'")
                    if json.loads(r["payload"]).get("data", {}).get("revision_id") == rid
                    and json.loads(r["payload"])["kind"] == "revision"]
            if len(rows) != 1:
                raise FlowError("Only a locally pending, unpublished revision can be abandoned.")
            abandoned = {e["data"]["revision_id"] for e in self.store.events("abandonment")}
            if any(r.get("parent_revision") == rid and key not in abandoned for key, r in self.revisions().items()):
                raise FlowError("Resolve descendant revisions first.")
            cfg = self.config.get("gitlab", {})
            if cfg.get("url") and cfg.get("project_id"):
                remote = GitLab(self.config)
                remote_event = remote.file(remote.prefix() + "/events/" + rows[0]["id"] + ".json")
                if remote_event:
                    raise FlowError("Remote contains this revision; synchronize before reconciling. Published history cannot be abandoned locally.")
            bundles = [e["data"]["bundle_id"] for e in self.store.events("bundle") if rid in e["data"].get("revision_ids", [])]
            if any(e["data"]["bundle_id"] in bundles for e in self.store.events("dispatch_report")) or any(e["data"]["revision_id"] == rid for e in self.store.events("confirmation")):
                raise FlowError("Revision has dispatch or application evidence; reconciliation needs a new demand preserving those references.")
            note = required_text(args.get("reason"), "reason", 4000)
            self.store.db.execute("UPDATE events SET sync='abandoned',error=? WHERE id=?", (note, rows[0]["id"]))
            self.event("abandonment", {"revision_id": rid, "reason": note,
                "cancelled_undispatched_bundles": bundles, "preserved_local_event": json.loads(rows[0]["payload"])}, op)
            return {"revision_id": rid, "disposition": "abandoned_locally", "history": "preserved"}
        return self.mutate("abandonment", args, apply)

    def cancelled_bundles(self):
        return {bid for e in self.store.events("abandonment") for bid in e["data"].get("cancelled_undispatched_bundles", [])}

    def bundle(self, bundle_id, allow_cancelled=False):
        if bundle_id in self.cancelled_bundles() and not allow_cancelled:
            raise FlowError("Bundle was cancelled and cannot be exported or dispatched. Its history is preserved.")
        for event in self.store.events("bundle"):
            if event["data"]["bundle_id"] == bundle_id:
                return event["data"]
        raise FlowError("Unknown bundle.")

    def get_revision(self, args):
        sync = self.sync()
        rid = args.get("revision_id")
        return {"revision": self.revision(rid), "sync": sync,
                "abandoned": rid in {e["data"]["revision_id"] for e in self.store.events("abandonment")}}

    def get_bundle(self, args):
        sync = self.sync()
        bid = args.get("bundle_id")
        bundle = self.bundle(bid, allow_cancelled=True)
        cancelled = bid in self.cancelled_bundles()
        result = {"bundle": bundle, "sync": sync, "cancelled": cancelled,
                  "dispatch_reports": [e for e in self.store.events("dispatch_report") if e["data"]["bundle_id"] == bid]}
        if not cancelled:
            directory = self.export_bundle(bid)
            result.update(bundle_id=bid, local_directory=str(directory), bundle_files={
                p.relative_to(directory).as_posix(): p.read_text(encoding="utf-8") for p in directory.rglob("*") if p.is_file()})
        if len(encoded(result).encode()) > 4000000:
            raise FlowError("Bundle too large for a single MCP transfer.")
        return result

    def export_bundle(self, bundle_id):
        bundle = self.bundle(bundle_id)
        directory = self.root / ".db-control/local/packages" / bundle_id
        manifest = {**bundle, "scripts": []}
        for rid in bundle["revision_ids"]:
            for i, change in enumerate(self.revision(rid)["changes"], 1):
                rel = f"{rid}/{change['database']}/{i:03d}-{change['filename']}"
                path = safe_path(directory, rel)
                if path.exists() and digest(path.read_bytes()) != change["script_sha256"]:
                    raise FlowError("Existing package file was modified; refusing overwrite.")
                atomic_write(path, change["script"])
                manifest["scripts"].append({"path": rel, "sha256": change["script_sha256"], "change_id": change["id"],
                    "revision_id": rid, "database": change["database"], "verification": change["verification"]})
        atomic_write(directory / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        email = (f"Assunto: Aplicação de mudanças de banco — {bundle_id} — {bundle['environment']}\n\n"
                 f"Solicito aplicação do pacote {bundle_id} no ambiente {bundle['environment']}.\n"
                 "Executar somente os scripts desta revisão, respeitando a ordem do manifest.json.\n"
                 "Informar resultado por banco e etapa, incluindo falhas e execução parcial.\n"
                 "Este arquivo é um rascunho; sua geração não registra envio nem aplicação.\n")
        atomic_write(directory / "email.txt", email)
        return directory

    def dispatch(self, args):
        def apply(op):
            bundle = self.bundle(args.get("bundle_id"))
            data = {"bundle_id": bundle["bundle_id"], "reference": required_text(args.get("reference"), "email reference", 2000),
                    "reported_sent_at": required_text(args.get("sent_at"), "sent_at", 100)}
            self.event("dispatch_report", data, op)
            return {**data, "application": "pending", "source": "user_report"}
        return self.mutate("dispatch_report", args, apply)

    def confirm(self, args):
        def apply(op):
            rid = args.get("revision_id")
            revision = self.revision(rid)
            database, environment = args.get("database"), args.get("environment")
            target(self.config, database, environment)
            destination = self.destination(database, environment)
            if args.get("bundle_id"):
                bundle = self.bundle(args["bundle_id"])
                if rid not in bundle["revision_ids"] or environment != bundle["environment"] or database not in bundle["targets"]:
                    raise FlowError("Confirmation does not belong to the selected bundle.")
                destination = bundle["targets"][database]
            if args.get("target_fingerprint") != destination["fingerprint"]:
                raise FlowError("Destination fingerprint differs; identify the exact instance/database and bundle before confirming.")
            ids = args.get("change_ids")
            known = {c["id"] for c in revision["changes"] if c["database"] == database}
            if not isinstance(ids, list) or not ids or not set(ids) <= known:
                raise FlowError("Select explicit change_ids belonging to this revision and database.")
            kind = args.get("kind")
            if kind not in ("manual_applied", "execution_succeeded", "state_matches", "state_differs", "failed"):
                raise FlowError("Unknown confirmation kind.")
            evidence = required_text(args.get("evidence"), "evidence or user statement", 20000)
            data = {"revision_id": rid, "database": database, "environment": environment,
                    "change_ids": sorted(set(ids)), "kind": kind, "evidence": evidence,
                    "reported_by": self.actor, "target": destination,
                    "observed_at": required_text(args.get("observed_at"), "observed_at", 100)}
            self.event("confirmation", data, op)
            return {**data, "scope": "only_the_exact_revision_and_selected_changes"}
        return self.mutate("confirmation", args, apply)

    def state(self, rid, environment):
        results = []
        for change in self.revision(rid)["changes"]:
            if environment not in self.config.get("databases", {}).get(change["database"], {}).get("environments", {}):
                results.append({"change_id": change["id"], "database": change["database"], "environment": environment,
                                "execution": "destination_not_mapped", "observation": "not_checked", "target": None})
                continue
            events = [e for e in self.store.events("confirmation") if e["data"]["revision_id"] == rid
                      and e["data"]["environment"] == environment and e["data"]["database"] == change["database"]
                      and e["data"].get("target", {}).get("fingerprint") == self.destination(change["database"], environment)["fingerprint"]
                      and change["id"] in e["data"]["change_ids"]]
            execution, observation = "pending", "not_checked"
            for event in events:
                kind = event["data"]["kind"]
                if kind in ("manual_applied", "execution_succeeded", "failed"):
                    execution = kind
                else:
                    observation = kind
            results.append({"change_id": change["id"], "database": change["database"], "environment": environment,
                            "target": self.destination(change["database"], environment),
                            "execution": execution, "observation": observation})
        return results

    def is_confirmed(self, rid, environment):
        return all((s["execution"] in ("manual_applied", "execution_succeeded") or s["observation"] == "state_matches")
                   and s["execution"] != "failed" and s["observation"] != "state_differs" for s in self.state(rid, environment))

    def snapshot(self, args):
        def apply(op):
            database, environment = args.get("database"), args.get("environment")
            target(self.config, database, environment)
            destination = self.destination(database, environment)
            if args.get("target_fingerprint") != destination["fingerprint"]:
                raise FlowError("Snapshot destination fingerprint differs from the configured target.")
            objects = args.get("objects")
            if not isinstance(objects, dict) or len(objects) > 5000 or any(not isinstance(v, str) for v in objects.values()):
                raise FlowError("objects must map object identities to definitions, at most 5000 entries.")
            if len(encoded(objects).encode()) > 8000000:
                raise FlowError("Snapshot too large; split the declared scope.")
            covered = args.get("covered_objects", [])
            if not isinstance(covered, list) or any(not isinstance(x, str) for x in covered):
                raise FlowError("covered_objects must explicitly list completely inspected object identities.")
            data = {"database": database, "environment": environment, "objects": objects,
                    "target": destination,
                    "covered_objects": covered, "captured_at": required_text(args.get("captured_at"), "captured_at", 100),
                    "source": required_text(args.get("source"), "source", 2000), "limitations": args.get("limitations", [])}
            data["snapshot_id"] = "s-" + digest(data)[:24]
            self.event("snapshot", data, op)
            comparisons = []
            rid = args.get("revision_id")
            if rid:
                for change in self.revision(rid)["changes"]:
                    if change["database"] != database:
                        continue
                    key = change["object"]
                    expected = change.get("after_definition")
                    if expected is None:
                        status = "no_expected_definition"
                    elif key not in objects:
                        status = "missing_in_declared_scope" if key in covered else "inconclusive"
                    else:
                        # Text equality only: no claim of semantic equivalence or execution proof.
                        normalize = lambda s: s.replace("\r\n", "\n").strip()
                        status = "text_matches" if normalize(expected) == normalize(objects[key]) else "text_differs"
                    comparisons.append({"change_id": change["id"], "object": key, "comparison": status})
            return {"snapshot_id": data["snapshot_id"], "comparison_basis": rid or "baseline_not_established",
                    "comparisons": comparisons, "application_status_changed": False}
        return self.mutate("snapshot", args, apply)

    def sync(self):
        cfg = self.config.get("gitlab", {})
        if not cfg.get("url") or not cfg.get("project_id"):
            return {"state": "not_configured", "pending": self.pending_count()}
        try:
            remote = GitLab(self.config)
            incoming = remote.pull()
            with self.store.db:
                for event, commit in incoming:
                    if event.get("project_key") != self.config["project_key"] or event.get("schema_version") != 1:
                        raise FlowError("Remote registry event belongs to an incompatible project/schema.")
                    self.validate_remote_event(event)
                    self.store.add(event, "synced", commit)
            pending = list(self.store.db.execute("SELECT id,payload FROM events WHERE sync='pending' ORDER BY created,id"))
            for row in pending:
                try:
                    commit = remote.publish(json.loads(row["payload"]))
                    with self.store.db:
                        self.store.db.execute("UPDATE events SET sync='synced',remote_commit=?,error=NULL WHERE id=?", (commit, row["id"]))
                except FlowError as exc:
                    with self.store.db:
                        self.store.db.execute("UPDATE events SET error=? WHERE id=?", (str(exc), row["id"]))
                    raise
            return {"state": "synced", "pending": self.pending_count()}
        except FlowError as exc:
            return {"state": "pending", "pending": self.pending_count(), "reason": str(exc)}

    def validate_remote_event(self, event, depth=0):
        if depth > 1 or not isinstance(event, dict):
            raise FlowError("Malformed remote event.")
        identifier(event.get("id"), "remote event id")
        required_text(event.get("created_at"), "remote event timestamp", 100)
        data = event.get("data")
        if not isinstance(data, dict):
            raise FlowError("Malformed remote event payload.")
        kind = event.get("kind")
        if kind == "revision":
            identifier(data.get("revision_id"), "revision id")
            identifier(data.get("demand_id"), "demand id")
            if data.get("parent_revision"):
                identifier(data["parent_revision"])
            if not isinstance(data.get("changes"), list) or not data["changes"]:
                raise FlowError("Remote revision has no changes.")
            for change in data["changes"]:
                for key in ("id", "database", "filename"):
                    identifier(change.get(key), "remote " + key)
                required_text(change.get("script"), "remote script")
                if digest(change["script"]) != change.get("script_sha256"):
                    raise FlowError("Remote script hash differs.")
            self.order_changes(data["changes"])
        elif kind == "bundle":
            identifier(data.get("bundle_id"))
            identifier(data.get("environment"))
            for rid in data.get("revision_ids", []):
                identifier(rid)
        elif kind in ("snapshot", "confirmation"):
            identifier(data.get("database"))
            identifier(data.get("environment"))
        elif kind == "dispatch_report":
            identifier(data.get("bundle_id"))
        elif kind == "abandonment":
            identifier(data.get("revision_id"))
            self.validate_remote_event(data.get("preserved_local_event"), depth + 1)
        else:
            raise FlowError("Unknown remote event kind.")

    def pending_count(self):
        return self.store.db.execute("SELECT count(*) FROM events WHERE sync='pending'").fetchone()[0]

    def status(self, sync=True, demand_id=None, offset=0, limit=50):
        if not isinstance(offset, int) or not isinstance(limit, int) or offset < 0 or not 1 <= limit <= 200:
            raise FlowError("Use offset >= 0 and limit between 1 and 200.")
        result = self.sync() if sync else {"state": "not_checked", "pending": self.pending_count()}
        revisions = self.revisions()
        overlaps = {}
        for demand, heads in self.heads().items():
            for rid in heads:
                for change in revisions[rid]["changes"]:
                    key = change["database"] + ":" + change["object"]
                    overlaps.setdefault(key, []).append({"demand": demand, "revision": rid})
        selected = [(rid, rev) for rid, rev in revisions.items() if not demand_id or rev["demand_id"] == demand_id]
        page = selected[offset:offset + limit]
        return {"project_key": self.config["project_key"], "sync": result, "heads": self.heads(),
                "destinations": {db: {env: self.destination(db, env) for env in cfg["environments"]} for db, cfg in self.config.get("databases", {}).items()},
                "total_revisions": len(selected), "next_offset": offset + limit if offset + limit < len(selected) else None,
                "revisions": [{"revision_id": rid, "demand_id": rev["demand_id"], "title": rev["title"],
                    "targets": {env: self.state(rid, env) for env in sorted({env for c in rev["changes"]
                        for env in self.config.get("databases", {}).get(c["database"], {}).get("environments", {})})}} for rid, rev in page],
                "bundles": [{**e["data"], "cancelled": e["data"]["bundle_id"] in self.cancelled_bundles()} for e in self.store.events("bundle")],
                "dispatch_reports": [e["data"] for e in self.store.events("dispatch_report")],
                "abandoned_revisions": [e["data"]["revision_id"] for e in self.store.events("abandonment")],
                "overlaps": {k: v for k, v in overlaps.items() if len(v) > 1},
                "coverage": "Only artifacts and evidence supplied; no business database connection; no email monitoring."}
