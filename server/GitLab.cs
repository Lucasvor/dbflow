using System.Net;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json.Nodes;

namespace DbFlow;

public sealed class GitLab : IDisposable
{
    readonly JsonObject config;
    readonly HttpClient http;
    readonly string endpoint, branch;
    public GitLab(JsonObject config, HttpMessageHandler? handler = null)
    {
        this.config = config; var cfg = config.O("gitlab");
        var url = cfg.S("url").TrimEnd('/');
        if (!Uri.TryCreate(url, UriKind.Absolute, out var uri) || uri.Scheme != "https" || uri.UserInfo != "" || uri.Query != "" || uri.Fragment != "") throw new FlowException("GitLab requires a final HTTPS URL without embedded credentials.");
        endpoint = url + "/api/v4/projects/" + Uri.EscapeDataString(J.Text(cfg.S("project_id"), "GitLab project")); branch = cfg.S("branch", "main");
        http = new HttpClient(handler ?? new HttpClientHandler { AllowAutoRedirect = false }, disposeHandler: handler == null) { Timeout = TimeSpan.FromSeconds(20), MaxResponseContentBufferSize = 16000000 };
        var token = Credentials.Get(config, cfg.S("token_env", "DBFLOW_GITLAB_TOKEN"));
        if (token == "") throw new FlowException("GitLab credential is unavailable; synchronization remains pending.");
        if (cfg.S("auth_type", "private_token") == "oauth_access_token") http.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);
        else http.DefaultRequestHeaders.Add("PRIVATE-TOKEN", token);
    }
    public JsonNode? Request(string method, string suffix, JsonObject? body = null, bool missing = false)
    {
        try
        {
            using var req = new HttpRequestMessage(new HttpMethod(method), endpoint + suffix);
            if (body != null) req.Content = new StringContent(J.Canon(body), Encoding.UTF8, "application/json");
            using var res = http.Send(req);
            if (missing && res.StatusCode == HttpStatusCode.NotFound) return null;
            if (!res.IsSuccessStatusCode) throw new FlowException($"GitLab returned HTTP {(int)res.StatusCode}; check credentials, branch and concurrent updates.");
            var text = res.Content.ReadAsStringAsync().GetAwaiter().GetResult();
            if (Encoding.UTF8.GetByteCount(text) > 16000000) throw new FlowException("GitLab response too large.");
            return JsonNode.Parse(text);
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or IOException) { throw new FlowException("GitLab unavailable; check network, TLS and configuration."); }
    }
    public JsonObject? File(string path) => Request("GET", "/repository/files/" + Uri.EscapeDataString(path) + "?ref=" + Uri.EscapeDataString(branch), missing: true) as JsonObject;
    public string Prefix => "dbflow/projects/" + J.Id(config.S("project_key"));
    static JsonObject Decode(JsonObject file) => JsonNode.Parse(Encoding.UTF8.GetString(Convert.FromBase64String(file.S("content"))))!.AsObject();
    public string Publish(JsonObject ev)
    {
        var path = Prefix + "/events/" + J.Id(ev.S("id")) + ".json"; var existing = File(path);
        if (existing != null)
        {
            if (J.Canon(Decode(existing)) != J.Canon(ev)) throw new FlowException("Remote event collision; refusing overwrite.");
            return existing.S("last_commit_id");
        }
        var actions = new JsonArray(J.Obj(("action", "create"), ("file_path", path), ("content", J.Canon(ev))));
        if (ev.S("kind") == "revision")
        {
            var data = ev.O("data"); var pointer = Prefix + "/demands/" + J.Id(data.S("demand_id")) + "/head.json"; var head = File(pointer);
            if ((head == null ? "" : Decode(head).S("revision_id")) != data.S("parent_revision")) throw new FlowException("Concurrent demand revision: reconcile the remote head explicitly.");
            var action = J.Obj(("action", head == null ? "create" : "update"), ("file_path", pointer), ("content", J.Canon(J.Obj(("revision_id", data.S("revision_id"))))));
            if (head != null) action["last_commit_id"] = head.S("last_commit_id");
            actions.Add(action);
        }
        return Request("POST", "/repository/commits", J.Obj(("branch", branch), ("commit_message", $"dbflow: {ev.S("kind")} {ev.S("id")}"), ("actions", actions)))!.S("id");
    }
    public List<(JsonObject Event, string Commit)> Pull()
    {
        var result = new List<(JsonObject, string)>();
        for (int page = 1; page <= 100; page++)
        {
            var entries = Request("GET", $"/repository/tree?path={Uri.EscapeDataString(Prefix + "/events")}&ref={Uri.EscapeDataString(branch)}&per_page=100&page={page}", missing: true) as JsonArray ?? new();
            foreach (var item in entries.Where(i => i.S("type") == "blob" && i.S("path").EndsWith(".json", StringComparison.Ordinal)))
            {
                var path = item.S("path");
                if (!path.StartsWith(Prefix + "/events/", StringComparison.Ordinal)) throw new FlowException("Invalid ledger path.");
                var file = File(path) ?? throw new FlowException("Ledger file disappeared during sync.");
                result.Add((Decode(file), file.S("last_commit_id")));
            }
            if (entries.Count < 100) return result;
        }
        throw new FlowException("Ledger exceeds 10000 events; incremental indexing is required.");
    }
    public void Dispose() => http.Dispose();
}
