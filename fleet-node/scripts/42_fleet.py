#!/usr/bin/env python3
"""Control Ranton Fleet locally without displaying its application key."""

import argparse
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request

RUNTIME = Path.home() / ".local/share/codex-fleet/runtime"
BASE = "http://127.0.0.1:8765"


def request(path, data=None):
    keyfile = RUNTIME / "admin.key"
    if keyfile.is_symlink() or keyfile.stat().st_mode & 0o077:
        raise RuntimeError("Application key permissions require review")
    key = keyfile.read_text().strip()
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(BASE + path, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        result = json.load(error)
        raise RuntimeError(result.get("error", "Request was rejected")) from None
    except urllib.error.URLError:
        raise RuntimeError("Fleet is unreachable; check the coordinator service") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    commands.add_parser("refresh")
    project = commands.add_parser("project")
    project.add_argument("path")
    project.add_argument("--name")
    project.add_argument("--allow-edits", action="store_true")
    project.add_argument('--chat-title', help='Associate this project with the current Codex control chat')
    task = commands.add_parser("submit")
    task.add_argument("project")
    task.add_argument("title")
    task.add_argument("--goal-file", help="Read goal from a file; otherwise use stdin")
    task.add_argument("--mode", choices=("read-only", "code"), default="read-only")
    task.add_argument("--priority", type=int, choices=(0, 10, 20), default=10)
    task.add_argument("--timeout", type=int, default=1200)
    task.add_argument("--idempotency")
    task.add_argument("--model")
    task.add_argument('--account', choices=('harith', 'jill'), help='Request one account; default is automatic. Eligibility checks still apply.')
    task.add_argument('--chat-title', help='Title of the current Codex control chat; defaults to project association')
    task.add_argument('--parent-task', help='Related task in the same project; does not share its worker session')
    inspect = commands.add_parser("task")
    inspect.add_argument("id")
    action = commands.add_parser("action")
    action.add_argument("id")
    action.add_argument("operation", choices=("pause", "resume", "complete", "followup"))
    dispatch = commands.add_parser("dispatch")
    dispatch.add_argument("setting", choices=("on", "off"))
    args = parser.parse_args()
    if args.command == "status":
        status = request("/api/status")
        # Brief status leaves task goals/results out of routine control logs.
        result = {key: status[key] for key in ("name", "host", "accounts", "projects", "counts", "dispatchEnabled", "reservePercent", "activeSlots", "maxSlots", "nextAccount", "nextReason", "privateUrl")}
        result["tasks"] = [{key: task[key] for key in ("id", "title", "state", "account", "model", "route")} for task in status["tasks"]]
    elif args.command == "refresh":
        result = request("/api/refresh", {})
    elif args.command == "project":
        origin = {'origin_chat_id': os.environ.get('CODEX_THREAD_ID'), 'origin_chat_title': args.chat_title} if args.chat_title else {}
        result = request("/api/projects", {"path": str(Path(args.path).expanduser()), "name": args.name, "writable": args.allow_edits, **origin})
    elif args.command == "submit":
        goal = Path(args.goal_file).read_text() if args.goal_file else sys.stdin.read()
        origin = {'origin_chat_id': os.environ.get('CODEX_THREAD_ID'), 'origin_chat_title': args.chat_title} if args.chat_title else {}
        result = request("/api/tasks", {"project": args.project, "title": args.title, "goal": goal, "mode": args.mode,
                                       "priority": args.priority, "timeout": args.timeout, "idempotency": args.idempotency, "model": args.model,
                                       'parent_task': args.parent_task, 'requested_account': args.account, **origin})
        result = {key: result[key] for key in ("id", "title", "state", "requested_account")}
    elif args.command == "task":
        result = next((t for t in request("/api/status")["tasks"] if t["id"] == args.id), None)
        if result is None:
            raise RuntimeError("Task was not found")
    elif args.command == "action":
        data = {"message": sys.stdin.read()} if args.operation == "followup" else {}
        task = request("/api/tasks/" + args.id + "/" + args.operation, data)
        result = {key: task[key] for key in ("id", "title", "state", "account", "session")}
    else:
        result = request("/api/dispatch", {"enabled": args.setting == "on"})
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError) as error:
        print("Fleet: " + str(error), file=sys.stderr)
        sys.exit(1)
