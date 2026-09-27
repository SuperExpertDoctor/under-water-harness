import json
import sqlite3
from pathlib import Path
from collections.abc import MutableMapping
from contextlib import contextmanager


class ReceiptLedger(MutableMapping):
    """Durable idempotency lookup without retaining responses in the live heap."""
    def __init__(self, store):
        self.store = store

    def __getitem__(self, key):
        value = self.store.get_receipt(key)
        if value is None:
            raise KeyError(key)
        return value

    def __setitem__(self, key, value):
        with self.store.transaction():
            self.store.db.execute("INSERT OR REPLACE INTO receipts VALUES(?,?)", (key, json.dumps(value, allow_nan=False)))

    def __delitem__(self, key):
        with self.store.transaction():
            self.store.db.execute("DELETE FROM receipts WHERE id=?", (key,))

    def __iter__(self):
        return (row[0] for row in self.store.db.execute("SELECT id FROM receipts"))

    def __len__(self):
        return self.store.db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0]


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self._transaction_depth = 0
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS checkpoint(id INTEGER PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS frames(id INTEGER PRIMARY KEY AUTOINCREMENT,
                episode TEXT NOT NULL, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS frames_episode ON frames(episode,id);
            CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS plan_history(id TEXT PRIMARY KEY, data TEXT NOT NULL);
        """)

    @contextmanager
    def transaction(self):
        depth = self._transaction_depth
        savepoint = f"mission_transaction_{depth}"
        self.db.execute("BEGIN IMMEDIATE" if depth == 0 else f"SAVEPOINT {savepoint}")
        self._transaction_depth += 1
        try:
            yield
            if depth == 0:
                self.db.commit()
            else:
                self.db.execute(f"RELEASE SAVEPOINT {savepoint}")
        except BaseException:
            if depth == 0:
                self.db.rollback()
            else:
                self.db.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                self.db.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
        finally:
            self._transaction_depth -= 1

    def load(self):
        row = self.db.execute("SELECT data FROM checkpoint WHERE id=1").fetchone()
        return json.loads(row[0]) if row else None

    def get_receipt(self, key):
        row = self.db.execute("SELECT data FROM receipts WHERE id=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def archive_plan(self, plan):
        with self.transaction():
            self.db.execute("INSERT OR REPLACE INTO plan_history VALUES(?,?)", (plan["plan_id"], json.dumps(plan, allow_nan=False)))

    def get_plan(self, plan_id):
        row = self.db.execute("SELECT data FROM plan_history WHERE id=?", (plan_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, data):
        with self.transaction():
            self.db.execute("INSERT OR REPLACE INTO checkpoint VALUES(1,?)", (json.dumps(data, allow_nan=False),))

    def frame(self, episode, frame):
        with self.transaction():
            self.db.execute("INSERT INTO frames(episode,data) VALUES(?,?)", (episode, json.dumps(frame, allow_nan=False)))
            self.db.execute("DELETE FROM frames WHERE episode=? AND id NOT IN (SELECT id FROM frames WHERE episode=? ORDER BY id DESC LIMIT 7200)", (episode, episode))
            self.db.execute("DELETE FROM frames WHERE episode NOT IN (SELECT episode FROM frames GROUP BY episode ORDER BY MAX(id) DESC LIMIT 8)")

    def episodes(self):
        return [row[0] for row in self.db.execute("SELECT episode FROM frames GROUP BY episode ORDER BY MAX(id) DESC")]

    def replay(self, episode, offset, limit):
        total = self.db.execute("SELECT COUNT(*) FROM frames WHERE episode=?", (episode,)).fetchone()[0]
        rows = self.db.execute("SELECT data FROM frames WHERE episode=? ORDER BY id LIMIT ? OFFSET ?", (episode, limit, offset))
        return {"total": total, "frames": [json.loads(row[0]) for row in rows]}

    def close(self):
        self.db.close()
