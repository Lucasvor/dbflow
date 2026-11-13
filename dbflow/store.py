import sqlite3
import json
from pathlib import Path
from .common import encoded, now, digest, FlowError


class Store:
    """Local durable journal only. SQLite never connects to a business database."""
    def __init__(self, root):
        local = Path(root) / ".db-control/local"
        local.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(local / "journal.sqlite3", timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, payload TEXT NOT NULL,
          sync TEXT NOT NULL, remote_commit TEXT, error TEXT, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, hash TEXT NOT NULL, disposition TEXT NOT NULL,
          revision TEXT, reason TEXT, updated TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS checks(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY, body TEXT NOT NULL, state TEXT NOT NULL, error TEXT);
        """)

    def close(self):
        self.db.close()

    def events(self, kind=None):
        values = [json.loads(r[0]) for r in self.db.execute("SELECT payload FROM events ORDER BY created,id")]
        return [v for v in values if kind is None or v["kind"] == kind]

    def add(self, event, sync="pending", commit=None):
        old = self.db.execute("SELECT payload FROM events WHERE id=?", (event["id"],)).fetchone()
        payload = encoded(event)
        if old and old[0] != payload:
            raise FlowError("Event identity collision; existing content differs.")
        if not old:
            self.db.execute("INSERT INTO events VALUES(?,?,?,?,?,?)",
                            (event["id"], payload, sync, commit, None, event["created_at"]))
        elif sync == "synced":
            self.db.execute("UPDATE events SET sync='synced',remote_commit=?,error=NULL WHERE id=?", (commit, event["id"]))

    def operation(self, operation_id, arguments, callback):
        fingerprint = digest(arguments)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            previous = self.db.execute("SELECT fingerprint,result FROM operations WHERE id=?", (operation_id,)).fetchone()
            if previous:
                if previous[0] != fingerprint:
                    raise FlowError("operation_id already used with different arguments.")
                return json.loads(previous[1])
            result = callback()
            self.db.execute("INSERT INTO operations VALUES(?,?,?)", (operation_id, fingerprint, encoded(result)))
            return result

    def set_check(self, key, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO checks VALUES(?,?)", (key, encoded(value)))

    def get_check(self, key, default=None):
        row = self.db.execute("SELECT value FROM checks WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default
