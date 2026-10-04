"""Redacted worker evidence and fail-closed, durable gateway account affinity."""

import hashlib
import json
import os
import stat
import re
import time

from .router import assess, choose


def redact(value, limit=4000):
    text = str(value or '')
    text = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', text)
    text = re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', text)
    text = re.sub(r'(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+', r'\1[redacted]', text)
    text = re.sub(r'\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9_]{10,}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\b', '[redacted]', text)
    text = re.sub(r'(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|authorization)\s*[=:]\s*)[^\s,;]+', r'\1[redacted]', text)
    text = re.sub(r'(https?://)[^\s/@]+:[^\s/@]+@', r'\1[redacted]@', text)
    return text[:limit]


def operation_evidence(item):
    kind = item.get('type')
    if kind == 'command_execution':
        command = str(item.get('command') or 'Command')
        if re.search(r'(?i)(auth\.json|\.env(?:\W|$)|private[_-]?key|keychains|management\.key|client\.key|BEGIN .*PRIVATE KEY)', command):
            command = '[Sensitive command details withheld]'
        return kind, redact(command)
    if kind == 'file_change':
        paths = [redact(change.get('path'), 500) for change in (item.get('changes') or []) if isinstance(change, dict)]
        return kind, '\n'.join(paths)[:4000] or 'Worktree file changes'
    if kind in ('web_search', 'mcp_tool_call'):
        # Queries, arguments and results can contain private project content.
        return kind, 'Documentation lookup' if kind == 'web_search' else 'Tool operation'
    return None


def routing_choices(fleet, now=None):
    now = time.time() if now is None else now
    observations = fleet.store.observations()
    with fleet.guard:
        active = {worker.task['account']: 1 for worker, thread in fleet.active.values() if thread.is_alive()}
    candidates = []
    for alias in ('harith', 'jill'):
        snapshot = observations.get(alias, {})
        eligibility = assess(snapshot, now, fleet.store.setting('reservePercent'), cooldown=snapshot.get('cooldownUntil', 0))
        if active.get(alias):
            eligibility = {**eligibility, 'eligible': False, 'reason': 'Account already has an active task'}
        windows = []
        for bucket in snapshot.get('quotaWindows') or []:
            for key in ('primary', 'secondary'):
                if bucket.get(key):
                    windows.append(bucket[key])
        candidates.append({'account': alias, **eligibility, 'windows': windows})
    return sorted(candidates, key=lambda entry: (not entry['eligible'], entry['reset'] or float('inf'), entry['account']))


def gateway_pick(fleet, data, now=None):
    """Only the plugin's filtered, available Codex candidates can be selected.

    Gateway model entitlement is checked by CLIProxyAPI before invoking us.
    Quota/auth health and account affinity are checked here, with no retries or
    resets. A client must supply a stable session ID. Bindings never silently
    expire or change account; failed requests retain the same binding.
    """
    now = time.time() if now is None else now
    if data.get('provider') != 'codex':
        raise ValueError('Gateway routing supports the configured Codex accounts only')
    session = data.get('session')
    candidates = data.get('candidates')
    if not isinstance(session, str) or not 1 <= len(session) <= 512:
        return {'handled': True, 'reject': True, 'reject_code': 'session_required',
                'reject_reason': 'Provide a stable session_id header for quota-aware routing'}
    if not isinstance(candidates, list) or len(candidates) > 100:
        raise ValueError('Invalid gateway candidates')
    caller = data.get('scope') or 'owner'
    if not isinstance(caller, str) or len(caller) > 100:
        raise ValueError('Invalid caller scope')
    namespace = 'codex:' + caller + ':'
    scope = hashlib.sha256((namespace + session).encode()).hexdigest()
    binding_path = fleet.runtime / 'gateway-accounts.json'
    try:
        from .worker import sessions
        sessions.accounts.reject_symlinks(binding_path)
        metadata = binding_path.stat()
        if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise ValueError('Invalid binding permissions')
        bindings = json.loads(binding_path.read_text())
        if not isinstance(bindings, dict):
            bindings = {}
    except (OSError, ValueError, RuntimeError):
        bindings = {}
    candidate_ids = {entry.get('id') for entry in candidates if isinstance(entry, dict)
                     and isinstance(entry.get('id'), str) and entry.get('provider') == 'codex'}
    with fleet.guard:
        observations = fleet.store.observations()
        available = {}
        for alias, binding in bindings.items():
            if not isinstance(binding, dict):
                continue
            snapshot = observations.get(alias, {})
            if (alias in ('harith', 'jill') and binding.get('id') in candidate_ids
                    and binding.get('identityFingerprint') == snapshot.get('identityFingerprint')):
                available[alias] = snapshot
        active = {worker.task['account']: 1 for worker, thread in fleet.active.values() if thread.is_alive()}
        with fleet.store.connect() as db:
            old = db.execute('SELECT account FROM gateway_bindings WHERE scope=?', (scope,)).fetchone()
            parent = data.get('parent')
            if not old and isinstance(parent, str) and 0 < len(parent) <= 512:
                parent_scope = hashlib.sha256((namespace + parent).encode()).hexdigest()
                old = db.execute('SELECT account FROM gateway_bindings WHERE scope=?', (parent_scope,)).fetchone()
            alias, reason = choose(available, {'account': old['account'] if old else None}, active, now, fleet.store.setting('reservePercent'))
            if alias:
                db.execute('INSERT INTO gateway_bindings VALUES(?,?,?,?) ON CONFLICT(scope) DO UPDATE SET touched=excluded.touched', (scope, alias, now, now))
        fleet.store.event(None, 'gateway_route', json.dumps({'account': alias, 'session': scope[:12],
            'model': redact(data.get('model'), 100), 'reason': reason, 'continued': bool(old)}))
        if not alias:
            return {'handled': True, 'reject': True, 'reject_code': 'quota_admission_wait',
                    'reject_reason': reason or 'No eligible account; wait for fresh available quota'}
        return {'handled': True, 'auth_id': bindings[alias]['id']}


def gateway_event(fleet, data):
    identifier, state = data.get('id'), data.get('state')
    if not isinstance(identifier, str) or not 1 <= len(identifier) <= 512 or state not in ('running', 'succeeded', 'failed', 'rejected', 'canceled'):
        raise ValueError('Invalid gateway lifecycle event')
    item = hashlib.sha256(identifier.encode()).hexdigest()[:24]
    code = data.get('statusCode')
    fleet.store.operation('gateway', item, 'gateway_request', redact(data.get('model'), 100), state,
                          code if type(code) is int and 0 <= code <= 599 else None)
    return {'recorded': True}


def public_observation(fleet):
    status = fleet.status()
    tasks = []
    for task in status['tasks']:
        tasks.append({key: redact(value, 4000) if isinstance(value, str) else value
                      for key, value in task.items() if key not in ('goal', 'result', 'pending_prompt')})
    status['tasks'] = tasks
    status['operations'] = fleet.store.operations()
    status['routing'] = {'strategy': 'earliest-reset', 'affinity': 'Persistent account and session',
                         'freshnessSeconds': 300, 'choices': routing_choices(fleet),
                         'gatewayConfigured': (fleet.runtime / 'gateway-accounts.json').is_file()}
    status['coverage'] = 'Fleet-managed tasks and CLIProxyAPI gateway traffic. Command output and unrelated Mac activity are not recorded.'
    return status
