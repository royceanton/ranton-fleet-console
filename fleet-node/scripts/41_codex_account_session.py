#!/usr/bin/env python3
"""Native browser login and read-only quota observation for fleet account homes.

Raw OAuth URLs, provider output and credential contents are never logged. This
helper does not start inference, consume reset credits or use Platform API keys.
"""

import argparse
import datetime
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import importlib.util
import json
import math
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("fleet_accounts", ROOT / "scripts/40_codex_accounts.py")
accounts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(accounts)


def timestamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def stop_child(proc):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)


def login(alias, timeout):
    home, environment, arguments = accounts.codex_context(alias)
    if (home / "auth.json").exists():
        raise RuntimeError("An authentication file already exists for " + alias + "; observe it before considering reauthentication")
    # The official CLI owns its browser launch, callback and token lifecycle.
    # Capture/discard its output: normal CLI output contains the OAuth URL.
    proc = subprocess.Popen(arguments + ["login"], env=environment, cwd=home,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    print("Native Codex browser sign-in started for " + alias + ". Awaiting the owner's account verification.", flush=True)
    try:
        try:
            exit_code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError("Browser sign-in timed out for " + alias + "; no raw login output was retained")
        if exit_code:
            raise RuntimeError("Native Codex login exited unsuccessfully for " + alias + "; no raw login output was retained")
        accounts.validate_home(home)
        if not (home / "auth.json").is_file():
            raise RuntimeError("Login returned without a persisted account credential")
        print("Native Codex login completed for " + alias + ". Verifying identity and quota.", flush=True)
        snapshot = observe_one(alias)
        save_snapshot(snapshot)
        print(json.dumps(snapshot, indent=2), flush=True)
        return 0 if snapshot["authentication"] == "chatgpt" else 1
    finally:
        stop_child(proc)


class AccountClient:
    """Short-lived stdio client with only account/limit read methods exposed."""

    allowed_methods = ("initialize", "account/read", "account/rateLimits/read", "model/list")

    def __init__(self, alias):
        home, environment, arguments = accounts.codex_context(alias)
        self.proc = subprocess.Popen(
            arguments + ["app-server", "--stdio", "--strict-config"],
            env=environment, cwd=home, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1,
        )
        self.messages = queue.Queue(maxsize=256)
        self.next_id = 0
        self.reader = threading.Thread(target=self.read_messages, daemon=True)
        self.reader.start()
        try:
            self.request("initialize", {"clientInfo": {
                "name": "ranton_fleet_account_observer", "title": "Ranton Fleet account observer", "version": "0.1.0"
            }})
            self.send({"method": "initialized", "params": {}})
        except Exception:
            self.close()
            raise

    def read_messages(self):
        try:
            for line in self.proc.stdout:
                if len(line) > 4 * 1024 * 1024:
                    break
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                try:
                    self.messages.put(message, timeout=1)
                except queue.Full:
                    break
        finally:
            try:
                self.messages.put(None, timeout=1)
            except queue.Full:
                pass

    def send(self, message):
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def request(self, method, params=None, timeout=30):
        if method not in self.allowed_methods:
            raise RuntimeError("Method is outside the read-only account observer")
        self.next_id += 1
        request_id = self.next_id
        self.send({"id": request_id, "method": method, "params": params or {}})
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("Timed out waiting for " + method)
            try:
                message = self.messages.get(timeout=remaining)
            except queue.Empty:
                raise RuntimeError("Timed out waiting for " + method)
            if message is None:
                raise RuntimeError("Account observer transport closed")
            if message.get("id") != request_id:
                # Never log notifications, raw account objects or provider errors.
                continue
            if "error" in message:
                code = message["error"].get("code")
                raise RuntimeError(method + " failed (RPC code " + str(code) + ")")
            return message.get("result") or {}

    def close(self):
        if self.proc.stdin and not self.proc.stdin.closed:
            try:
                self.proc.stdin.close()
            except OSError:
                pass
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            stop_child(self.proc)
        if self.proc.stdout:
            self.proc.stdout.close()


class LoginClient(AccountClient):
    """Managed native sign-in; no inference or externally supplied tokens."""

    allowed_methods = AccountClient.allowed_methods + ("account/login/start", "account/login/cancel")

    def await_login(self, login_id, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                message = self.messages.get(timeout=min(1, deadline - time.monotonic()))
            except queue.Empty:
                continue
            if message is None:
                raise RuntimeError("Native login transport closed")
            if message.get("method") != "account/login/completed":
                continue
            params = message.get("params") or {}
            if params.get("loginId") != login_id:
                continue
            if params.get("success") is not True:
                raise RuntimeError("Native login did not complete successfully; provider details were not retained")
            return
        raise RuntimeError("Browser sign-in timed out")


def make_login_relay(auth_url):
    # Keep the OAuth URL in memory. A private browser opens a short-lived loopback
    # redirect, so no OAuth state or PKCE parameters enter tool arguments/logs.
    parsed = urlsplit(auth_url)
    if parsed.scheme != "https" or parsed.netloc != "auth.openai.com" or parsed.path != "/oauth/authorize":
        raise RuntimeError("Native login returned an unexpected authentication destination")

    class Redirect(BaseHTTPRequestHandler):
        used = False

        def do_GET(self):
            if self.headers.get("Host") != "127.0.0.1:" + str(self.server.server_port):
                self.send_error(403)
                return
            if self.path != "/login" or Redirect.used:
                self.send_error(404)
                return
            Redirect.used = True
            self.send_response(302)
            self.send_header("Location", auth_url)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()

        def log_message(self, *_):
            pass

    return HTTPServer(("127.0.0.1", 0), Redirect)


def browser_login(alias, timeout):
    home, _, _ = accounts.codex_context(alias)
    if (home / "auth.json").exists():
        raise RuntimeError("An authentication file already exists for " + alias + "; observe before reauthentication")
    client = LoginClient(alias)
    relay = None
    login_id = None
    completed = False
    try:
        result = client.request("account/login/start", {"type": "chatgpt", "appBrand": "codex"})
        login_id = result.get("loginId")
        if result.get("type") != "chatgpt" or not login_id or not isinstance(result.get("authUrl"), str):
            raise RuntimeError("Native login did not return the expected managed browser flow")
        relay = make_login_relay(result["authUrl"])
        threading.Thread(target=relay.serve_forever, daemon=True).start()
        print("Open in a private browser for " + alias + ": http://127.0.0.1:" + str(relay.server_port) + "/login", flush=True)
        client.await_login(login_id, timeout)
        completed = True
        accounts.validate_home(home)
        if not (home / "auth.json").is_file():
            raise RuntimeError("Login completed without a persisted account credential")
    finally:
        if relay:
            relay.shutdown()
            relay.server_close()
        if login_id and not completed:
            try:
                client.request("account/login/cancel", {"loginId": login_id}, timeout=5)
            except (OSError, RuntimeError):
                pass
        client.close()
    snapshot = observe_one(alias)
    save_snapshot(snapshot)
    print(json.dumps(snapshot, indent=2), flush=True)
    return 0 if snapshot["authentication"] == "chatgpt" else 1


def identity_fingerprint(home):
    # Read only the account ID for a non-reversible comparison; never return tokens.
    auth = home / "auth.json"
    if not auth.is_file() or auth.is_symlink():
        return None
    parsed = json.loads(auth.read_text())
    account_id = (parsed.get("tokens") or {}).get("account_id")
    return hashlib.sha256(account_id.encode()).hexdigest() if isinstance(account_id, str) and account_id else None


def masked_email(email):
    if not email or "@" not in email:
        return None
    local, domain = email.rsplit("@", 1)
    return local[:1] + "***@" + domain


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def sanitize_window(window):
    if not isinstance(window, dict):
        return None
    used = window.get("usedPercent")
    duration = window.get("windowDurationMins")
    reset = window.get("resetsAt")
    return {
        "usedPercent": used if number(used) and used >= 0 else None,
        "remainingPercent": max(0, 100 - used) if number(used) and used >= 0 else None,
        "windowDurationMins": duration if number(duration) and duration > 0 else None,
        "resetsAt": reset if number(reset) and reset >= 0 else None,
    }


def sanitize_limits(result):
    buckets = result.get("rateLimitsByLimitId")
    if not isinstance(buckets, dict) or not buckets:
        legacy = result.get("rateLimits")
        buckets = {legacy.get("limitId") or "codex": legacy} if isinstance(legacy, dict) else {}
    return [{
        "limitId": str(key),
        "primary": sanitize_window(bucket.get("primary")),
        "secondary": sanitize_window(bucket.get("secondary")),
        "rateLimitReachedType": bucket.get("rateLimitReachedType"),
        "planType": bucket.get("planType"),
    } for key, bucket in buckets.items() if isinstance(bucket, dict)]


def observe_one(alias):
    home = accounts.account_home(alias)
    snapshot = {"alias": alias, "observedAt": timestamp(), "authentication": "unknown",
                "identityFingerprint": None, "maskedEmail": None, "planType": None,
                "quotaStatus": "unknown", "quotaWindows": [], "inferenceStarted": False}
    client = AccountClient(alias)
    try:
        result = client.request("account/read", {"refreshToken": False})
        account = result.get("account")
        if not isinstance(account, dict):
            snapshot["authentication"] = "signed-out"
            return snapshot
        if account.get("type") != "chatgpt":
            snapshot["authentication"] = "unsupported"
            return snapshot
        snapshot.update({"authentication": "chatgpt", "identityFingerprint": identity_fingerprint(home),
                         "maskedEmail": masked_email(account.get("email")), "planType": account.get("planType")})
        try:
            catalog = client.request("model/list", {"limit": 100})
            snapshot["models"] = [{"id": model.get("id"), "model": model.get("model"),
                                   "displayName": model.get("displayName"), "isDefault": model.get("isDefault")}
                                  for model in catalog.get("data", [])
                                  if isinstance(model, dict) and not model.get("hidden")]
        except RuntimeError:
            snapshot["models"] = []
        try:
            snapshot["quotaWindows"] = sanitize_limits(client.request("account/rateLimits/read"))
            snapshot["quotaStatus"] = "observed" if snapshot["quotaWindows"] else "unknown"
        except RuntimeError as error:
            snapshot["quotaError"] = str(error)
        snapshot["observedAt"] = timestamp()
        return snapshot
    finally:
        client.close()


def save_snapshot(snapshot):
    target = ROOT / "state" / ("account-" + snapshot["alias"] + "-observation.json")
    temporary = target.with_suffix(".tmp")
    with temporary.open("w") as handle:
        json.dump(snapshot, handle, indent=2)
        handle.write("\n")
    os.replace(temporary, target)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("login", "browser-login", "observe"))
    parser.add_argument("account", choices=accounts.ALIASES)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    if not 30 <= args.timeout <= 900:
        parser.error("timeout must be between 30 and 900 seconds")
    if args.action == "login":
        return login(args.account, args.timeout)
    if args.action == "browser-login":
        return browser_login(args.account, args.timeout)
    snapshot = observe_one(args.account)
    save_snapshot(snapshot)
    print(json.dumps(snapshot, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("Account setup interrupted; the interactive Codex account was not changed.", file=sys.stderr)
        sys.exit(130)
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        print("Account setup: " + str(error), file=sys.stderr)
        sys.exit(1)
