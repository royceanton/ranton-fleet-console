#!/usr/bin/env python3
"""Deploy the private console and scheduler on an already bootstrapped Mac.

Uses existing account sign-ins, backs up changed configuration, never logs keys.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()
PROXY = HOME/'.local/share/ranton-proxy'
RUNTIME = HOME/'.local/share/ranton-console'
FLEET = HOME/'.local/share/codex-fleet/runtime'
LABEL = 'com.ranton.console'
DOMAIN = 'gui/' + str(os.getuid())
PLIST = HOME/'Library/LaunchAgents'/ (LABEL+'.plist')


def backup(path):
    if path.is_symlink():
        raise RuntimeError('Symlinked deployment path refused')
    if path.exists():
        target = RUNTIME/(path.name+'-before-'+datetime.datetime.now().strftime('%Y%m%dT%H%M%S%f'))
        shutil.copy2(path, target)
        target.chmod(0o600)
        print('Backup: '+str(target))


def request(port, path):
    key = (PROXY/'management.key').read_text().strip()
    req = urllib.request.Request('http://127.0.0.1:'+str(port)+path, headers={'Authorization':'Bearer '+key})
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.load(response)


def bind_accounts():
    entries = request(8317, '/v8/management/credentials').get('files', [])
    db = sqlite3.connect('file:'+str(FLEET/'fleet.sqlite3')+'?mode=ro', uri=True)
    try:
        snapshots = {row[0]:json.loads(row[1]) for row in db.execute('SELECT alias,snapshot FROM accounts')}
    finally:
        db.close()
    result = {}
    for alias in ('harith', 'jill'):
        expected = snapshots.get(alias, {}).get('identityFingerprint')
        matches = []
        for entry in entries:
            account = (entry.get('id_token') or {}).get('chatgpt_account_id')
            if entry.get('provider') == 'codex' and isinstance(account, str) and expected and hashlib.sha256(account.encode()).hexdigest() == expected:
                matches.append(entry)
        if len(matches) != 1:
            raise RuntimeError('Exactly one verified proxy identity is required for '+alias)
        result[alias] = {'id':matches[0]['id'], 'authIndex':matches[0]['auth_index'], 'identityFingerprint':expected}
    target = FLEET/'gateway-accounts.json'
    backup(target)
    target.write_text(json.dumps(result, indent=2)+'\n')
    target.chmod(0o600)
    print('Verified both proxy accounts against native Codex identities')


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--coordinator-root', type=Path, required=True)
    args = parser.parse_args()
    RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
    if RUNTIME.is_symlink() or RUNTIME.stat().st_uid != os.getuid() or RUNTIME.stat().st_mode & 0o077:
        raise RuntimeError('Console runtime must be owner-only')
    asset = ROOT/'dist/index.html'
    if not asset.is_file():
        raise RuntimeError('Run bun run verify before deploying')
    bind_accounts()
    directory = RUNTIME/'plugins/darwin/arm64'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    plugin = directory/'ranton-router.dylib'
    backup(plugin)
    staged_plugin = plugin.with_suffix('.next')
    subprocess.run(['clang','-fobjc-arc','-dynamiclib','-framework','Foundation',str(ROOT/'fleet-node/scheduler.m'),'-o',str(staged_plugin)], check=True)
    staged_plugin.chmod(0o600)
    staged_plugin.replace(plugin)
    config = PROXY/'config.yaml'
    parsed = subprocess.run(['bun','-e',"import YAML from 'yaml'; import fs from 'node:fs'; process.stdout.write(JSON.stringify(YAML.parse(fs.readFileSync(process.argv[1],'utf8'))));",str(config)], cwd=ROOT, capture_output=True, check=True)
    document = json.loads(parsed.stdout)
    document['plugins'] = {**document.get('plugins', {}), 'enabled':True, 'dir':str(RUNTIME/'plugins'), 'configs':{**document.get('plugins', {}).get('configs', {}), 'ranton-router':{'enabled':True,'priority':100}}}
    backup(config)
    config.write_text(json.dumps(document, indent=2)+'\n')
    config.chmod(0o600)
    target = RUNTIME/'management.html'
    backup(target)
    shutil.copy2(asset, target)
    target.chmod(0o600)
    payload = plistlib.dumps({'Label':LABEL,'ProgramArguments':[str(Path(sys.executable).resolve()), str(ROOT/'fleet-node/console_bridge.py')], 'WorkingDirectory':str(ROOT),'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':10,'ProcessType':'Background', 'EnvironmentVariables':{'HOME':str(HOME),'PATH':os.environ['PATH'],'PYTHONUNBUFFERED':'1'}, 'StandardOutPath':str(RUNTIME/'service.log'), 'StandardErrorPath':str(RUNTIME/'service-error.log')})
    backup(PLIST)
    if PLIST.exists():
        subprocess.run(['launchctl','bootout',DOMAIN+'/'+LABEL], capture_output=True)
    PLIST.write_bytes(payload)
    PLIST.chmod(0o600)
    # bootout can return before launchd completes deregistration.
    for attempt in range(10):
        loaded = subprocess.run(['launchctl','bootstrap',DOMAIN,str(PLIST)], capture_output=True)
        if loaded.returncode == 0:
            break
        time.sleep(0.25)
    else:
        raise RuntimeError('Console LaunchAgent did not load')
    subprocess.run([sys.executable,str(args.coordinator_root/'scripts/43_fleet_service.py'),'restart'],check=True)
    subprocess.run([sys.executable,str(args.coordinator_root/'scripts/44_proxy_service.py'),'restart'],check=True)
    print('Deployed: console 127.0.0.1:8318; gateway 127.0.0.1:8317; coordinator 127.0.0.1:8765')
    print('Verify local health and plugin registration before updating Tailscale Serve')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # Native/provider exception text can contain account data or config.
        print('Deployment stopped. Inspect the named phase and retain configuration backups.', file=sys.stderr)
        raise SystemExit(1)
