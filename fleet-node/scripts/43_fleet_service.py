#!/usr/bin/env python3
"""Install/manage the owner-scoped coordinator LaunchAgent, without sudo."""

import argparse
import datetime
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
LABEL = "com.ranton.fleet"
DOMAIN = "gui/" + str(os.getuid())
TARGET = DOMAIN + "/" + LABEL
PLIST = Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")
RUNTIME = Path.home() / ".local/share/codex-fleet/runtime"


def call(*args):
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=15)


def status():
    result = call("print", TARGET)
    if result.returncode:
        print("Coordinator LaunchAgent is not loaded")
        return False
    state = re.search(r"\bstate = ([^\n]+)", result.stdout)
    pid = re.search(r"\bpid = (\d+)", result.stdout)
    print("Coordinator: " + (state.group(1).strip() if state else "loaded") + ("; PID " + pid.group(1) if pid else ""))
    return True


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "status", "start", "stop", "restart"))
    args = parser.parse_args()
    if args.action == "status":
        status();return
    if args.action == "install":
        RUNTIME.mkdir(mode=0o700, parents=True, exist_ok=True)
        PLIST.parent.mkdir(parents=True, exist_ok=True)
        if PLIST.is_symlink():
            raise RuntimeError("Refusing a symlinked service configuration")
        payload = plistlib.dumps({"Label": LABEL, "ProgramArguments": [str(Path(sys.executable).resolve()), "-m", "fleet.server"],
                                 "WorkingDirectory": str(ROOT), "RunAtLoad": True, "KeepAlive": True,
                                 "ThrottleInterval": 10, "ProcessType": "Background",
                                 "EnvironmentVariables": {"PATH": os.environ["PATH"], "HOME": str(Path.home()), "PYTHONUNBUFFERED": "1"},
                                 "StandardOutPath": str(RUNTIME / "service.log"), "StandardErrorPath": str(RUNTIME / "service-error.log")}, sort_keys=True)
        if PLIST.exists() and PLIST.read_bytes() != payload:
            backup = RUNTIME / ("launchagent-" + datetime.datetime.now().strftime("%Y%m%dT%H%M%S") + ".plist")
            shutil.copy2(PLIST, backup)
            print("Existing service configuration backed up to " + str(backup))
            call("bootout", TARGET)
        if not PLIST.exists() or PLIST.read_bytes() != payload:
            PLIST.write_bytes(payload)
            PLIST.chmod(0o600)
        if not call("print", TARGET).returncode:
            status();return
        result = call("bootstrap", DOMAIN, str(PLIST))
        if result.returncode:
            raise RuntimeError("macOS did not load the LaunchAgent; exit " + str(result.returncode))
    elif args.action == "stop":
        result = call("bootout", TARGET)
        if result.returncode and call("print", TARGET).returncode == 0:
            raise RuntimeError("macOS did not unload the coordinator")
        print("Coordinator unloaded. Task/workspace/credential state is preserved.")
        return
    elif args.action == "restart":
        result = call("kickstart", "-k", TARGET)
        if result.returncode:
            raise RuntimeError("Coordinator is not loaded; run start first")
    else:
        if call("print", TARGET).returncode:
            if call("bootstrap", DOMAIN, str(PLIST)).returncode:
                raise RuntimeError("Coordinator start failed")
    status()


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print("Fleet service: " + str(error), file=sys.stderr)
        sys.exit(1)
