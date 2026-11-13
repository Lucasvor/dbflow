using Microsoft.Data.Sqlite;
using System.Text.Json.Nodes;

namespace DbFlow;

public sealed class Store : IDisposable
{
    readonly SqliteConnection db;
    SqliteTransaction? transaction;
    public Store(string root)
    {
        var path = J.SafePath(root, ".db-control/local/journal.sqlite3");
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        db = new SqliteConnection(new SqliteConnectionStringBuilder { DataSource = path, DefaultTimeout = 30, Pooling = false }.ToString());
        db.Open();
        Execute("PRAGMA journal_mode=WAL");
        Execute("""
          CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,payload TEXT NOT NULL,sync TEXT NOT NULL,remote_commit TEXT,error TEXT,created TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,result TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY,hash TEXT NOT NULL,disposition TEXT NOT NULL,revision TEXT,reason TEXT,updated TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS checks(key TEXT PRIMARY KEY,value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,body TEXT NOT NULL,state TEXT NOT NULL,error TEXT);
          """);
    }
    SqliteCommand Command(string sql, object?[] args)
    {
        var command = db.CreateCommand(); command.CommandText = sql; command.Transaction = transaction;
        for (var i = 0; i < args.Length; i++) command.Parameters.AddWithValue("$p" + i, args[i] ?? DBNull.Value);
        return command;
    }
    public int Execute(string sql, params object?[] args) { using var c = Command(sql, args); return c.ExecuteNonQuery(); }
    public List<JsonObject> Query(string sql, params object?[] args)
    {
        using var c = Command(sql, args); using var r = c.ExecuteReader(); var rows = new List<JsonObject>();
        while (r.Read()) { var row = new JsonObject(); for (int i = 0; i < r.FieldCount; i++) row[r.GetName(i)] = r.IsDBNull(i) ? null : J.Node(r.GetValue(i)); rows.Add(row); }
        return rows;
    }
    public List<JsonObject> Events(string? kind = null) => Query("SELECT payload FROM events ORDER BY created,id").Select(r => JsonNode.Parse(r.S("payload"))!.AsObject()).Where(e => kind == null || e.S("kind") == kind).ToList();
    public int Pending => Query("SELECT id FROM events WHERE sync='pending'").Count;
    public void Add(JsonObject ev, string sync = "pending", string? commit = null)
    {
        var payload = J.Canon(ev); var old = Query("SELECT payload FROM events WHERE id=$p0", ev.S("id")).FirstOrDefault();
        if (old != null && old.S("payload") != payload) throw new FlowException("Event identity collision; content differs.");
        if (old == null) Execute("INSERT INTO events VALUES($p0,$p1,$p2,$p3,NULL,$p4)", ev.S("id"), payload, sync, commit, ev.S("created_at"));
        else if (sync == "synced") Execute("UPDATE events SET sync='synced',remote_commit=$p0,error=NULL WHERE id=$p1", commit, ev.S("id"));
    }
    public JsonObject Operation(string id, JsonObject args, Func<JsonObject> apply)
    {
        using var tx = db.BeginTransaction(deferred: false); transaction = tx;
        try
        {
            var fingerprint = J.Hash(args); var old = Query("SELECT fingerprint,result FROM operations WHERE id=$p0", id).FirstOrDefault();
            if (old != null)
            {
                if (old.S("fingerprint") != fingerprint) throw new FlowException("operation_id already used with different arguments.");
                tx.Commit(); return JsonNode.Parse(old.S("result"))!.AsObject();
            }
            var result = apply();
            Execute("INSERT INTO operations VALUES($p0,$p1,$p2)", id, fingerprint, J.Canon(result));
            tx.Commit(); return result;
        }
        finally { transaction = null; }
    }
    public void Acknowledge(JsonArray artifacts, string rid)
    {
        foreach (var c in artifacts.Where(c => c.S("source_path") != "")) Execute("UPDATE files SET disposition='registered',revision=$p0,reason=NULL WHERE path=$p1 AND hash=$p2", rid, c.S("source_path"), c.S("source_sha256"));
    }
    public void Dispose() => db.Dispose();
}
