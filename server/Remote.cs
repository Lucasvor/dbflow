using System.Net.Http.Headers;
using System.Text;
using System.Text.Json.Nodes;

namespace DbFlow;

public static class Remote
{
    public static readonly HashSet<string> Mutations = ["dbflow_register_revision", "dbflow_prepare_bundle", "dbflow_report_dispatch", "dbflow_confirm", "dbflow_record_snapshot", "dbflow_abandon_local_revision"];
    static JsonObject Request(JsonObject cfg, string name, JsonObject args)
    {
        if (!Uri.TryCreate(cfg.S("remote_url"), UriKind.Absolute, out var uri) || (uri.Scheme != "https" && !(uri.Scheme == "http" && uri.IsLoopback)) || uri.UserInfo != "" || uri.Query != "" || uri.Fragment != "") throw new FlowException("MCP URL requires HTTPS without credentials (HTTP only for loopback tests).");
        var token = Credentials.Get(cfg, cfg.S("remote_token_env", "DBFLOW_SERVICE_TOKEN")); if (token == "") throw new FlowException("MCP access token is unavailable.");
        using var http = new HttpClient(new HttpClientHandler { AllowAutoRedirect = false }) { Timeout = TimeSpan.FromSeconds(120), MaxResponseContentBufferSize = 10000000 };
        http.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);
        http.DefaultRequestHeaders.Accept.ParseAdd("application/json, text/event-stream");
        http.DefaultRequestHeaders.Add("MCP-Protocol-Version", "2025-11-25");
        var id = Guid.NewGuid().ToString("N"); var body = J.Obj(("jsonrpc", "2.0"), ("id", id), ("method", "tools/call"), ("params", J.Obj(("name", name), ("arguments", args))));
        try
        {
            using var req = new HttpRequestMessage(HttpMethod.Post, uri) { Content = new StringContent(J.Canon(body), Encoding.UTF8, "application/json") };
            using var res = http.Send(req);
            if (!res.IsSuccessStatusCode) throw new FlowException($"MCP returned HTTP {(int)res.StatusCode}; request remains pending.");
            var text = res.Content.ReadAsStringAsync().GetAwaiter().GetResult();
            JsonObject? json = null;
            if (res.Content.Headers.ContentType?.MediaType == "text/event-stream")
            {
                foreach (var block in text.Replace("\r\n", "\n").Split("\n\n", StringSplitOptions.RemoveEmptyEntries))
                {
                    var data = string.Join("\n", block.Split('\n').Where(l => l.StartsWith("data:", StringComparison.Ordinal)).Select(l => l[5..].TrimStart(' ')));
                    if (data == "") continue;
                    var message = JsonNode.Parse(data) as JsonObject;
                    if (message.S("id") == id) { json = message; break; }
                }
            }
            else json = JsonNode.Parse(text) as JsonObject;
            if (json == null) throw new FlowException("MCP response did not contain the expected request ID.");
            if (json.S("id") != id || json["error"] != null || json["result"] is not JsonObject result || result["content"] is not JsonArray) throw new FlowException("Incomplete MCP response; request remains pending.");
            return result;
        }
        catch (Exception ex) when (ex is HttpRequestException or IOException or TaskCanceledException or System.Text.Json.JsonException) { throw new FlowException("MCP unavailable or invalid response; request remains pending locally."); }
    }
    static void Verify(JsonObject config)
    {
        var response = Request(config, "dbflow_status", J.Obj(("limit", 1)));
        if (response["isError"]?.GetValue<bool>() == true) throw new FlowException("MCP identity could not be verified.");
        var value = response.O("structuredContent"); var expected = config.O("gitlab"); var actual = value.O("registry");
        if (value.S("project_key") != config.S("project_key") || expected.S("url") == "" || expected.S("project_id") == "" || actual.S("url").TrimEnd('/') != expected.S("url").TrimEnd('/') || actual.S("project_id") != expected.S("project_id") || actual.S("branch", "main") != expected.S("branch", "main")) throw new FlowException("MCP project/repository differs from installer configuration; no queued mutation was sent.");
    }
    public static JsonObject Call(Control c, string name, JsonObject args)
    {
        c.Store.Execute("CREATE TABLE IF NOT EXISTS remote_results(id TEXT PRIMARY KEY,result TEXT NOT NULL)");
        if (Mutations.Contains(name))
        {
            var op = J.Id(args.S("operation_id")); var body = J.Obj(("name", name), ("arguments", args));
            var old = c.Store.Query("SELECT body,state FROM requests WHERE id=$p0", op).FirstOrDefault();
            // Accept the 0.1 journal's full JSON-RPC body during upgrade.
            if (old != null)
            {
                var saved = JsonNode.Parse(old.S("body"))!.AsObject(); if (saved["params"] is JsonObject p) saved = p;
                if (J.Canon(saved) != J.Canon(body)) throw new FlowException("operation_id already queued with other arguments.");
                if (old.S("state") == "cancelled") throw new FlowException("This request was cancelled; its history is preserved.");
            }
            c.Store.Execute("INSERT OR IGNORE INTO requests VALUES($p0,$p1,'pending',NULL)", op, J.Canon(body));
        }
        Verify(c.Config);
        foreach (var row in c.Store.Query("SELECT * FROM requests WHERE state='pending' ORDER BY rowid"))
        {
            var saved = JsonNode.Parse(row.S("body"))!.AsObject(); if (saved["params"] is JsonObject p) saved = p;
            var response = Request(c.Config, saved.S("name"), saved.O("arguments")); var error = response["isError"]?.GetValue<bool>() == true;
            c.Store.Execute("INSERT OR REPLACE INTO remote_results VALUES($p0,$p1)", row.S("id"), J.Canon(response));
            c.Store.Execute("UPDATE requests SET state=$p0,error=$p1 WHERE id=$p2", error ? "rejected" : "done", error ? "Server rejected operation; inspect and reconcile." : null, row.S("id"));
            if (!error) c.Store.Acknowledge(response.O("structuredContent").A("artifacts"), response.O("structuredContent").S("revision_id"));
        }
        JsonObject result;
        var cached = Mutations.Contains(name) ? c.Store.Query("SELECT result FROM remote_results WHERE id=$p0", args.S("operation_id")).FirstOrDefault() : null;
        if (cached != null) result = JsonNode.Parse(cached.S("result"))!.AsObject();
        else result = Request(c.Config, name, args); // Legacy completed requests recover via server idempotency.
        if (result["isError"]?.GetValue<bool>() == true) return result;
        var value = result.O("structuredContent"); Scanner.Export(c.Root, value);
        if (name == "dbflow_status") value["local_remote_outbox"] = J.Arr(c.Store.Query("SELECT id,state,error FROM requests WHERE state IN ('pending','rejected')"));
        return Runtime.Envelope(value);
    }
}
