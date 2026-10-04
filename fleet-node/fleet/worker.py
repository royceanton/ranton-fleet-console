"""Bounded native Codex execution, worktree isolation and session continuity."""

import importlib.util
import hashlib
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import threading
import time
from .observability import operation_evidence, redact

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("fleet_sessions", ROOT / "scripts/41_codex_account_session.py")
sessions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sessions)


def git(path, *arguments):
    result = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", str(path), *arguments],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError("Git workspace operation failed; source checkout was not reset")
    return result.stdout.strip()


def safe_project(path):
    path = Path(path).expanduser()
    sessions.accounts.reject_symlinks(path)
    path = path.resolve(strict=True)
    if not path.is_dir():
        raise ValueError("Project must be a local directory")
    home = Path.home()
    protected = [home / name for name in (".codex", ".ssh", ".config", "Library", ".local/share/codex-fleet", ".local/share/ranton-proxy")]
    if path == home or path == ROOT.parent or len(path.parts) < 4:
        raise ValueError("Register one project, not an entire home or filesystem")
    if any(path == item or item in path.parents or path in item.parents for item in protected):
        raise ValueError("Credential and system directories cannot be fleet projects")
    return path


def prepare_workspace(task, project, workspaces):
    source = safe_project(project["path"])
    if task.get("workspace"):
        workspace = Path(task["workspace"])
        sessions.accounts.reject_symlinks(workspace)
        if not workspace.is_dir():
            raise RuntimeError("Task workspace is missing; recover it before resuming")
        return workspace, task.get("branch"), task.get("base_revision")
    if task["mode"] == "read-only":
        revision = git(source, "rev-parse", "HEAD") if project["git"] else None
        return source, None, revision
    if not project["git"] or not project["writable"]:
        raise RuntimeError("Code edits require a registered Git repository with isolated edits enabled")
    if Path(git(source, "rev-parse", "--show-toplevel")).resolve() != source:
        raise RuntimeError("Register the repository root before allowing code edits")
    revision = git(source, "rev-parse", "HEAD")
    branch = "fleet/" + task["id"]
    workspace = workspaces / task["id"]
    if workspace.exists():
        raise RuntimeError("Workspace already exists; inspect it instead of overwriting")
    git(source, "worktree", "add", "-b", branch, str(workspace), revision)
    return workspace, branch, revision


def permission_arguments(task, project):
    """Ignore legacy profile sandbox config; select one explicit minimal policy."""
    access = "write" if task["mode"] == "code" else "read"
    home = Path.home()
    rules = {":minimal": "read", str(Path(task["workspace"])): access,
             str(home / ".codex"): "deny", str(home / ".ssh"): "deny",
             str(home / "Library/Keychains"): "deny", str(sessions.accounts.FLEET_ROOT): "deny",
             str(home / ".config"): "deny", str(home / ".local/share/ranton-proxy"): "deny", "/opt/homebrew": "read",
             str(home / ".nvm"): "read", str(home / ".pyenv"): "read"}
    if project["git"]:
        # Git status/diff need the original repository's metadata, never writes.
        rules[str(Path(project["path"]) / ".git")] = "read"
    entries = [json.dumps(key) + "=" + json.dumps(value) for key, value in rules.items()]
    workspace = '{"."=' + json.dumps(access) + ',".git"="read",".codex"="read","**/.env"="deny","**/.env.*"="deny","**/secrets/**"="deny"}'
    entries.append('":workspace_roots"=' + workspace)
    return ["-c", 'approval_policy="never"', "-c", 'default_permissions="fleet_task"',
            "-c", "permissions.fleet_task.filesystem={" + ",".join(entries) + "}",
            "-c", "permissions.fleet_task.network.enabled=false", "-c", "features.multi_agent=false"]


def prompt_for(task, project):
    policy = """You are executing one bounded task for the personal Ranton Fleet.
Read the project's AGENTS.md and relevant project documentation before changes.
Only work on the submitted goal. Do not access credentials, auth files, Keychain,
other projects or personal files. Never push, merge, deploy, install global tools,
change system settings or perform destructive cleanup. Local command network access
is disabled. If blocked by permissions, dependencies or missing information, explain
the required action instead of trying to escape the sandbox. Do not start child agents.
Finish with a concise result, checks actually run, and any question or remaining work.
"""
    if task["mode"] == "code":
        policy += "Edits are permitted only in this task's isolated worktree. Leave changes for owner review.\n"
    else:
        policy += "This task is read-only. Do not change project files.\n"
    instruction = task.get("pending_prompt") or task["goal"]
    # Stable project/task briefing: no changing clocks/quota readings in prefixes.
    prompt = policy + "\nProject: " + project["name"] + "\nTask: " + task["title"] + "\nGoal:\n" + instruction
    if not task.get("session") and task.get("result"):
        prompt += "\nVerified prior checkpoint:\n" + task["result"][:20000]
    return prompt


def stop_group(proc):
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=3)


def recover_owned_process(task, result_path):
    # A crashed coordinator might leave a native child. Never signal a reused PID
    # or an unrelated Codex session. Our unique output file is the ownership marker.
    result = subprocess.run(["ps", "-axo", "pid=,uid=,pgid=,command="], capture_output=True, text=True, timeout=10)
    marker = str(result_path)
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) != 4 or marker not in parts[3] or "codex" not in parts[3]:
            continue
        try:
            pid, uid, pgid = map(int, parts[:3])
            if uid == os.getuid() and pid == pgid and pid > 1:
                os.killpg(pid, signal.SIGTERM)
        except (ValueError, ProcessLookupError):
            pass


class Worker:
    def __init__(self, store, runtime, workspaces, task, project, account_lock):
        self.store, self.runtime, self.workspaces = store, runtime, workspaces
        self.task, self.project, self.account_lock = task, project, account_lock
        self.cancel = threading.Event()
        self.proc = None

    def run(self):
        task_id = self.task["id"]
        try:
            with self.account_lock:
                self.execute()
        except Exception:
            # Native/provider exceptions can contain secrets; retain no raw error.
            if self.store.task(task_id)["state"] == "running":
                self.store.update(task_id, state="needs_input", error="Worker stopped before completion. Inspect the workspace and retry explicitly.", pid=None)
                self.store.event(task_id, "needs_input", "Worker stopped; no account switch or automatic retry occurred")
        finally:
            if self.proc:
                stop_group(self.proc)
            self.store.finish_operations(task_id)

    def execute(self):
        operation_run = str(time.time_ns()) + ':'
        task = self.store.task(self.task["id"])
        if self.cancel.is_set():
            self.store.update(task["id"], state="paused", pid=None)
            return
        workspace, branch, revision = prepare_workspace(task, self.project, self.workspaces)
        self.store.update(task["id"], workspace=str(workspace), branch=branch, base_revision=revision)
        task = self.store.task(task["id"])
        profile_home, environment, arguments = sessions.accounts.codex_context(task["account"])
        # Native Codex can append benign project-trust records. Back up only the
        # validated non-secret config before launch, never OAuth credentials.
        backups = self.runtime / "profile-config-backups"
        sessions.accounts.reject_symlinks(backups)
        backups.mkdir(mode=0o700, exist_ok=True)
        config = (profile_home / "config.toml").read_bytes()
        digest = hashlib.sha256(config).hexdigest()[:12]
        if not list(backups.glob(task["account"] + "-" + digest + "-*.toml")):
            (backups / (task["account"] + "-" + digest + "-" + str(time.time_ns()) + ".toml")).write_bytes(config)
        # Model tool processes receive no inherited cloud/payment/API environment.
        keep = {"PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL", "TZ", "CODEX_HOME"}
        environment = {key: value for key, value in environment.items() if key in keep}
        environment.update({"OMP_NUM_THREADS": "2", "RAYON_NUM_THREADS": "2",
                            "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"})
        output = self.runtime / "results" / (task["id"] + "-last.txt")
        command = arguments + permission_arguments(task, self.project) + ["exec"]
        if task.get("session"):
            command += ["resume"]
        command += ["--ignore-user-config", "--strict-config", "--json", "--skip-git-repo-check", "-o", str(output)]
        if task.get("model"):
            command += ["-m", task["model"]]
        if task.get("session"):
            command += [task["session"]]
        command += ["-"]
        self.proc = subprocess.Popen(command, cwd=workspace, env=environment, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                     start_new_session=True, bufsize=1)
        self.store.update(task["id"], pid=self.proc.pid, pending_prompt=None)
        self.store.event(task["id"], "running", "Native worker started in " + task["mode"] + " mode")
        self.proc.stdin.write(prompt_for(task, self.project))
        self.proc.stdin.close()
        messages = queue.Queue(maxsize=100)

        def read():
            try:
                for line in self.proc.stdout:
                    if len(line) > 4 * 1024 * 1024:
                        break
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    while self.proc.poll() is None and not self.cancel.is_set():
                        try:
                            messages.put(event, timeout=1)
                            break
                        except queue.Full:
                            continue
                    else:
                        try:
                            messages.put_nowait(event)
                        except queue.Full:
                            break
            finally:
                try:
                    messages.put_nowait(None)
                except queue.Full:
                    pass

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        deadline = time.monotonic() + task["timeout"]
        completed, failed, limited, usage, final_message = False, False, False, {}, ""
        stream_open = True
        while stream_open and time.monotonic() < deadline and not self.cancel.is_set():
            try:
                event = messages.get(timeout=0.5)
            except queue.Empty:
                if not reader.is_alive() and self.proc.poll() is not None:
                    break
                continue
            if event is None:
                stream_open = False
                break
            kind = event.get("type")
            if kind == "thread.started":
                session = event.get("thread_id")
                if isinstance(session, str) and len(session) < 100:
                    self.store.update(task["id"], session=session)
            elif kind == "turn.completed":
                completed = True
                usage = {key: value for key, value in (event.get("usage") or {}).items()
                         if key in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")
                         and isinstance(value, int) and not isinstance(value, bool) and value >= 0}
            elif kind in ("turn.failed", "error"):
                failed = True
                raw = json.dumps(event).lower()
                limited = limited or any(word in raw for word in ("429", "rate limit", "usage limit", "quota exceeded"))
            elif kind in ("item.started", "item.completed"):
                item = event.get("item") or {}
                if item.get("type") == "agent_message" and kind == "item.completed":
                    final_message = str(item.get("text") or "")[:64000]
                elif kind == "item.started" and item.get("type") in ("command_execution", "file_change", "web_search"):
                    label = {"command_execution": "Checking the project", "file_change": "Updating worktree files", "web_search": "Looking up documentation"}[item["type"]]
                    self.store.event(task["id"], "progress", label)
                evidence = operation_evidence(item)
                item_id = item.get('id')
                if evidence and isinstance(item_id, str) and len(item_id) <= 200:
                    exit_code = item.get('exit_code')
                    self.store.operation(task['id'], operation_run + item_id, *evidence,
                        'running' if kind == 'item.started' else item.get('status', 'completed'),
                        exit_code if type(exit_code) is int else None)
        if self.cancel.is_set() or time.monotonic() >= deadline:
            stop_group(self.proc)
            state = "paused" if self.cancel.is_set() else "needs_input"
            self.store.update(task["id"], state=state, pid=None, error="Paused by owner" if self.cancel.is_set() else "Task time bound reached; continue explicitly")
            self.store.event(task["id"], state, "Worktree and account/session binding preserved")
        else:
            try:
                exit_code = self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                stop_group(self.proc)
                exit_code = -1
            if output.is_file():
                final_message = output.read_text(errors="replace")[:64000]
            final_message = redact(final_message, 64000)
            if completed and exit_code == 0 and final_message.strip():
                self.store.update(task["id"], state="review", result=final_message, error=None, usage=json.dumps(usage), pid=None)
                self.store.event(task["id"], "review", "Result is ready for review")
            else:
                message = "Provider limit reached. The task stays on its bound account." if limited else "Native worker failed. Retry explicitly after reviewing the workspace."
                self.store.update(task["id"], state="needs_input", error=message, result=final_message or None, usage=json.dumps(usage), pid=None)
                self.store.event(task["id"], "needs_input", message)
                with self.store.connect() as db:
                    db.execute("UPDATE accounts SET cooldown=? WHERE alias=?", (time.time() + 600, task["account"]))
        # Portable checkpoint holds verified task/workspace/result facts, not auth
        # files or an assumed transferable provider computation cache.
        saved = self.store.task(task["id"])
        checkpoint = {key: saved.get(key) for key in ("id", "goal", "account", "session", "workspace", "branch", "base_revision", "state", "result", "usage", "error")}
        (self.runtime / "results" / (task["id"] + "-checkpoint.json")).write_text(json.dumps(checkpoint, indent=2) + "\n")
        if self.proc.stdout:
            self.proc.stdout.close()
