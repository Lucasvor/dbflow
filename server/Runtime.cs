using System.Collections.Concurrent;
using System.Text.Json;
using System.Text.Json.Nodes;
using ModelContextProtocol.Protocol;

namespace DbFlow;

public static class Runtime
{
    static readonly ConcurrentDictionary<string, object> Gates = new();
    public static readonly JsonArray Definitions = JsonNode.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "tools.json")))!.AsArray();
    public static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);
    public static ListToolsResult List(bool local) => new() { Tools = Definitions.Where(t => local || !LocalOnly.Contains(t.S("name"))).Select(t => JsonSerializer.Deserialize<Tool>(t!.ToJsonString(), JsonOptions)!).ToList() };
    static readonly HashSet<string> LocalOnly = ["dbflow_scan", "dbflow_exclude_candidate", "dbflow_cancel_remote_request"];
    public static JsonObject Envelope(JsonObject value, bool error = false)
    {
        var result = J.Obj(("content", new JsonArray(J.Obj(("type", "text"), ("text", J.Canon(value))))), ("structuredContent", value), ("isError", error));
        // Bound the escaped wire representation used by the SDK, not only raw UTF-8 text.
        if (JsonSerializer.SerializeToUtf8Bytes(result, JsonOptions).Length > 9000000) result["content"] = new JsonArray(J.Obj(("type", "text"), ("text", "Complete result is in structuredContent.")));
        if (JsonSerializer.SerializeToUtf8Bytes(result, JsonOptions).Length > 9500000) throw new FlowException("Result exceeds MCP transfer limit; narrow the scope.");
        return result;
    }
    public static JsonObject Call(string root, JsonObject cfg, string actor, string name, JsonObject args, bool local)
    {
        lock (Gates.GetOrAdd(Path.GetFullPath(root), _ => new object()))
        {
            try
            {
                var definition = Definitions.FirstOrDefault(t => t.S("name") == name) ?? throw new FlowException("Unknown tool.");
                Validate(args, definition.O("inputSchema"));
                if (!local && LocalOnly.Contains(name)) throw new FlowException("This tool runs only in the developer's local client.");
                using var c = new Control(root, cfg, actor);
                if (cfg.S("mode") == "remote" && !LocalOnly.Contains(name)) return Remote.Call(c, name, args);
                var result = name switch
                {
                    "dbflow_status" => c.Status(args), "dbflow_get_revision" => c.GetRevision(args), "dbflow_get_bundle" => c.GetBundle(args),
                    "dbflow_register_revision" => c.Register(args), "dbflow_prepare_bundle" => c.Prepare(args), "dbflow_report_dispatch" => c.Dispatch(args),
                    "dbflow_confirm" => c.Confirm(args), "dbflow_record_snapshot" => c.Snapshot(args), "dbflow_abandon_local_revision" => c.Abandon(args),
                    "dbflow_scan" => Scanner.Scan(c), "dbflow_exclude_candidate" => Scanner.Exclude(c, args), "dbflow_cancel_remote_request" => Cancel(c, args),
                    _ => throw new FlowException("Unknown tool.")
                };
                if (local) Scanner.Export(root, result); return Envelope(result);
            }
            catch (FlowException ex) { return Envelope(J.Obj(("error", ex.Message)), true); }
            catch { return Envelope(J.Obj(("error", "Operation failed; inspect configuration and journal. No success is implied.")), true); }
        }
    }
    static JsonObject Cancel(Control c, JsonObject args)
    {
        if (args.S("reason").Trim().Length < 10) throw new FlowException("Cancellation requires a concrete reason.");
        c.Store.Execute("UPDATE requests SET state='cancelled',error=$p0 WHERE id=$p1 AND state IN ('pending','rejected')", args.S("reason"), args.S("operation_id"));
        return J.Obj(("cancelled_retry", args.S("operation_id")), ("server_operation_undone", false));
    }
    static void Validate(JsonNode? value, JsonObject schema)
    {
        var types = schema["type"] is JsonArray a ? a.Select(t => t!.GetValue<string>()).ToArray() : new[] { schema.S("type") };
        var kind = value?.GetValueKind() ?? JsonValueKind.Null;
        bool valid = types.Any(t => t switch { "object" => kind == JsonValueKind.Object, "array" => kind == JsonValueKind.Array, "string" => kind == JsonValueKind.String, "null" => kind == JsonValueKind.Null, "integer" => kind == JsonValueKind.Number && value!.AsValue().TryGetValue<int>(out _), _ => false });
        if (!valid) throw new FlowException("Invalid tool argument type.");
        if (value is JsonObject obj)
        {
            foreach (var key in schema.Strings("required")) if (!obj.ContainsKey(key)) throw new FlowException("Missing required tool argument.");
            foreach (var (key, child) in obj)
            {
                if (schema.O("properties")[key] is JsonObject prop) Validate(child, prop);
                else if (schema["additionalProperties"] is JsonObject extra) Validate(child, extra);
                else if (schema["additionalProperties"]?.GetValueKind() == JsonValueKind.False) throw new FlowException("Unexpected tool argument.");
            }
        }
        if (value is JsonArray array && schema["items"] is JsonObject items) foreach (var item in array) Validate(item, items);
    }
    public static CallToolResult Result(JsonObject result) => JsonSerializer.Deserialize<CallToolResult>(result.ToJsonString(), JsonOptions)!;
}
