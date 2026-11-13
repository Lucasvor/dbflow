using System.Text.RegularExpressions;
using System.Text.Json.Nodes;

namespace DbFlow;

public static class Scanner
{
    static readonly HashSet<string> Excluded = new(StringComparer.OrdinalIgnoreCase) { ".git", ".db-control", "node_modules", ".venv", "venv", "__pycache__", "bin", "obj" };
    public static JsonObject Scan(Control c)
    {
        var patterns = c.Config.O("watch").Strings("include");
        bool Match(string path) => patterns.Any(p => Regex.IsMatch(path, "^" + Regex.Escape(p).Replace("\\*", ".*").Replace("\\?", ".") + "$", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant));
        var present = new Dictionary<string, string>(); var limitations = new List<string>(); int count = 0;
        void Visit(string dir)
        {
            if (count > 10000) return;
            try
            {
                foreach (var path in Directory.EnumerateFileSystemEntries(dir))
                {
                    var attrs = File.GetAttributes(path); if ((attrs & FileAttributes.ReparsePoint) != 0) continue;
                    if ((attrs & FileAttributes.Directory) != 0) { if (!Excluded.Contains(Path.GetFileName(path))) Visit(path); continue; }
                    var rel = Path.GetRelativePath(c.Root, path).Replace('\\', '/'); if (!Match(rel)) continue;
                    if (++count > 10000) { limitations.Add("Scan limited to 10000 matching files."); return; }
                    try { if (new FileInfo(path).Length > 1000000) limitations.Add(rel + ": exceeds 1 MB"); else present[rel] = J.Hash(File.ReadAllBytes(path)); }
                    catch (IOException) { limitations.Add(rel + ": unreadable"); }
                }
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { limitations.Add("A directory could not be inspected."); }
        }
        Visit(c.Root);
        var known = c.Store.Query("SELECT * FROM files").ToDictionary(r => r.S("path"));
        foreach (var (path, hash) in present) if (!known.TryGetValue(path, out var prev) || prev.S("hash") != hash) c.Store.Execute("INSERT OR REPLACE INTO files VALUES($p0,$p1,'candidate',NULL,NULL,$p2)", path, hash, J.Now());
        if (limitations.Count == 0) foreach (var (path, prev) in known) if (!present.ContainsKey(path) && prev.S("hash") != "DELETED" && Match(path)) c.Store.Execute("UPDATE files SET hash='DELETED',disposition='candidate',revision=NULL,reason=NULL,updated=$p0 WHERE path=$p1", J.Now(), path);
        var coverage = J.Obj(("checked_at", J.Now()), ("limitations", limitations), ("scope", patterns), ("external_database_changes", "not_observable_by_hooks"));
        c.Store.Execute("INSERT OR REPLACE INTO checks VALUES('coverage',$p0)", J.Canon(coverage));
        return J.Obj(("candidates", J.Arr(c.Store.Query("SELECT * FROM files WHERE disposition='candidate' ORDER BY path"))), ("pending_sync", c.Store.Pending), ("pending_remote_requests", c.Store.Query("SELECT id FROM requests WHERE state IN ('pending','rejected')").Count), ("coverage", coverage));
    }
    public static JsonObject Exclude(Control c, JsonObject args)
    {
        J.SafePath(c.Root, args.S("path")); if (args.S("reason").Trim().Length < 10) throw new FlowException("Provide a concrete exclusion reason (at least 10 characters).");
        if (c.Store.Execute("UPDATE files SET disposition='excluded',reason=$p0,updated=$p1 WHERE path=$p2 AND hash=$p3", args.S("reason"), J.Now(), args.S("path"), args.S("sha256")) != 1) throw new FlowException("Candidate changed or unknown; rescan.");
        return J.Obj(("path", args.S("path")), ("sha256", args.S("sha256")), ("disposition", "excluded"), ("reason", args.S("reason")));
    }
    public static void Export(string root, JsonObject value)
    {
        var files = value.O("bundle_files"); if (files.Count == 0) return;
        var directory = J.SafePath(root, ".db-control/local/packages/" + J.Id(value.S("bundle_id")));
        var preserved = new List<string>();
        foreach (var (rel, data) in files)
        {
            var path = J.SafePath(directory, rel); var text = data!.GetValue<string>();
            if (File.Exists(path) && J.Hash(File.ReadAllBytes(path)) != J.Hash(text))
            {
                var old = File.ReadAllText(path);
                if (rel == "email.txt" || (rel == "manifest.json" && J.Canon(JsonNode.Parse(old)) == J.Canon(JsonNode.Parse(text)))) { preserved.Add(rel); continue; }
                throw new FlowException("Local package edited; refusing overwrite.");
            }
            J.Write(path, text);
        }
        value["local_directory"] = directory;
        if (preserved.Count > 0) value["preserved_local_metadata_or_drafts"] = J.Node(preserved);
    }
}
