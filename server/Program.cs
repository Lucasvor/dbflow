using System.Security.Claims;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using DbFlow;
using ModelContextProtocol.Protocol;

Console.InputEncoding = new UTF8Encoding(false);
Console.OutputEncoding = new UTF8Encoding(false);

static string Option(string[] args, string name, string fallback = "") { var i = Array.IndexOf(args, name); return i >= 0 && i + 1 < args.Length ? args[i + 1] : fallback; }
static JsonObject ReadConfig(string path) => JsonNode.Parse(File.ReadAllText(path))!.AsObject();
static JsonObject LoadProject(string root)
{
    var cfg = ReadConfig(J.SafePath(root, ".db-control/project.json")); J.Id(cfg.S("project_key"));
    if (cfg["version"]?.GetValue<int>() != 1) throw new FlowException("Unsupported project configuration.");
    return cfg;
}
try
{
    var root = Path.GetFullPath(Option(args, "--project", "."));
    if (args.Contains("--set-secret")) { Credentials.Save(root, Option(args, "--set-secret"), Console.ReadLine() ?? ""); Console.WriteLine("{\"stored_encrypted\":true}"); return; }
    if (args.Contains("--call") || args.Contains("--stdio"))
    {
        Credentials.Load(root); var cfg = LoadProject(root);
        if (args.Contains("--call"))
        {
            var input = JsonNode.Parse(Console.In.ReadToEnd())!.AsObject();
            Console.WriteLine(J.Canon(Runtime.Call(root, cfg, "local:" + Environment.UserName, Option(args, "--call"), input, true))); return;
        }
        var builder = Host.CreateApplicationBuilder();
        builder.Logging.ClearProviders(); builder.Logging.AddConsole(o => o.LogToStandardErrorThreshold = LogLevel.Trace); builder.Logging.SetMinimumLevel(LogLevel.Warning);
        builder.Services.AddMcpServer(o => o.ServerInfo = new Implementation { Name = "dbflow", Version = "0.2.0" }).WithStdioServerTransport()
            .WithListToolsHandler((_, _) => ValueTask.FromResult(Runtime.List(true)))
            .WithCallToolHandler((context, _) =>
            {
                var p = context.Params!; var input = JsonSerializer.SerializeToNode(p.Arguments ?? new Dictionary<string, JsonElement>())!.AsObject();
                return ValueTask.FromResult(Runtime.Result(Runtime.Call(root, LoadProject(root), "local:" + Environment.UserName, p.Name, input, true)));
            });
        await builder.Build().RunAsync(); return;
    }
    var configPath = Option(args, "--config", Environment.GetEnvironmentVariable("DBFLOW_CONFIG") ?? "server-config.json");
    var serverConfig = ReadConfig(configPath); var projects = serverConfig.O("projects");
    if (projects.Count == 0) throw new FlowException("Server requires at least one allowlisted project.");
    var dataRoot = Path.GetFullPath(Environment.GetEnvironmentVariable("DBFLOW_DATA") ?? "data");
    var tokens = JsonNode.Parse(Environment.GetEnvironmentVariable("DBFLOW_SERVICE_TOKENS") ?? "{}")!.AsObject();
    if (tokens.Count == 0) throw new FlowException("Configure DBFLOW_SERVICE_TOKENS via a secret provider.");
    foreach (var (key, cfg) in projects)
    {
        J.Id(key); if (cfg.S("project_key") != key || cfg.S("mode") != "local") throw new FlowException("Each server project must have a matching project_key and local mode.");
        using var store = new Store(J.SafePath(dataRoot, key));
    }
    foreach (var (token, identity) in tokens)
        if (token.Length < 32 || identity.S("actor") == "" || identity.Strings("projects").Count == 0 || identity.Strings("projects").Any(p => projects[p] == null)) throw new FlowException("Service tokens need 32+ characters, an actor and explicit allowed projects.");
    var web = WebApplication.CreateBuilder(args);
    web.Logging.ClearProviders(); web.Logging.AddConsole(); web.Logging.SetMinimumLevel(LogLevel.Warning);
    web.WebHost.ConfigureKestrel(o => o.Limits.MaxRequestBodySize = 10000000);
    web.Services.AddMcpServer(o => o.ServerInfo = new Implementation { Name = "dbflow", Version = "0.2.0" }).WithHttpTransport(o => o.Stateless = true)
        .WithListToolsHandler((_, _) => ValueTask.FromResult(Runtime.List(false)))
        .WithCallToolHandler((context, _) =>
        {
            var key = context.User?.FindFirst("dbflow_project")?.Value ?? ""; var actor = context.User?.Identity?.Name ?? "";
            if (projects[key] is not JsonObject cfg || actor == "") throw new FlowException("Missing authorized project scope.");
            var p = context.Params!; var input = JsonSerializer.SerializeToNode(p.Arguments ?? new Dictionary<string, JsonElement>())!.AsObject();
            return ValueTask.FromResult(Runtime.Result(Runtime.Call(J.SafePath(dataRoot, key), cfg, "service:" + actor, p.Name, input, false)));
        });
    var app = web.Build();
    app.Use(async (ctx, next) =>
    {
        if (ctx.Request.Path.StartsWithSegments("/health")) { await next(ctx); return; }
        var allowedHosts = serverConfig.Strings("allowed_hosts");
        if (allowedHosts.Count == 0 || !allowedHosts.Contains(ctx.Request.Host.Host, StringComparer.OrdinalIgnoreCase)) { ctx.Response.StatusCode = 400; return; }
        if (ctx.Request.Headers.TryGetValue("Origin", out var origin) && !serverConfig.Strings("allowed_origins").Contains(origin.ToString(), StringComparer.Ordinal)) { ctx.Response.StatusCode = 403; return; }
        var segments = ctx.Request.Path.Value?.Split('/', StringSplitOptions.RemoveEmptyEntries) ?? [];
        if (segments.Length != 2 || segments[0] != "mcp" || projects[segments[1]] == null) { ctx.Response.StatusCode = 404; return; }
        var authorization = ctx.Request.Headers.Authorization.ToString(); var token = authorization.StartsWith("Bearer ", StringComparison.Ordinal) ? authorization[7..] : "";
        JsonNode? identity = null;
        foreach (var entry in tokens) if (CryptographicOperations.FixedTimeEquals(SHA256.HashData(Encoding.UTF8.GetBytes(token)), SHA256.HashData(Encoding.UTF8.GetBytes(entry.Key)))) identity = entry.Value;
        if (identity == null) { ctx.Response.StatusCode = 401; ctx.Response.Headers.WWWAuthenticate = "Bearer"; return; }
        if (!identity.Strings("projects").Contains(segments[1], StringComparer.Ordinal)) { ctx.Response.StatusCode = 403; return; }
        ctx.User = new ClaimsPrincipal(new ClaimsIdentity([new Claim(ClaimTypes.Name, identity.S("actor")), new Claim("dbflow_project", segments[1])], "DbFlowToken"));
        await next(ctx);
    });
    app.MapGet("/health/live", () => Results.Ok(new { status = "live" }));
    app.MapGet("/health/ready", () =>
    {
        try { foreach (var (key, _) in projects) { using var s = new Store(J.SafePath(dataRoot, key)); s.Execute("INSERT OR REPLACE INTO checks VALUES('readiness',$p0)", J.Now()); } return Results.Ok(new { status = "ready" }); }
        catch { return Results.StatusCode(503); }
    });
    app.MapMcp("/mcp/{projectKey}");
    await app.RunAsync();
}
catch (FlowException ex) { Console.Error.WriteLine(ex.Message); Environment.ExitCode = 1; }
catch { Console.Error.WriteLine("DBFlow startup failed. Check configuration, secret provider and writable storage."); Environment.ExitCode = 1; }
