using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace DbFlow;

public static class Credentials
{
    public static string Get(JsonObject config, string name) => Environment.GetEnvironmentVariable(name) ?? "";
    public static void Load(string root)
    {
        var path = J.SafePath(root, ".db-control/local/credentials.dpapi.json");
        if (!File.Exists(path)) return;
        if (!OperatingSystem.IsWindows()) throw new FlowException("DPAPI credentials require the original Windows account. Use environment variables on other platforms.");
        foreach (var (name, value) in JsonNode.Parse(File.ReadAllText(path))!.AsObject())
            if (string.IsNullOrEmpty(Environment.GetEnvironmentVariable(name))) Environment.SetEnvironmentVariable(name, Encoding.UTF8.GetString(ProtectedData.Unprotect(Convert.FromBase64String(value!.GetValue<string>()), null, DataProtectionScope.CurrentUser)));
    }
    public static void Save(string root, string name, string value)
    {
        if (!OperatingSystem.IsWindows()) throw new FlowException("Local encrypted storage supports Windows DPAPI; use an environment/secret provider on Linux.");
        if (!Regex.IsMatch(name, "^DBFLOW_[A-Z0-9_]+$")) throw new FlowException("Use a DBFLOW_ credential name.");
        J.Text(value, "credential", 20000);
        var path = J.SafePath(root, ".db-control/local/credentials.dpapi.json");
        var data = File.Exists(path) ? JsonNode.Parse(File.ReadAllText(path))!.AsObject() : new JsonObject();
        data[name] = Convert.ToBase64String(ProtectedData.Protect(Encoding.UTF8.GetBytes(value), null, DataProtectionScope.CurrentUser));
        J.Write(path, J.Canon(data));
    }
}
