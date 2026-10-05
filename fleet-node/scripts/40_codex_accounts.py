#!/usr/bin/env python3
"""Prepare isolated native Codex accounts without touching ~/.codex.

Uses the installed Codex CLI, no Platform API key or third-party gateway.
Run login from a local terminal so the owner can complete browser verification.
"""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import sys


FLEET_ROOT = Path.home() / ".local" / "share" / "codex-fleet"
ACCOUNTS_ROOT = FLEET_ROOT / "accounts"
ALIASES = ("harith", "jill")
CONFIG = (
    '# Managed fleet profile; independent of the interactive ~/.codex home.\n'
    'cli_auth_credentials_store = "file"\n'
    'forced_login_method = "chatgpt"\n'
    'sandbox_mode = "read-only"\n'
    'approval_policy = "on-request"\n'
)


def managed_config(text):
    """Native Codex may append trust records; preserve them, never loosen policy."""
    if not text.startswith(CONFIG):
        return False
    allowed = {str(Path(__file__).resolve().parent.parent)}
    database = FLEET_ROOT / "runtime/fleet.sqlite3"
    if database.is_file() and not database.is_symlink() and database.stat().st_uid == os.getuid() and not database.stat().st_mode & 0o077:
        connection = sqlite3.connect("file:" + str(database) + "?mode=ro", uri=True)
        try:
            allowed.update(row[0] for row in connection.execute("SELECT path FROM projects"))
        except sqlite3.DatabaseError:
            pass
        finally:
            connection.close()
    trailing = text[len(CONFIG):].strip()
    while trailing:
        match = re.match(r'\[projects\.("(?:[^"\\]|\\.)*")\]\s*\ntrust_level\s*=\s*"trusted"\s*(?:\n|$)', trailing)
        if not match:
            return False
        try:
            path = Path(json.loads(match.group(1)))
        except (ValueError, TypeError):
            return False
        spaces = Path.home() / ".local/share/codex-fleet-workspaces"
        if str(path) not in allowed and not (path.is_absolute() and path.parent == spaces and path.is_dir()):
            return False
        trailing = trailing[match.end():].strip()
    return True


def reject_symlinks(path):
    for item in (path, *path.parents):
        if item.is_symlink():
            raise RuntimeError("Refusing a symlink in the account path: " + str(item))


def account_home(alias):
    path = ACCOUNTS_ROOT / alias
    reject_symlinks(path)
    if path.resolve() == (Path.home() / ".codex").resolve():
        raise RuntimeError("Fleet account must not use the interactive Codex home")
    return path


def validate_home(path):
    if not path.is_dir():
        raise RuntimeError("Account home missing; run prepare first")
    for item in (FLEET_ROOT, ACCOUNTS_ROOT, path):
        if item.stat().st_uid != os.getuid() or stat.S_IMODE(item.stat().st_mode) != 0o700:
            raise RuntimeError("Expected an owner-only directory: " + str(item))
    config = path / "config.toml"
    reject_symlinks(config)
    if not config.is_file() or not managed_config(config.read_text()):
        raise RuntimeError("Unexpected profile config; review it rather than overwrite it: " + str(config))
    if config.stat().st_uid != os.getuid() or stat.S_IMODE(config.stat().st_mode) != 0o600:
        raise RuntimeError("Expected an owner-only profile config: " + str(config))
    auth = path / "auth.json"
    reject_symlinks(auth)
    if auth.exists() and (
        not auth.is_file() or auth.stat().st_uid != os.getuid()
        or stat.S_IMODE(auth.stat().st_mode) & 0o077
    ):
        raise RuntimeError("Credential file permissions require review: " + str(auth))


def prepare():
    # Check every existing target before making any changes. Never replace config.
    targets = [FLEET_ROOT, ACCOUNTS_ROOT, *(account_home(a) for a in ALIASES)]
    for path in targets:
        reject_symlinks(path)
        if path.exists() and (
            not path.is_dir() or path.stat().st_uid != os.getuid()
            or stat.S_IMODE(path.stat().st_mode) != 0o700
        ):
            raise RuntimeError("Existing directory requires review: " + str(path))
    for alias in ALIASES:
        path = account_home(alias) / "config.toml"
        reject_symlinks(path)
        if path.exists() and (
            not path.is_file() or not managed_config(path.read_text())
            or path.stat().st_uid != os.getuid()
            or stat.S_IMODE(path.stat().st_mode) != 0o600
        ):
            raise RuntimeError("Existing config requires review: " + str(path))
    os.umask(0o077)
    for path in targets:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    for alias in ALIASES:
        path = account_home(alias)
        config = path / "config.toml"
        if not config.exists():
            with config.open("x") as handle:
                handle.write(CONFIG)
        validate_home(path)
    print(json.dumps({
        "accounts": [{"alias": a, "codexHome": str(account_home(a))} for a in ALIASES],
        "interactiveHomeChanged": False,
        "authentication": "Browser sign-in required for each account; no credentials copied",
    }, indent=2))


def codex_context(alias):
    path = account_home(alias)
    validate_home(path)
    executable = shutil.which("codex")
    if not executable:
        raise RuntimeError("Installed Codex CLI is not on PATH")
    environment = os.environ.copy()
    # Do not accidentally inherit a separately billed API or access-token lane.
    for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "OPENAI_BASE_URL"):
        environment.pop(key, None)
    environment["CODEX_HOME"] = str(path)
    os.umask(0o077)
    arguments = [executable, "-c", 'cli_auth_credentials_store="file"',
                 "-c", 'forced_login_method="chatgpt"']
    return path, environment, arguments


def native_command(action, alias):
    path, environment, arguments = codex_context(alias)
    if action == "status":
        result = subprocess.run(arguments + ["login", "status"], env=environment,
                                cwd=path, capture_output=True, text=True, timeout=20)
        # Only expose known status text, never raw output or credential contents.
        output = result.stdout + result.stderr
        status = "unknown"
        if result.returncode == 0 and "Logged in using ChatGPT" in output:
            status = "signed-in-chatgpt"
        elif "Not logged in" in output:
            status = "signed-out"
        print(json.dumps({"alias": alias, "status": status, "exitCode": result.returncode}))
        return 0 if status in ("signed-in-chatgpt", "signed-out") else 1
    if action == "login":
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise RuntimeError("Run login in your local terminal, not in captured logs")
        print("Sign in as " + alias + " in the browser. Check the account before confirming.")
        print("This creates only the isolated fleet login; it does not change ~/.codex.")
        return subprocess.call(arguments + ["login"], env=environment, cwd=path)
    raise RuntimeError("Unsupported action")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "status", "login"))
    parser.add_argument("account", nargs="?", choices=ALIASES)
    args = parser.parse_args()
    if args.action == "prepare":
        if args.account:
            parser.error("prepare initializes both isolated account homes")
        prepare()
        return 0
    if not args.account:
        parser.error("status/login requires harith or jill")
    return native_command(args.action, args.account)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print("Fleet account setup: " + str(error), file=sys.stderr)
        sys.exit(1)
