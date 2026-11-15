"""Single-process SQLite workflow journal. Commit intent before external effects."""
import json
from pathlib import Path
import sqlite3


class Journal:
    def __init__(self, path):
        if path != ':memory:':
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY, owner TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS operations(
              id TEXT PRIMARY KEY, owner TEXT NOT NULL, cid TEXT NOT NULL, kind TEXT NOT NULL,
              request_key TEXT NOT NULL, body TEXT NOT NULL, delegation TEXT, trace TEXT,
              status TEXT NOT NULL, result TEXT, error TEXT,
              UNIQUE(owner, cid, request_key));
        ''')
        if 'request_hash' not in {row['name'] for row in self.db.execute('PRAGMA table_info(operations)')}:
            self.db.execute('ALTER TABLE operations ADD COLUMN request_hash TEXT')
        self.db.execute("UPDATE operations SET status='pending' WHERE status='running'")
        self.db.commit()

    def save(self, cid, owner, data):
        self.db.execute('INSERT OR REPLACE INTO conversations VALUES(?,?,?)', (cid, owner, json.dumps(data)))
        self.db.commit()

    def load(self, cid, owner):
        row = self.db.execute('SELECT data FROM conversations WHERE id=? AND owner=?', (cid, owner)).fetchone()
        return json.loads(row['data']) if row else None

    def close(self):
        self.db.close()
