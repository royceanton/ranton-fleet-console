"""SQLite state with short transactions and durable task/account bindings."""

from contextlib import contextmanager
import json
import sqlite3
import time
import uuid


class Store:
    def __init__(self, path):
        self.path = path
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT UNIQUE NOT NULL,
                    git INTEGER NOT NULL, writable INTEGER NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS accounts(alias TEXT PRIMARY KEY, snapshot TEXT NOT NULL, cooldown REAL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, title TEXT NOT NULL, project TEXT NOT NULL,
                    goal TEXT NOT NULL, mode TEXT NOT NULL, priority INTEGER NOT NULL, timeout INTEGER NOT NULL,
                    model TEXT, state TEXT NOT NULL, account TEXT, session TEXT, workspace TEXT, branch TEXT,
                    base_revision TEXT, route TEXT, result TEXT, error TEXT, usage TEXT, pid INTEGER,
                    pending_prompt TEXT, created REAL NOT NULL, updated REAL NOT NULL, idempotency TEXT UNIQUE);
                CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, task TEXT, at REAL NOT NULL,
                    kind TEXT NOT NULL, message TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions(hash TEXT PRIMARY KEY, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS operations(task TEXT NOT NULL, item TEXT NOT NULL,
                    kind TEXT NOT NULL, command TEXT NOT NULL, state TEXT NOT NULL, exit_code INTEGER,
                    started REAL NOT NULL, finished REAL, PRIMARY KEY(task,item));
                CREATE TABLE IF NOT EXISTS gateway_bindings(scope TEXT PRIMARY KEY,
                    account TEXT NOT NULL, created REAL NOT NULL, touched REAL NOT NULL);
            """)
            for key, value in {"dispatchEnabled": True, "reservePercent": 10, "maxSlots": 2}.items():
                db.execute("INSERT OR IGNORE INTO settings VALUES(?,?)", (key, json.dumps(value)))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def setting(self, key, value=None):
        with self.connect() as db:
            if value is not None:
                db.execute("UPDATE settings SET value=? WHERE key=?", (json.dumps(value), key))
            return json.loads(db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()[0])

    def projects(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM projects ORDER BY name")]

    def add_project(self, name, path, git, writable):
        with self.connect() as db:
            previous = db.execute("SELECT * FROM projects WHERE path=?", (path,)).fetchone()
            if previous:
                db.execute("UPDATE projects SET name=?,git=?,writable=? WHERE id=?", (name, git, writable, previous["id"]))
                return dict(db.execute("SELECT * FROM projects WHERE id=?", (previous["id"],)).fetchone())
            project_id = uuid.uuid4().hex[:12]
            db.execute("INSERT INTO projects VALUES(?,?,?,?,?,?)", (project_id, name, path, git, writable, time.time()))
            return dict(db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone())

    def observations(self):
        with self.connect() as db:
            return {row["alias"]: {**json.loads(row["snapshot"]), "cooldownUntil": row["cooldown"]}
                    for row in db.execute("SELECT * FROM accounts")}

    def observe(self, alias, snapshot):
        with self.connect() as db:
            previous = db.execute("SELECT value FROM settings WHERE key=?", ("identity:" + alias,)).fetchone()
            observed_id = snapshot.get("identityFingerprint")
            if previous and json.loads(previous[0]) != observed_id:
                # Identity binding survives sign-out and subsequent sign-ins.
                snapshot = {**snapshot, "authentication": "identity-changed", "quotaStatus": "unknown"}
            elif not previous and observed_id:
                db.execute("INSERT INTO settings VALUES(?,?)", ("identity:" + alias, json.dumps(observed_id)))
            db.execute("INSERT INTO accounts(alias,snapshot) VALUES(?,?) ON CONFLICT(alias) DO UPDATE SET snapshot=excluded.snapshot",
                       (alias, json.dumps(snapshot)))

    def tasks(self):
        with self.connect() as db:
            # Never hide an unfinished task behind a display-history limit.
            return [dict(row) for row in db.execute("""SELECT * FROM tasks WHERE state != 'completed'
                OR id IN (SELECT id FROM tasks WHERE state='completed' ORDER BY created DESC LIMIT 300)
                ORDER BY created DESC""")]

    def task(self, task_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            return dict(row) if row else None

    def submit(self, data):
        now, task_id = time.time(), uuid.uuid4().hex[:12]
        with self.connect() as db:
            if data.get("idempotency"):
                old = db.execute("SELECT * FROM tasks WHERE idempotency=?", (data["idempotency"],)).fetchone()
                if old:
                    return dict(old)
            db.execute("""INSERT INTO tasks(id,title,project,goal,mode,priority,timeout,model,state,created,updated,idempotency)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (task_id, data["title"], data["project"], data["goal"], data["mode"], data["priority"],
                        data["timeout"], data.get("model"), "queued", now, now, data.get("idempotency")))
            db.execute("INSERT INTO events(task,at,kind,message) VALUES(?,?,?,?)", (task_id, now, "queued", "Task submitted; account selection is automatic"))
        return self.task(task_id)

    def update(self, task_id, **values):
        allowed = {"state", "account", "session", "workspace", "branch", "base_revision", "route", "result", "error", "usage", "pid", "pending_prompt", "model"}
        if not values or set(values) - allowed:
            raise ValueError("Invalid task update")
        values["updated"] = time.time()
        with self.connect() as db:
            db.execute("UPDATE tasks SET " + ",".join(key + "=?" for key in values) + " WHERE id=?", (*values.values(), task_id))

    def claim(self, task_id, alias, reason):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT COUNT(*) FROM tasks WHERE state='running' AND account=?", (alias,)).fetchone()[0]:
                return False
            changed = db.execute("UPDATE tasks SET state='running',account=?,route=?,error=NULL,updated=? WHERE id=? AND state IN ('queued','waiting')",
                                 (alias, reason, time.time(), task_id)).rowcount
            return bool(changed)

    def event(self, task_id, kind, message):
        with self.connect() as db:
            db.execute("INSERT INTO events(task,at,kind,message) VALUES(?,?,?,?)", (task_id, time.time(), kind, message[:1000]))
            db.execute("DELETE FROM events WHERE id < (SELECT COALESCE(MAX(id),0)-3000 FROM events)")

    def events(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM events ORDER BY id DESC LIMIT 80")]

    def operation(self, task, item, kind, command, state, exit_code=None):
        now = time.time()
        with self.connect() as db:
            db.execute("""INSERT INTO operations VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(task,item) DO UPDATE SET state=excluded.state,
                command=excluded.command,exit_code=excluded.exit_code,finished=excluded.finished""",
                (task, item, kind, command, state, exit_code, now,
                 None if state == 'running' else now))
            db.execute("""DELETE FROM operations WHERE rowid NOT IN
                (SELECT rowid FROM operations WHERE state='running' OR rowid IN
                 (SELECT rowid FROM operations ORDER BY started DESC LIMIT 3000))""")

    def operations(self, task=None):
        with self.connect() as db:
            if task:
                return [dict(row) for row in db.execute(
                    "SELECT * FROM operations WHERE task=? ORDER BY started DESC LIMIT 100", (task,))]
            return [dict(row) for row in db.execute(
                "SELECT * FROM operations ORDER BY started DESC LIMIT 80")]

    def finish_operations(self, task):
        with self.connect() as db:
            db.execute("UPDATE operations SET state='interrupted',finished=? WHERE task=? AND state='running'",
                       (time.time(), task))
