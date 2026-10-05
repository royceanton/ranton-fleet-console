#!/usr/bin/env python3
"""Run the packaged CLIProxyAPI with private, owner-scoped configuration."""

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import plistlib
import re
import secrets
import shutil
import stat
import subprocess
import sys

ROOT = Path.home() / ".local/share/ranton-proxy"
LABEL = "com.ranton.proxy"
DOMAIN = "gui/" + str(os.getuid())
TARGET = DOMAIN + "/" + LABEL
PLIST = Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")
BINARY = Path("/opt/homebrew/bin/cliproxyapi")


def private(path, directory=False):
    for part in (path, *path.parents):
        if part.is_symlink():
            raise RuntimeError("Refusing a symlinked proxy path")
    if directory:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    mode = 0o700 if directory else 0o600
    if path.stat().st_uid != os.getuid() or stat.S_IMODE(path.stat().st_mode) != mode:
        raise RuntimeError("Proxy files must be owner-only")


def call(*args):
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=15)


def status():
    result = call("print", TARGET)
    if result.returncode:
        print("CLIProxyAPI service is not loaded")
        return False
    state = re.search(r"\bstate = ([^\n]+)", result.stdout)
    pid = re.search(r"\bpid = (\d+)", result.stdout)
    print("CLIProxyAPI: " + (state.group(1).strip() if state else "loaded")
          + ("; PID " + pid.group(1) if pid else ""))
    return True


def initialize():
    private(ROOT, True)
    for folder in ("auth", "logs"):
        private(ROOT / folder, True)
    keys = {}
    for name in ("management", "client"):
        path = ROOT / (name + ".key")
        if not path.exists():
            with path.open("x") as handle:
                handle.write(secrets.token_urlsafe(32) + "\n")
        private(path)
        keys[name] = path.read_text().strip()
    config = ROOT / "config.yaml"
    if not config.exists():
        # JSON is valid YAML. The native service owns subsequent YAML serialization.
        document = {
            "config-version": 8,
            "server": {"host": "127.0.0.1", "port": 8317, "trusted-proxies": [],
                       "discovery": {"enabled": False}},
            "management": {"allow-remote": False, "secret-key": keys["management"],
                           "disable-control-panel": False, "disable-auto-update-panel": True,
                           "panel-github-repository": "https://github.com/router-for-me/Cli-Proxy-API-Management-Center"},
            "access": {"api-keys": [keys["client"]]},
            "oauth": {"auth-dir": str(ROOT / "auth"),
                      "providers": {"antigravity": {"antigravity-credits": False}}},
            "routing": {"strategy": "fill-first", "session-affinity": True,
                        "session-affinity-ttl": "24h", "session-affinity-subagents": True,
                        "retry": {"request-retry": 0, "max-retry-interval": 0,
                                  "max-retry-credentials": 1}},
            "observability": {"logs": {"debug": False, "logging-to-file": False, "request-log": False,
                                        "logs-max-total-size-mb": 20, "error-logs-max-files": 3},
                              "usage": {"usage-statistics-enabled": True},
                              "pprof": {"enable": False}},
            "plugins": {"enabled": False},
        }
        with config.open("x") as handle:
            handle.write(json.dumps(document, indent=2) + "\n")
    private(config)


def install():
    if not BINARY.is_file():
        raise RuntimeError("Install the Homebrew cliproxyapi package first")
    initialize()
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    if PLIST.is_symlink():
        raise RuntimeError("Refusing a symlinked LaunchAgent")
    payload = plistlib.dumps({"Label": LABEL,
                             "ProgramArguments": [str(BINARY), "-config", str(ROOT / "config.yaml")],
                             "WorkingDirectory": str(ROOT), "RunAtLoad": True, "KeepAlive": True,
                             "ThrottleInterval": 10, "ProcessType": "Background", "Umask": 0o077,
                             "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                                                      "HOME": str(Path.home())},
                             "StandardOutPath": "/dev/null", "StandardErrorPath": "/dev/null"}, sort_keys=True)
    if PLIST.exists() and PLIST.read_bytes() != payload:
        backup = ROOT / ("launchagent-" + datetime.now().strftime("%Y%m%dT%H%M%S") + ".plist")
        shutil.copy2(PLIST, backup)
        print("Prior service configuration backed up to " + str(backup))
        call("bootout", TARGET)
    if not PLIST.exists() or PLIST.read_bytes() != payload:
        PLIST.write_bytes(payload)
        PLIST.chmod(0o600)
    if call("print", TARGET).returncode and call("bootstrap", DOMAIN, str(PLIST)).returncode:
        raise RuntimeError("macOS could not load the proxy LaunchAgent")
    status()


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "status", "start", "stop", "restart"))
    action = parser.parse_args().action
    if action == "install":
        install()
    elif action == "status":
        status()
    elif action == "stop":
        result = call("bootout", TARGET)
        if result.returncode and call("print", TARGET).returncode == 0:
            raise RuntimeError("macOS could not unload the proxy")
        print("CLIProxyAPI unloaded; configuration and credentials are preserved")
    elif action == "restart":
        if call("kickstart", "-k", TARGET).returncode:
            raise RuntimeError("Service is not loaded")
        status()
    else:
        private(ROOT / "config.yaml")
        if call("print", TARGET).returncode and call("bootstrap", DOMAIN, str(PLIST)).returncode:
            raise RuntimeError("macOS could not start the proxy")
        status()


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print("Proxy: " + str(error), file=sys.stderr)
        sys.exit(1)
