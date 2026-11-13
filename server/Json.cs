using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace DbFlow;

public sealed class FlowException(string message) : Exception(message);

public static class J
{
    public static JsonObject Obj(params (string Key, object? Value)[] values)
    {
        var result = new JsonObject();
        foreach (var (key, value) in values) result[key] = Node(value);
        return result;
    }
    public static JsonNode? Node(object? value) => value is JsonNode n ? n.DeepClone() : JsonSerializer.SerializeToNode(value);
    public static JsonArray Arr(IEnumerable<JsonNode?> values) => new(values.Select(n => n?.DeepClone()).ToArray());
    public static string S(this JsonNode? node, string key, string fallback = "") => node?[key]?.GetValue<string>() ?? fallback;
    public static JsonObject O(this JsonNode? node, string key) => node?[key] as JsonObject ?? new();
    public static JsonArray A(this JsonNode? node, string key) => node?[key] as JsonArray ?? new();
    public static List<string> Strings(this JsonNode? node, string key) => node.A(key).Select(n => n!.GetValue<string>()).ToList();
    public static string Id(string value)
    {
        if (!Regex.IsMatch(value, @"\A[A-Za-z0-9][A-Za-z0-9_.-]{0,119}\z") || value.Contains("..")) throw new FlowException("Invalid identifier.");
        return value;
    }
    public static string Text(string value, string field, int max = 1000000)
    {
        if (string.IsNullOrWhiteSpace(value) || Encoding.UTF8.GetByteCount(value) > max) throw new FlowException($"Invalid or oversized {field}.");
        return value;
    }
    // Python ensure_ascii=False/sort_keys=True compatibility, including non-BMP characters.
    public static string Canon(JsonNode? node) => node switch
    {
        null => "null",
        JsonObject obj => "{" + string.Join(",", obj.OrderBy(k => k.Key, CodepointComparer.Instance).Select(k => Quote(k.Key) + ":" + Canon(k.Value))) + "}",
        JsonArray array => "[" + string.Join(",", array.Select(Canon)) + "]",
        JsonValue val when val.TryGetValue<string>(out var str) => Quote(str),
        _ => node.ToJsonString()
    };
    static string Quote(string str)
    {
        var b = new StringBuilder("\"");
        foreach (char c in str) b.Append(c switch { '"' => "\\\"", '\\' => "\\\\", '\b' => "\\b", '\f' => "\\f", '\n' => "\\n", '\r' => "\\r", '\t' => "\\t", < ' ' => "\\u" + ((int)c).ToString("x4"), _ => c.ToString() });
        return b.Append('"').ToString();
    }
    sealed class CodepointComparer : IComparer<string>
    {
        public static readonly CodepointComparer Instance = new();
        public int Compare(string? a, string? b)
        {
            var x = (a ?? "").EnumerateRunes().Select(r => r.Value).ToArray();
            var y = (b ?? "").EnumerateRunes().Select(r => r.Value).ToArray();
            for (int i = 0; i < Math.Min(x.Length, y.Length); i++) if (x[i] != y[i]) return x[i].CompareTo(y[i]);
            return x.Length.CompareTo(y.Length);
        }
    }
    public static string Hash(string value) => Hash(Encoding.UTF8.GetBytes(value));
    public static string Hash(JsonNode? node) => Hash(Canon(node));
    public static string Hash(byte[] value) => Convert.ToHexString(SHA256.HashData(value)).ToLowerInvariant();
    public static string Now() => DateTimeOffset.UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.ffffffzzz");
    public static string SafePath(string root, string relative)
    {
        if (string.IsNullOrEmpty(relative) || relative.Contains('\\') || relative.Contains(':') || Path.IsPathRooted(relative) || relative.Split('/').Any(p => p is "." or ".." or "")) throw new FlowException("Invalid relative path.");
        var full = Path.GetFullPath(Path.Combine(root, relative));
        var prefix = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
        if (!full.StartsWith(prefix, OperatingSystem.IsWindows() ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal)) throw new FlowException("Path escapes root.");
        var current = new FileInfo(full) as FileSystemInfo;
        while (current != null && current.FullName.Length >= prefix.Length)
        {
            if (current.Exists && (current.Attributes & FileAttributes.ReparsePoint) != 0) throw new FlowException("Linked paths are not supported.");
            current = current is FileInfo f ? f.Directory : ((DirectoryInfo)current).Parent;
        }
        return full;
    }
    public static void Write(string path, string text)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        var tmp = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try
        {
            using (var f = new FileStream(tmp, FileMode.CreateNew, FileAccess.Write)) { var bytes = Encoding.UTF8.GetBytes(text); f.Write(bytes); f.Flush(true); }
            File.Move(tmp, path, true);
        }
        finally { if (File.Exists(tmp)) File.Delete(tmp); }
    }
}
