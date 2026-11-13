using System.Text;
using System.Text.Json.Nodes;

namespace DbFlow;

public sealed class Control(string root, JsonObject config, string actor, Func<JsonObject, GitLab>? gitlabFactory = null) : IDisposable
{
    public string Root { get; } = root;
    public JsonObject Config { get; } = config;
    public Store Store { get; } = new(root);
    List<JsonObject> Events(string kind) => Store.Events(kind);
    JsonObject Data(JsonObject e) => e.O("data");
    void Event(string kind, JsonObject data, string op) => Store.Add(J.Obj(("id", op), ("schema_version", 1), ("project_key", Config.S("project_key")), ("kind", kind), ("data", data), ("actor", actor), ("created_at", J.Now())));
    public Dictionary<string, JsonObject> Revisions()
    {
        var result = new Dictionary<string, JsonObject>();
        foreach (var d in Events("revision").Select(Data)) result[d.S("revision_id")] = d;
        foreach (var e in Events("abandonment")) { var d = e.O("data").O("preserved_local_event").O("data"); if (d.Count > 0) result.TryAdd(d.S("revision_id"), d); }
        return result;
    }
    JsonObject Revision(string id) => Revisions().GetValueOrDefault(id) ?? throw new FlowException("Unknown revision; synchronize the registry first.");
    HashSet<string> Abandoned => Events("abandonment").Select(e => e.O("data").S("revision_id")).ToHashSet();
    HashSet<string> Cancelled => Events("abandonment").SelectMany(e => e.O("data").Strings("cancelled_undispatched_bundles")).ToHashSet();
    public JsonObject Destination(string database, string environment)
    {
        var db = Database(database); var env = db.O("environments")[environment] as JsonObject ?? throw new FlowException("Environment is not mapped for this database.");
        var result = J.Obj(("database_key", database), ("engine", db.S("engine")), ("environment", environment));
        foreach (var item in env) result[item.Key] = item.Value?.DeepClone();
        result["fingerprint"] = J.Hash(result); return result;
    }
    JsonObject Database(string name) => Config.O("databases")[J.Id(name)] as JsonObject ?? throw new FlowException("Logical database is not mapped.");
    Dictionary<string, List<string>> Heads()
    {
        var active = Revisions().Where(r => !Abandoned.Contains(r.Key)).ToDictionary();
        var parents = active.Values.Select(r => r.S("parent_revision")).ToHashSet();
        return active.Where(r => !parents.Contains(r.Key)).GroupBy(r => r.Value.S("demand_id")).ToDictionary(g => g.Key, g => g.Select(r => r.Key).ToList());
    }
    JsonObject Mutate(string kind, JsonObject args, Func<string, JsonObject> apply)
    {
        var op = J.Id(args.S("operation_id")); var input = (JsonObject)args.DeepClone();
        if (!input.ContainsKey("kind")) input["kind"] = kind;
        var result = Store.Operation(op, input, () => apply(op)); result["sync"] = Sync(); return result;
    }
    public JsonObject Register(JsonObject args)
    {
        Sync();
        return Mutate("revision", args, op =>
        {
            var demand = J.Id(args.S("demand_id")); var title = J.Text(args.S("title"), "title", 500); var parent = args.S("parent_revision");
            var heads = Heads().GetValueOrDefault(demand) ?? [];
            if (heads.Count > 0 ? heads.Count != 1 || heads[0] != parent : parent != "") throw new FlowException("Provide current parent_revision; competing revisions require reconciliation.");
            var changes = args.A("changes"); if (changes.Count is < 1 or > 100) throw new FlowException("Provide 1 to 100 changes.");
            var clean = new JsonArray(); var seen = new HashSet<string>();
            foreach (var c in changes)
            {
                var id = J.Id(c.S("id")); if (!seen.Add(id)) throw new FlowException("Duplicate change id.");
                var database = J.Id(c.S("database")); var script = J.Text(c.S("script"), "script");
                var item = J.Obj(("id", id), ("database", database), ("engine", Database(database).S("engine")), ("object", J.Text(c.S("object"), "object", 500)),
                    ("filename", J.Id(c.S("filename"))), ("script", script), ("script_sha256", J.Hash(script)), ("kind", J.Text(c.S("kind"), "kind", 100)),
                    ("depends_on", c.A("depends_on")), ("verification", J.Text(c.S("verification"), "verification", 10000)));
                foreach (var key in new[] { "before_definition", "after_definition" }) if (c?[key] != null) item[key] = J.Text(c.S(key), key);
                if (c.S("source_path") != "")
                {
                    J.SafePath(Root, c.S("source_path"));
                    if (c.S("source_sha256") != J.Hash(script)) throw new FlowException("Source hash must match exact submitted UTF-8 script bytes.");
                    item["source_path"] = c.S("source_path"); item["source_sha256"] = c.S("source_sha256");
                }
                clean.Add(item);
            }
            foreach (var dep in args.Strings("dependencies")) Revision(dep);
            var data = J.Obj(("demand_id", demand), ("title", title), ("parent_revision", parent == "" ? null : parent), ("changes", Order(clean)), ("dependencies", args.A("dependencies")));
            Limit(data, 4000000); data["revision_id"] = "r-" + J.Hash(data)[..24]; Event("revision", data, op);
            Store.Acknowledge(clean, data.S("revision_id"));
            var artifacts = J.Arr(clean.Select(c => J.Obj(("id", c.S("id")), ("source_path", c.S("source_path")), ("source_sha256", c.S("source_sha256")))));
            return J.Obj(("revision_id", data.S("revision_id")), ("demand_id", demand), ("artifacts", artifacts), ("application", "not_implied_by_registration"));
        });
    }
    public static JsonArray Order(JsonArray changes)
    {
        var byId = changes.ToDictionary(c => c.S("id")); var done = new HashSet<string>(); var visiting = new HashSet<string>(); var result = new JsonArray();
        void Visit(string id)
        {
            if (!byId.TryGetValue(id, out var c)) throw new FlowException("Unknown change dependency.");
            if (done.Contains(id)) return;
            if (!visiting.Add(id)) throw new FlowException("Cyclic change dependencies.");
            foreach (var dep in c.Strings("depends_on")) Visit(dep);
            visiting.Remove(id); done.Add(id); result.Add(c?.DeepClone());
        }
        foreach (var id in byId.Keys) Visit(id); return result;
    }
    JsonObject Bundle(string id, bool allowCancelled = false)
    {
        if (!allowCancelled && Cancelled.Contains(id)) throw new FlowException("Bundle cancelled; history is preserved.");
        return Events("bundle").Select(Data).FirstOrDefault(b => b.S("bundle_id") == id) ?? throw new FlowException("Unknown bundle.");
    }
    JsonObject Files(JsonObject bundle)
    {
        var files = new JsonObject(); var manifest = (JsonObject)bundle.DeepClone(); var scripts = new JsonArray();
        foreach (var rid in bundle.Strings("revision_ids"))
        {
            int i = 0;
            foreach (var c in Revision(rid).A("changes"))
            {
                var rel = $"{J.Id(rid)}/{J.Id(c.S("database"))}/{++i:000}-{J.Id(c.S("filename"))}";
                files[rel] = c.S("script"); scripts.Add(J.Obj(("path", rel), ("sha256", c.S("script_sha256")), ("change_id", c.S("id")), ("revision_id", rid), ("database", c.S("database")), ("verification", c.S("verification"))));
            }
        }
        manifest["scripts"] = scripts; files["manifest.json"] = J.Canon(manifest);
        files["email.txt"] = $"Assunto: Aplicação de mudanças de banco — {bundle.S("bundle_id")} — {bundle.S("environment")}\n\nSolicito aplicação deste pacote respeitando a ordem do manifest.json.\nInformar resultado por banco e etapa, incluindo falhas e execução parcial.\nEste rascunho não registra envio nem aplicação.\n";
        return files;
    }
    public JsonObject Prepare(JsonObject args)
    {
        Sync();
        var result = Mutate("bundle", args, op =>
        {
            var ids = args.Strings("revision_ids"); var env = J.Id(args.S("environment"));
            if (ids.Count == 0 || ids.Distinct().Count() != ids.Count) throw new FlowException("Select explicit distinct revision_ids.");
            var revisions = ids.Select(Revision).ToList();
            if (ids.Any(Abandoned.Contains)) throw new FlowException("Abandoned revision cannot be delivered.");
            if (revisions.Select(r => r.S("demand_id")).Distinct().Count() != ids.Count) throw new FlowException("Select one revision per demand.");
            var objects = new HashSet<string>(); var targets = new JsonObject();
            foreach (var r in revisions)
            {
                foreach (var dep in r.Strings("dependencies")) if (!ids.Contains(dep) && !Confirmed(dep, env)) throw new FlowException("Dependency neither selected nor confirmed in destination.");
                foreach (var c in r.A("changes"))
                {
                    targets[c.S("database")] = Destination(c.S("database"), env);
                    if (!objects.Add(c.S("database") + "\0" + c.S("object"))) throw new FlowException("Overlapping objects; reconcile into an explicit tested revision.");
                }
            }
            var ordered = new List<string>(); var visiting = new HashSet<string>();
            void Add(string id)
            {
                if (ordered.Contains(id)) return; if (!visiting.Add(id)) throw new FlowException("Cyclic revision dependency.");
                foreach (var dep in Revision(id).Strings("dependencies").Where(ids.Contains)) Add(dep);
                visiting.Remove(id); ordered.Add(id);
            }
            foreach (var id in ids) Add(id);
            var data = J.Obj(("revision_ids", ordered), ("environment", env), ("targets", targets)); data["bundle_id"] = "b-" + J.Hash(data)[..24];
            Limit(J.Obj(("bundle", data), ("bundle_files", Files(data))), 4000000);
            Event("bundle", data, op); var output = (JsonObject)data.DeepClone(); output["dispatch"] = "not_sent"; output["application"] = "pending"; return output;
        });
        result["bundle_files"] = Files(Bundle(result.S("bundle_id"))); return result;
    }
    public JsonObject GetRevision(JsonObject args) { var sync = Sync(); var rid = args.S("revision_id"); return J.Obj(("revision", Revision(rid)), ("sync", sync), ("abandoned", Abandoned.Contains(rid))); }
    public JsonObject GetBundle(JsonObject args)
    {
        var sync = Sync(); var id = args.S("bundle_id"); var bundle = Bundle(id, true); var cancelled = Cancelled.Contains(id);
        var result = J.Obj(("bundle", bundle), ("sync", sync), ("cancelled", cancelled), ("dispatch_reports", J.Arr(Events("dispatch_report").Where(e => e.O("data").S("bundle_id") == id))));
        if (!cancelled) { result["bundle_id"] = id; result["bundle_files"] = Files(bundle); } Limit(result, 4000000); return result;
    }
    public JsonObject Dispatch(JsonObject args) => Mutate("dispatch_report", args, op =>
    {
        var bundle = Bundle(args.S("bundle_id")); var data = J.Obj(("bundle_id", bundle.S("bundle_id")), ("reference", J.Text(args.S("reference"), "reference", 2000)), ("reported_sent_at", J.Text(args.S("sent_at"), "sent_at", 100)));
        Event("dispatch_report", data, op); data["application"] = "pending"; data["source"] = "user_report"; return data;
    });
    public JsonObject Confirm(JsonObject args) => Mutate("confirmation", args, op =>
    {
        var rid = args.S("revision_id"); var rev = Revision(rid); var db = args.S("database"); var env = args.S("environment"); var dest = Destination(db, env);
        if (args.S("bundle_id") != "")
        {
            var bundle = Bundle(args.S("bundle_id"));
            if (!bundle.Strings("revision_ids").Contains(rid) || bundle.S("environment") != env || bundle.O("targets")[db] == null) throw new FlowException("Confirmation does not belong to the selected bundle.");
            dest = bundle.O("targets").O(db);
        }
        if (args.S("target_fingerprint") != dest.S("fingerprint")) throw new FlowException("Destination fingerprint differs; identify exact destination/bundle.");
        var ids = args.Strings("change_ids"); var known = rev.A("changes").Where(c => c.S("database") == db).Select(c => c.S("id")).ToHashSet();
        if (ids.Count == 0 || ids.Any(id => !known.Contains(id))) throw new FlowException("Select change_ids belonging to exact revision/database.");
        var kind = args.S("kind"); if (kind is not ("manual_applied" or "execution_succeeded" or "state_matches" or "state_differs" or "failed")) throw new FlowException("Unknown confirmation kind.");
        var data = J.Obj(("revision_id", rid), ("database", db), ("environment", env), ("change_ids", ids.Distinct().Order().ToArray()), ("kind", kind), ("evidence", J.Text(args.S("evidence"), "evidence", 20000)), ("reported_by", actor), ("target", dest), ("observed_at", J.Text(args.S("observed_at"), "observed_at", 100)));
        Event("confirmation", data, op); data["scope"] = "only_the_exact_revision_and_selected_changes"; return data;
    });
    public JsonArray State(string rid, string environment)
    {
        var states = new JsonArray();
        foreach (var c in Revision(rid).A("changes"))
        {
            var db = c.S("database"); var execution = "pending"; var observation = "not_checked"; JsonObject? dest = null;
            if (Config.O("databases").O(db).O("environments")[environment] == null) execution = "destination_not_mapped";
            else
            {
                dest = Destination(db, environment);
                foreach (var ev in Events("confirmation"))
                {
                    var d = ev.O("data");
                    if (d.S("revision_id") != rid || d.S("database") != db || d.S("environment") != environment || d.O("target").S("fingerprint") != dest.S("fingerprint") || !d.Strings("change_ids").Contains(c.S("id"))) continue;
                    if (d.S("kind") is "manual_applied" or "execution_succeeded" or "failed") execution = d.S("kind"); else observation = d.S("kind");
                }
            }
            states.Add(J.Obj(("change_id", c.S("id")), ("database", db), ("environment", environment), ("target", dest), ("execution", execution), ("observation", observation)));
        }
        return states;
    }
    bool Confirmed(string rid, string env) => State(rid, env).All(s => (s.S("execution") is "manual_applied" or "execution_succeeded" || s.S("observation") == "state_matches") && s.S("execution") != "failed" && s.S("observation") != "state_differs");
    public JsonObject Snapshot(JsonObject args) => Mutate("snapshot", args, op =>
    {
        var db = args.S("database"); var env = args.S("environment"); var dest = Destination(db, env);
        if (args.S("target_fingerprint") != dest.S("fingerprint")) throw new FlowException("Snapshot destination differs.");
        var objects = args["objects"] as JsonObject ?? throw new FlowException("objects must map identities to definitions.");
        if (objects.Count > 5000 || objects.Any(o => o.Value is not JsonValue v || !v.TryGetValue<string>(out _))) throw new FlowException("Invalid snapshot definitions.");
        Limit(objects, 8000000); var covered = args.Strings("covered_objects"); var rid = args.S("revision_id");
        var comparisons = new JsonArray();
        if (rid != "") foreach (var c in Revision(rid).A("changes").Where(c => c.S("database") == db))
        {
            var key = c.S("object"); var comparison = c?["after_definition"] == null ? "no_expected_definition" : objects[key] == null ? (covered.Contains(key) ? "missing_in_declared_scope" : "inconclusive") : c.S("after_definition").Replace("\r\n", "\n").Trim() == objects.S(key).Replace("\r\n", "\n").Trim() ? "text_matches" : "text_differs";
            comparisons.Add(J.Obj(("change_id", c.S("id")), ("object", key), ("comparison", comparison)));
        }
        var data = J.Obj(("database", db), ("environment", env), ("objects", objects), ("target", dest), ("covered_objects", covered), ("captured_at", J.Text(args.S("captured_at"), "captured_at", 100)), ("source", J.Text(args.S("source"), "source", 2000)), ("limitations", args.A("limitations")));
        data["snapshot_id"] = "s-" + J.Hash(data)[..24]; Event("snapshot", data, op);
        return J.Obj(("snapshot_id", data.S("snapshot_id")), ("comparison_basis", rid == "" ? "baseline_not_established" : rid), ("comparisons", comparisons), ("application_status_changed", false));
    });
    public JsonObject Abandon(JsonObject args)
    {
        Sync();
        return Mutate("abandonment", args, op =>
        {
            var rid = args.S("revision_id"); Revision(rid);
            var rows = Store.Query("SELECT * FROM events WHERE sync='pending'").Where(r => { var e = JsonNode.Parse(r.S("payload")); return e.S("kind") == "revision" && e.O("data").S("revision_id") == rid; }).ToList();
            if (rows.Count != 1) throw new FlowException("Only pending unpublished revisions can be abandoned.");
            if (Revisions().Any(r => r.Value.S("parent_revision") == rid && !Abandoned.Contains(r.Key))) throw new FlowException("Resolve descendant revisions first.");
            if (Configured)
            {
                using var git = gitlabFactory?.Invoke(Config) ?? new GitLab(Config);
                if (git.File(git.Prefix + "/events/" + rows[0].S("id") + ".json") != null) throw new FlowException("Revision was published; synchronize before reconciling.");
            }
            var bundles = Events("bundle").Select(Data).Where(b => b.Strings("revision_ids").Contains(rid)).Select(b => b.S("bundle_id")).ToList();
            if (Events("dispatch_report").Any(e => bundles.Contains(e.O("data").S("bundle_id"))) || Events("confirmation").Any(e => e.O("data").S("revision_id") == rid)) throw new FlowException("Revision has dispatch/application evidence; preserve it and reconcile in a new demand.");
            var reason = J.Text(args.S("reason"), "reason", 4000);
            Store.Execute("UPDATE events SET sync='abandoned',error=$p0 WHERE id=$p1", reason, rows[0].S("id"));
            Event("abandonment", J.Obj(("revision_id", rid), ("reason", reason), ("cancelled_undispatched_bundles", bundles), ("preserved_local_event", JsonNode.Parse(rows[0].S("payload")))), op);
            return J.Obj(("revision_id", rid), ("disposition", "abandoned_locally"), ("history", "preserved"));
        });
    }
    bool Configured => Config.O("gitlab").S("url") != "" && Config.O("gitlab").S("project_id") != "";
    public JsonObject Sync()
    {
        if (!Configured) return J.Obj(("state", "not_configured"), ("pending", Store.Pending));
        try
        {
            using var git = gitlabFactory?.Invoke(Config) ?? new GitLab(Config);
            var incoming = git.Pull(); foreach (var (ev, _) in incoming) ValidateEvent(ev);
            foreach (var (ev, commit) in incoming) Store.Add(ev, "synced", commit);
            foreach (var row in Store.Query("SELECT id,payload FROM events WHERE sync='pending' ORDER BY created,id"))
            {
                try { var commit = git.Publish(JsonNode.Parse(row.S("payload"))!.AsObject()); Store.Execute("UPDATE events SET sync='synced',remote_commit=$p0,error=NULL WHERE id=$p1", commit, row.S("id")); }
                catch (FlowException ex) { Store.Execute("UPDATE events SET error=$p0 WHERE id=$p1", ex.Message, row.S("id")); throw; }
            }
            return J.Obj(("state", "synced"), ("pending", Store.Pending));
        }
        catch (Exception ex) when (ex is FlowException or System.Text.Json.JsonException or FormatException) { return J.Obj(("state", "pending"), ("pending", Store.Pending), ("reason", ex is FlowException ? ex.Message : "Invalid remote ledger.")); }
    }
    void ValidateEvent(JsonObject ev, int depth = 0)
    {
        if (depth > 1 || ev.S("project_key") != Config.S("project_key") || ev["schema_version"]?.GetValue<int>() != 1) throw new FlowException("Incompatible remote event project/schema.");
        J.Id(ev.S("id")); J.Text(ev.S("created_at"), "timestamp", 100); var d = ev.O("data");
        switch (ev.S("kind"))
        {
            case "revision":
                J.Id(d.S("revision_id")); J.Id(d.S("demand_id")); if (d.A("changes").Count == 0) throw new FlowException("Empty revision.");
                foreach (var c in d.A("changes")) { J.Id(c.S("id")); J.Id(c.S("database")); J.Id(c.S("filename")); if (J.Hash(c.S("script")) != c.S("script_sha256")) throw new FlowException("Remote script hash differs."); }
                Order(d.A("changes")); break;
            case "bundle": J.Id(d.S("bundle_id")); J.Id(d.S("environment")); foreach (var rid in d.Strings("revision_ids")) J.Id(rid); break;
            case "snapshot": case "confirmation": J.Id(d.S("database")); J.Id(d.S("environment")); break;
            case "dispatch_report": J.Id(d.S("bundle_id")); break;
            case "abandonment": J.Id(d.S("revision_id")); ValidateEvent(d.O("preserved_local_event"), depth + 1); break;
            default: throw new FlowException("Unknown remote event kind.");
        }
    }
    public JsonObject Status(JsonObject args, bool sync = true)
    {
        int offset = args["offset"]?.GetValue<int>() ?? 0, limit = args["limit"]?.GetValue<int>() ?? 50;
        if (offset < 0 || limit is < 1 or > 200) throw new FlowException("Use offset>=0 and limit 1..200.");
        var synced = sync ? Sync() : J.Obj(("state", "not_checked"), ("pending", Store.Pending)); var revisions = Revisions(); var heads = Heads();
        var overlaps = new Dictionary<string, List<JsonObject>>();
        foreach (var (demand, ids) in heads) foreach (var rid in ids) foreach (var c in revisions[rid].A("changes"))
        {
            var key = c.S("database") + ":" + c.S("object"); if (!overlaps.ContainsKey(key)) overlaps[key] = [];
            overlaps[key].Add(J.Obj(("demand", demand), ("revision", rid)));
        }
        var destinations = new JsonObject();
        foreach (var (db, cfg) in Config.O("databases")) { var envs = new JsonObject(); foreach (var env in cfg.O("environments")) envs[env.Key] = Destination(db, env.Key); destinations[db] = envs; }
        var selected = revisions.Where(r => args.S("demand_id") == "" || r.Value.S("demand_id") == args.S("demand_id")).ToList(); var page = new JsonArray();
        foreach (var (rid, rev) in selected.Skip(offset).Take(limit))
        {
            var targets = new JsonObject(); foreach (var env in rev.A("changes").SelectMany(c => Config.O("databases").O(c.S("database")).O("environments").Select(x => x.Key)).Distinct().Order()) targets[env] = State(rid, env);
            page.Add(J.Obj(("revision_id", rid), ("demand_id", rev.S("demand_id")), ("title", rev.S("title")), ("targets", targets)));
        }
        var bundles = J.Arr(Events("bundle").Select(e => { var d = (JsonObject)e.O("data").DeepClone(); d["cancelled"] = Cancelled.Contains(d.S("bundle_id")); return d; }));
        return J.Obj(("project_key", Config.S("project_key")), ("registry", J.Obj(("url", Config.O("gitlab").S("url")), ("project_id", Config.O("gitlab").S("project_id")), ("branch", Config.O("gitlab").S("branch", "main")))), ("sync", synced), ("heads", heads), ("destinations", destinations), ("total_revisions", selected.Count), ("next_offset", offset + limit < selected.Count ? offset + limit : null), ("revisions", page), ("bundles", bundles), ("dispatch_reports", J.Arr(Events("dispatch_report").Select(Data))), ("abandoned_revisions", Abandoned), ("overlaps", overlaps.Where(k => k.Value.Count > 1).ToDictionary()), ("coverage", "Only supplied artifacts/evidence. No business database connection or email monitoring."));
    }
    public static void Limit(JsonNode value, int max) { if (Encoding.UTF8.GetByteCount(J.Canon(value)) > max) throw new FlowException("Payload too large; split explicit dependent deliveries."); }
    public void Dispose() => Store.Dispose();
}
