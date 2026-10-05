"""Authenticated loopback dashboard/API and a supervised two-slot scheduler."""

import argparse
from collections import deque
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import signal
import stat
import uuid
import threading
import time
from urllib.parse import urlsplit

from .router import assess, choose, observed_age
from .store import Store
from .worker import ROOT, Worker, git, recover_owned_process, safe_project, sessions
from .observability import gateway_pick, gateway_event, public_observation, redact
from .codex_monitor import CodexMonitor

RUNTIME = sessions.accounts.FLEET_ROOT / "runtime"
WORKSPACES = Path.home() / ".local/share/codex-fleet-workspaces"
PORT = 8765
PRIVATE_HOST = "macbook-pro.tail9ad173.ts.net:8443"


def chat_origin(data, fallback=None):
    fallback = fallback or {}
    identifier = data.get('origin_chat_id') or fallback.get('origin_chat_id')
    title = data.get('origin_chat_title') or fallback.get('origin_chat_title')
    if data.get('origin_chat_id') and identifier != fallback.get('origin_chat_id') and not data.get('origin_chat_title'):
        raise ValueError('Enter a title for the new control chat association')
    if not identifier:
        if title:
            raise ValueError('A control chat ID is required with its title')
        return None, None
    try:
        if not isinstance(identifier, str) or str(uuid.UUID(identifier)) != identifier:
            raise ValueError()
    except (ValueError, AttributeError):
        raise ValueError('Control chat ID must be a canonical UUID') from None
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 200:
        raise ValueError('Enter the control chat title')
    return identifier, title.strip()


def owner_directory(path):
    sessions.accounts.reject_symlinks(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.stat().st_uid != os.getuid() or stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise RuntimeError("Runtime directory must be owner-only")


def admin_key(runtime):
    path = runtime / "admin.key"
    sessions.accounts.reject_symlinks(path)
    if not path.exists():
        with path.open("x") as handle:
            handle.write(secrets.token_urlsafe(32) + "\n")
    if path.stat().st_uid != os.getuid() or stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise RuntimeError("Application key must be owner-only")
    return path.read_text().strip()


class Fleet:
    def __init__(self, runtime=RUNTIME, workspaces=WORKSPACES):
        os.umask(0o077)
        owner_directory(runtime)
        owner_directory(runtime / "results")
        owner_directory(workspaces)
        self.runtime, self.workspaces = runtime, workspaces
        self.lockfile = (runtime / "coordinator.lock").open("a")
        fcntl.flock(self.lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.key = admin_key(runtime)
        self.store = Store(runtime / "fleet.sqlite3")
        self.guard = threading.RLock()
        self.account_locks = {alias: threading.Lock() for alias in sessions.accounts.ALIASES}
        self.active = {}
        self.stop = threading.Event()
        self.refresh = threading.Event()
        self.last_refresh = 0
        self.refreshing = False
        self.login_attempts = deque()
        self.pair_code = None
        self.pair_expires = 0
        self.started = time.time()
        self.codex_monitor = CodexMonitor()
        if not self.store.projects():
            self.store.add_project("Fleet bootstrap", str(ROOT), False, False)
        for task in self.store.tasks():
            if task["state"] == "running":
                recover_owned_process(task, runtime / "results" / (task["id"] + "-last.txt"))
                self.store.update(task["id"], state="needs_input", pid=None, error="Coordinator restarted. Inspect preserved work and resume explicitly.")
                self.store.event(task["id"], "needs_input", "Interrupted execution was not replayed")
                with self.store.connect() as db:
                    db.execute("UPDATE operations SET state='interrupted',finished=? WHERE task=? AND state='running'", (time.time(), task['id']))
        self.store.finish_operations("gateway")
        self.store.event(None, "coordinator", "Coordinator started; existing account and task bindings preserved")

    def start(self):
        threading.Thread(target=self.observe_loop, daemon=True).start()
        threading.Thread(target=self.schedule_loop, daemon=True).start()

    def observe_loop(self):
        while not self.stop.is_set():
            self.refreshing = True
            for alias, lock in self.account_locks.items():
                if not lock.acquire(blocking=False):
                    continue
                try:
                    snapshot = sessions.observe_one(alias)
                    self.store.observe(alias, snapshot)
                except Exception:
                    # Never log provider exception strings or auth data.
                    previous = self.store.observations().get(alias, {})
                    self.store.observe(alias, {**previous, "quotaStatus": "unknown", "observerError": "Quota refresh failed"})
                finally:
                    lock.release()
            self.last_refresh = time.time()
            self.refreshing = False
            self.refresh.wait(90)
            self.refresh.clear()

    def schedule_loop(self):
        while not self.stop.wait(1):
            try:
                self.schedule()
            except Exception:
                self.store.setting("dispatchEnabled", False)
                self.store.event(None, "needs_input", "Scheduler stopped after an internal error; inspect service before enabling dispatch")

    def schedule(self):
        with self.guard:
            self.active = {key: value for key, value in self.active.items() if value[1].is_alive()}
            if not self.store.setting("dispatchEnabled") or len(self.active) >= self.store.setting("maxSlots"):
                return
            observations = self.store.observations()
            identities = [snapshot.get("identityFingerprint") for snapshot in observations.values() if snapshot.get("authentication") == "chatgpt"]
            if len(identities) != 2 or len(set(identities)) != 2:
                return
            active_counts = {}
            for worker, _ in self.active.values():
                alias = worker.task["account"]
                active_counts[alias] = active_counts.get(alias, 0) + 1
            projects = {p["id"]: p for p in self.store.projects()}
            waiting = sorted([task for task in self.store.tasks() if task["state"] in ("queued", "waiting")],
                             key=lambda task: (-task["priority"], task["created"]))
            for task in waiting:
                if len(self.active) >= self.store.setting("maxSlots"):
                    break
                alias, reason = choose(observations, task, active_counts, time.time(), self.store.setting("reservePercent"))
                if not alias:
                    if task["route"] != reason or task["state"] != "waiting":
                        self.store.update(task["id"], state="waiting", route=reason)
                    continue
                if not self.store.claim(task["id"], alias, reason):
                    continue
                snapshot = observations[alias]
                if not task["model"]:
                    catalog = snapshot["models"]
                    defaults = [m["model"] for m in catalog if m.get("isDefault")]
                    fast = [m["model"] for m in catalog if str(m.get("model", "")).endswith("-luna")]
                    model = (fast if task["mode"] == "read-only" and fast else defaults or [catalog[0]["model"]])[0]
                    self.store.update(task["id"], model=model)
                task = self.store.task(task["id"])
                worker = Worker(self.store, self.runtime, self.workspaces, task, projects[task["project"]], self.account_locks[alias])
                thread = threading.Thread(target=worker.run, daemon=True)
                self.active[task["id"]] = (worker, thread)
                active_counts[alias] = active_counts.get(alias, 0) + 1
                self.store.event(task["id"], "assigned", alias.title() + ": " + reason)
                thread.start()

    def status(self):
        now = time.time()
        observations = self.store.observations()
        accounts = []
        for alias in sessions.accounts.ALIASES:
            snapshot = observations.get(alias, {})
            ready = assess(snapshot, now, self.store.setting("reservePercent"), cooldown=snapshot.get("cooldownUntil", 0))
            public = {key: value for key, value in snapshot.items() if key not in ("identityFingerprint", "quotaError")}
            accounts.append({**public, "alias": alias, "ageSeconds": observed_age(snapshot, now) if snapshot else None,
                             "eligible": ready["eligible"], "reason": ready["reason"]})
        tasks = self.store.tasks()
        counts = {}
        for task in tasks:
            counts[task["state"]] = counts.get(task["state"], 0) + 1
        with self.guard:
            slots = len([value for value in self.active.values() if value[1].is_alive()])
            active_counts = {}
            for worker, thread in self.active.values():
                if thread.is_alive():
                    active_counts[worker.task["account"]] = 1
        alias, reason = choose(observations, {"model": None}, active_counts, now, self.store.setting("reservePercent"))
        # Non-finite JSON is prohibited, including unknown observation ages.
        for account in accounts:
            if account.get("ageSeconds") == float("inf"):
                account["ageSeconds"] = None
        return {"name": "ranton fleet", "host": "24 GB Mac", "now": now, "started": self.started,
                "accounts": accounts, "projects": self.store.projects(), "tasks": tasks,
                "counts": counts, "events": self.store.events(), "dispatchEnabled": self.store.setting("dispatchEnabled"),
                "reservePercent": self.store.setting("reservePercent"), "maxSlots": self.store.setting("maxSlots"),
                "activeSlots": slots, "refreshing": self.refreshing, "nextAccount": alias, "nextReason": reason,
                "privateUrl": "https://" + PRIVATE_HOST, "protocolVersion": "Codex CLI 0.154.0"}

    def submit(self, data):
        if not isinstance(data, dict):
            raise ValueError("Task must be an object")
        project = next((p for p in self.store.projects() if p["id"] == data.get("project")), None)
        if not project:
            raise ValueError("Select a registered project")
        title, goal = data.get("title"), data.get("goal")
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120:
            raise ValueError("Task title must contain 1–120 characters")
        if not isinstance(goal, str) or not 1 <= len(goal.strip()) <= 16000:
            raise ValueError("Describe a goal using 1–16,000 characters")
        mode = data.get("mode", "read-only")
        if mode not in ("read-only", "code"):
            raise ValueError("Unknown task mode")
        if mode == "code" and not (project["git"] and project["writable"]):
            raise ValueError("This project supports analysis only; register a Git repository to allow isolated edits")
        priority, timeout = data.get("priority", 10), data.get("timeout", 1200)
        if type(priority) is not int or priority not in (0, 10, 20):
            raise ValueError("Choose low, normal or high priority")
        if type(timeout) is not int or not 60 <= timeout <= 3600:
            raise ValueError("Task time bound must be 1–60 minutes")
        model = data.get("model") or None
        if model is not None and (not isinstance(model, str) or len(model) > 100):
            raise ValueError("Invalid model")
        requested_account = data.get('requested_account')
        if requested_account is not None and (not isinstance(requested_account, str)
                                              or requested_account not in sessions.accounts.ALIASES):
            raise ValueError('Requested account must be harith or jill; omit it for automatic routing')
        key = data.get("idempotency")
        if key is not None and (not isinstance(key, str) or len(key) > 100):
            raise ValueError("Invalid submission key")
        if sum(task["state"] in ("queued", "waiting") for task in self.store.tasks()) >= 100:
            raise ValueError("Queue is full; review existing tasks")
        parent_id = data.get('parent_task') or None
        parent = self.store.task(parent_id) if isinstance(parent_id, str) else None
        if parent_id and (not parent or parent['project'] != project['id']):
            raise ValueError('The related task must belong to this project')
        origin_id, origin_title = chat_origin(data, parent or project)
        if parent and parent.get('origin_chat_id') and origin_id != parent['origin_chat_id']:
            raise ValueError('A related task must retain its control chat')
        return self.store.submit({"title": title.strip(), "goal": goal.strip(), "project": project["id"],
                                  "mode": mode, "priority": priority, "timeout": timeout, "model": model, "idempotency": key,
                                  'origin_chat_id': origin_id, 'origin_chat_title': origin_title, 'parent_task': parent_id,
                                  'requested_account': requested_account})

    def action(self, task_id, action, data):
        with self.guard:
            task = self.store.task(task_id)
            if not task:
                raise ValueError("Unknown task")
            active = self.active.get(task_id)
            alive = active and active[1].is_alive()
            if action == "pause":
                if alive:
                    active[0].cancel.set()
                else:
                    self.store.update(task_id, state="paused")
            elif action in ("resume", "followup"):
                if alive:
                    raise ValueError("Pause and wait for the worker to stop before continuing")
                if action == "resume" and task["state"] not in ("paused", "needs_input"):
                    raise ValueError("Only paused or interrupted tasks can be resumed")
                if action == "followup":
                    message = data.get("message")
                    if not isinstance(message, str) or not 1 <= len(message.strip()) <= 16000:
                        raise ValueError("Enter a follow-up message")
                    self.store.update(task_id, pending_prompt=message.strip())
                else:
                    self.store.update(task_id, pending_prompt="Continue the original goal from the preserved state. First inspect any partial changes and previous checks.\n" + task["goal"])
                self.store.update(task_id, state="queued", error=None)
                self.refresh.set()
            elif action == "complete":
                if alive or task["state"] not in ("review", "needs_input", "paused"):
                    raise ValueError("Review or pause the task before marking it complete")
                self.store.update(task_id, state="completed")
            else:
                raise ValueError("Unknown task action")
            self.store.event(task_id, action, "Owner requested " + action)
        return self.store.task(task_id)

    def close(self):
        self.stop.set()
        self.refresh.set()
        with self.guard:
            workers = list(self.active.values())
            for worker, _ in workers:
                worker.cancel.set()
        for worker, thread in workers:
            thread.join(8)
            if worker.proc:
                from .worker import stop_group
                stop_group(worker.proc)
        self.lockfile.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "RantonFleet"

    @property
    def fleet(self):
        return self.server.fleet

    def log_message(self, *_):
        pass  # No HTTP logs containing URLs, keys, task goals or cookies.

    def respond(self, status, body, content_type="application/json", cookie=None):
        if content_type == "application/json":
            body = json.dumps(body, allow_nan=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def valid_host(self):
        return self.headers.get("Host") in ("127.0.0.1:" + str(self.server.server_port), "localhost:" + str(self.server.server_port), PRIVATE_HOST)

    def authenticated(self):
        bearer = self.headers.get("Authorization", "")
        if bearer.startswith("Bearer ") and hmac.compare_digest(bearer[7:], self.fleet.key):
            return True
        try:
            cookies = SimpleCookie(self.headers.get("Cookie", ""))
            value = cookies["fleet_session"].value
        except (KeyError, ValueError):
            return False
        digest = hashlib.sha256(value.encode()).hexdigest()
        with self.fleet.store.connect() as db:
            return db.execute("SELECT 1 FROM sessions WHERE hash=? AND expires>?", (digest, time.time())).fetchone() is not None

    def valid_origin(self):
        origin = self.headers.get("Origin")
        if self.headers.get("Authorization", "").startswith("Bearer ") and origin is None:
            return True
        expected = ("https://" if self.headers.get("Host") == PRIVATE_HOST else "http://") + self.headers.get("Host", "")
        return origin == expected and self.headers.get("X-Fleet-Intent") == "dashboard"

    def do_GET(self):
        if not self.valid_host():
            self.respond(403, {"error": "Unrecognized host"})
            return
        path = urlsplit(self.path).path
        if path == "/health":
            self.respond(200, {"status": "ok"})
        elif path in ("/", "/app.js", "/style.css"):
            filename, content_type = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"), "/style.css": ("style.css", "text/css")}[path]
            self.respond(200, (ROOT / "web" / filename).read_bytes(), content_type)
        elif path == "/api/status":
            if not self.authenticated():
                self.respond(401, {"error": "Sign in to the private dashboard"})
            else:
                self.respond(200, self.fleet.status())
        elif path == '/api/observe':
            self.respond(200, public_observation(self.fleet)) if self.authenticated() else self.respond(401, {'error': 'Authentication required'})
        elif path == '/api/codex' or path.startswith('/api/codex/chats/'):
            if not self.authenticated():
                self.respond(401, {'error': 'Authentication required'})
                return
            try:
                payload = self.fleet.codex_monitor.snapshot() if path == '/api/codex' else self.fleet.codex_monitor.detail(path.split('/')[-1])
                self.respond(200, payload) if payload is not None else self.respond(404, {'error': 'Codex chat not found'})
            except (ValueError, OSError):
                self.respond(400, {'error': 'Codex chat is unavailable'})
        elif path.startswith('/api/task/'):
            if not self.authenticated():
                self.respond(401, {'error': 'Authentication required'})
                return
            task = self.fleet.store.task(path.split('/')[-1])
            if not task:
                self.respond(404, {'error': 'Unknown task'})
            else:
                self.respond(200, {'task': {key: redact(value, 64000) if isinstance(value, str) else value for key, value in task.items()},
                                   'operations': self.fleet.store.operations(task['id'])})
        else:
            self.respond(404, {"error": "Unknown path"})

    def do_POST(self):
        if not self.valid_host() or not self.valid_origin():
            self.respond(403, {"error": "Request origin is not allowed"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            if not 0 < length <= 20000 or not self.headers.get("Content-Type", "").startswith("application/json"):
                raise ValueError("Invalid request body")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("Request must be an object")
            path = urlsplit(self.path).path
            if path == "/api/login":
                self.login(data)
                return
            if not self.authenticated():
                self.respond(401, {"error": "Dashboard session expired"})
                return
            if path == "/api/tasks":
                result = self.fleet.submit(data)
            elif path.startswith("/api/tasks/"):
                parts = path.split("/")
                if len(parts) != 5:
                    raise ValueError("Invalid task action")
                result = self.fleet.action(parts[3], parts[4], data)
            elif path == "/api/projects":
                project_path = safe_project(data.get("path", ""))
                name = data.get("name") or project_path.name
                if not isinstance(name, str) or not 1 <= len(name) <= 120:
                    raise ValueError("Invalid project name")
                is_git = False
                try:
                    is_git = Path(git(project_path, "rev-parse", "--show-toplevel")).resolve() == project_path
                except RuntimeError:
                    pass
                previous = next((p for p in self.fleet.store.projects() if p['path'] == str(project_path)), None)
                origin_id, origin_title = chat_origin(data, previous)
                result = self.fleet.store.add_project(name, str(project_path), is_git, is_git and data.get("writable") is True,
                                                      origin_id, origin_title)
            elif path == "/api/dispatch":
                if type(data.get("enabled")) is not bool:
                    raise ValueError("Choose whether dispatch is enabled")
                result = {"enabled": self.fleet.store.setting("dispatchEnabled", data["enabled"])}
            elif path == "/api/refresh":
                self.fleet.refresh.set()
                result = {"requested": True}
            elif path == '/api/policy':
                reserve, slots = data.get('reservePercent'), data.get('maxSlots')
                if type(reserve) is not int or not 5 <= reserve <= 50 or type(slots) is not int or not 1 <= slots <= 2:
                    raise ValueError('Reserve must be 5–50%; worker slots must be 1 or 2')
                with self.fleet.guard, self.fleet.store.connect() as db:
                    for key, value in (('reservePercent', reserve), ('maxSlots', slots)):
                        db.execute('UPDATE settings SET value=? WHERE key=?', (json.dumps(value), key))
                self.fleet.store.event(None, 'policy', 'Routing policy updated: reserve ' + str(reserve) + '%, slots ' + str(slots))
                result = {'updated': True}
            elif path == '/api/gateway/pick':
                result = gateway_pick(self.fleet, data)
            elif path == '/api/gateway/event':
                result = gateway_event(self.fleet, data)
            elif path == "/api/pair":
                with self.fleet.guard:
                    self.fleet.pair_code = str(secrets.randbelow(10 ** 8)).zfill(8)
                    self.fleet.pair_expires = time.time() + 300
                    result = {"code": self.fleet.pair_code, "expires": self.fleet.pair_expires}
            else:
                self.respond(404, {"error": "Unknown action"})
                return
            self.respond(200, result)
        except json.JSONDecodeError:
            self.respond(400, {"error": "Invalid JSON request"})
        except ValueError as error:
            self.respond(400, {"error": str(error)})
        except (TypeError, KeyError):
            self.respond(400, {"error": "Check the task, project path or requested action"})
        except Exception:
            self.respond(500, {"error": "Operation failed; existing state was preserved"})

    def login(self, data):
        now = time.time()
        with self.fleet.guard:
            attempts = self.fleet.login_attempts
            while attempts and attempts[0] < now - 60:
                attempts.popleft()
            if len(attempts) >= 10:
                self.respond(429, {"error": "Wait a minute before trying again"})
                return
            attempts.append(now)
            supplied = data.get("key", "")
            if not isinstance(supplied, str) or len(supplied) > 200:
                supplied = ""
            valid = hmac.compare_digest(supplied, self.fleet.key)
            if self.fleet.pair_code and self.fleet.pair_expires > now and hmac.compare_digest(supplied, self.fleet.pair_code):
                valid = True
                self.fleet.pair_code = None
            if not valid:
                self.respond(401, {"error": "Access key or device code was not accepted"})
                return
            session = secrets.token_urlsafe(32)
            with self.fleet.store.connect() as db:
                db.execute("DELETE FROM sessions WHERE expires<?", (now,))
                db.execute("INSERT INTO sessions VALUES(?,?)", (hashlib.sha256(session.encode()).hexdigest(), now + 30 * 86400))
        secure = "; Secure" if self.headers.get("Host") == PRIVATE_HOST else ""
        self.respond(200, {"authenticated": True}, cookie="fleet_session=" + session + "; HttpOnly; SameSite=Strict; Path=/; Max-Age=2592000" + secure)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    fleet = Fleet()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.daemon_threads = True
    server.fleet = fleet
    fleet.start()

    def stop(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print("Ranton Fleet listening on loopback; provider/HTTP output is not logged", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        fleet.close()


if __name__ == "__main__":
    main()
