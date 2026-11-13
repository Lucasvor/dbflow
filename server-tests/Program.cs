using System.Net;
using System.Text;
using System.Text.Json.Nodes;
using DbFlow;

var root = Path.Combine(Path.GetTempPath(), "dbflow-native-gitlab-" + Guid.NewGuid().ToString("N"));
Directory.CreateDirectory(root);
try
{
    var cfg = JsonNode.Parse("""
    {"version":1,"project_key":"test","mode":"local","gitlab":{"url":"https://gitlab.example.invalid","project_id":"team/scripts","branch":"main","token_env":"DBFLOW_TEST_TOKEN"},"databases":{"PRO":{"engine":"sqlserver","environments":{"HML":{"instance_alias":"fake-hml","database":"PRO"},"PRD":{"instance_alias":"fake-prd","database":"PRO"}}}}}
    """)!.AsObject();
    Environment.SetEnvironmentVariable("DBFLOW_TEST_TOKEN", "synthetic-test-value");
    using var handler = new FakeGitLab();
    GitLab Factory(JsonObject c) => new(c, handler);
    JsonObject Args(string op, string? parent = null, string script = "ALTER TABLE dbo.T ADD C INT;") => J.Obj(("operation_id", op), ("demand_id", "D1"), ("title", "Test"), ("parent_revision", parent), ("changes", new JsonArray(J.Obj(("id", "c1"), ("database", "PRO"), ("object", "dbo.T"), ("kind", "alter"), ("filename", "001.sql"), ("script", script), ("verification", "Inspect column definition")))));
    var results = new List<string>();
    void Check(bool ok, string scenario) { if (!ok) throw new Exception(scenario); results.Add(scenario); }
    var firstArgs = Args("r1"); string rid;
    using (var a = new Control(Path.Combine(root, "a"), cfg, "test:a", Factory))
    {
        handler.LoseNextCommitResponse = true;
        var first = a.Register(firstArgs); rid = first.S("revision_id");
        Check(first.O("sync").S("state") == "pending" && a.Store.Pending == 1, "lost response leaves durable pending record");
    }
    using (var a = new Control(Path.Combine(root, "a"), cfg, "test:a", Factory))
    {
        var retried = a.Register(firstArgs);
        Check(retried.S("revision_id") == rid && a.Store.Pending == 0 && handler.CommitCount == 1, "restart and replay recover committed event without duplication");
        Check(handler.LastAuthorization == "PRIVATE-TOKEN", "PAT/project token uses PRIVATE-TOKEN header");
        using var b = new Control(Path.Combine(root, "b"), cfg, "test:b", Factory);
        b.Sync(); handler.Offline = true;
        var ra = a.Register(Args("r2-a", rid, "ALTER TABLE dbo.T ADD A INT;"));
        var rb = b.Register(Args("r2-b", rid, "ALTER TABLE dbo.T ADD B INT;"));
        handler.Offline = false; a.Sync();
        Check(b.Sync().S("state") == "pending" && b.Store.Pending == 1, "concurrent demand head conflict preserves unpublished event");
        b.Abandon(J.Obj(("operation_id", "abandon"), ("revision_id", rb.S("revision_id")), ("reason", "Reconcile competing revision")));
        Check(b.Store.Pending == 0 && b.GetRevision(J.Obj(("revision_id", rb.S("revision_id"))))["abandoned"]!.GetValue<bool>(), "abandonment preserves content and reconciles pending conflict");
        var status = b.Status(new()); Check(status["total_revisions"]!.GetValue<int>() == 3, "both competing revisions remain auditable");
        // Simulate a legacy ledger with two events for the same content-addressed revision.
        var duplicate = (JsonObject)a.Store.Events("revision")[0].DeepClone(); duplicate["id"] = "legacy-duplicate";
        a.Store.Add(duplicate, "synced", "legacy"); Check(a.Revisions().ContainsKey(rid), "duplicate content revision IDs do not break migrated journals");
        cfg.O("gitlab")["auth_type"] = "oauth_access_token";
        a.Sync(); Check(handler.LastAuthorization == "Bearer", "externally supplied OAuth access token uses Bearer header");
    }
    Console.WriteLine(J.Canon(J.Obj(("passed", results.Count), ("checks", results), ("real_network_requests", 0))));
}
finally { Directory.Delete(root, true); Environment.SetEnvironmentVariable("DBFLOW_TEST_TOKEN", null); }

sealed class FakeGitLab : HttpMessageHandler
{
    readonly Dictionary<string, (string Content, string Commit)> files = new();
    public bool Offline, LoseNextCommitResponse;
    public int CommitCount;
    public string LastAuthorization = "";
    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct) => Task.FromResult(Send(request, ct));
    protected override HttpResponseMessage Send(HttpRequestMessage req, CancellationToken ct)
    {
        if (Offline) throw new HttpRequestException("Simulated outage");
        LastAuthorization = req.Headers.Contains("PRIVATE-TOKEN") ? "PRIVATE-TOKEN" : req.Headers.Authorization?.Scheme ?? "";
        var path = req.RequestUri!.AbsolutePath;
        var prefix = "/repository/files/";
        if (path.Contains(prefix))
        {
            var key = Uri.UnescapeDataString(path[(path.IndexOf(prefix, StringComparison.Ordinal) + prefix.Length)..]);
            if (!files.TryGetValue(key, out var file)) return Reply(404, new JsonObject());
            return Reply(200, J.Obj(("content", Convert.ToBase64String(Encoding.UTF8.GetBytes(file.Content))), ("last_commit_id", file.Commit)));
        }
        if (path.EndsWith("/repository/tree", StringComparison.Ordinal)) return Reply(200, J.Arr(files.Keys.Where(k => k.Contains("/events/", StringComparison.Ordinal)).Select(k => J.Obj(("type", "blob"), ("path", k)))));
        if (path.EndsWith("/repository/commits", StringComparison.Ordinal))
        {
            var body = JsonNode.Parse(req.Content!.ReadAsStringAsync(ct).GetAwaiter().GetResult())!;
            foreach (var action in body.A("actions"))
            {
                var key = action.S("file_path"); var exists = files.TryGetValue(key, out var old);
                if (action.S("action") == "create" ? exists : !exists || old.Commit != action.S("last_commit_id")) return Reply(400, new JsonObject());
            }
            var commit = "commit-" + ++CommitCount;
            foreach (var action in body.A("actions")) files[action.S("file_path")] = (action.S("content"), commit);
            if (LoseNextCommitResponse) { LoseNextCommitResponse = false; throw new HttpRequestException("Simulated lost response after commit"); }
            return Reply(201, J.Obj(("id", commit)));
        }
        return Reply(404, new JsonObject());
    }
    static HttpResponseMessage Reply(int status, JsonNode body) => new((HttpStatusCode)status) { Content = new StringContent(J.Canon(body), Encoding.UTF8, "application/json") };
}
