"""Read-only projection of this Mac's Codex projects, chats and tool activity.

Codex's local storage is an internal, versioned contract. Unsupported schemas
fail visibly rather than substituting fleet jobs. No messages, reasoning,
outputs, tool arguments, credentials or account identifiers are exported.
"""

import fcntl
from copy import deepcopy
from contextlib import closing
import json
import logging
import os
from pathlib import Path
import sqlite3
import stat
import threading
import time
import uuid

from .observability import operation_evidence, redact


class UnsafeRecordPath(OSError):
    pass


def read_error_code(error):
    if isinstance(error, UnsafeRecordPath):
        return 'unsafe_data_path'
    if isinstance(error, json.JSONDecodeError):
        return 'metadata_updating'
    if isinstance(error, sqlite3.Error):
        code = getattr(error, 'sqlite_errorcode', None)
        # Python 3.10 does not expose SQLite error codes on exceptions.
        if (isinstance(code, int) and code & 255 in (5, 6)) or str(error).lower().startswith(
                ('database is locked', 'database table is locked', 'database is busy')):
            return 'database_busy'
    return 'records_unreadable'


def valid_id(value):
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def owned_file(path):
    if path.is_symlink() or path.resolve() != path:
        raise UnsafeRecordPath('Unsafe Codex data path')
    info = path.stat()
    if info.st_uid != os.getuid() or not stat.S_ISREG(info.st_mode):
        raise UnsafeRecordPath('Codex data must belong to this user')
    return path


def read_json(path):
    owned_file(path)
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('Codex metadata exceeds the supported size')
    for attempt in range(3):
        try:
            with owned_file(path).open() as handle:
                value = json.load(handle)
            break
        except json.JSONDecodeError:
            if attempt == 2:
                raise
            time.sleep(0.03 * (attempt + 1))
    return value if isinstance(value, dict) else {}


def connect(path):
    owned_file(path)
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    return db


def writer_active(root, identifier):
    path = root / 'thread-writer-locks' / (identifier + '.lock')
    try:
        owned_file(path)
        with path.open('rb') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(handle, fcntl.LOCK_UN)
    except OSError:
        pass
    return False


def parent_id(source):
    try:
        value = json.loads(source)
        parent = value.get('subagent', {}).get('thread_spawn', {}).get('parent_thread_id')
        return parent if valid_id(parent) else ''
    except (ValueError, AttributeError, TypeError):
        return ''


def project_catalog(state, db):
    # These are the actual desktop sidebar records, including legacy IDs.
    local = state.get('local-projects', {})
    projects = []
    if isinstance(local, dict):
        for value in local.values():
            if not isinstance(value, dict):
                continue
            roots = [p for p in value.get('rootPaths', []) if isinstance(p, str) and p.startswith('/')]
            if isinstance(value.get('id'), str) and isinstance(value.get('name'), str) and roots:
                projects.append({'id': value['id'], 'name': redact(value['name'], 200),
                                 'roots': roots, 'path': roots[0]})
    # Newer clients may finish migrating projects into the state database.
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    aliases = {}
    if {'projects', 'project_roots'} <= tables:
        for row in db.execute('SELECT p.id,p.name,r.path FROM projects p JOIN project_roots r ON r.project_id=p.id ORDER BY p.position,r.position'):
            match = next((p for p in projects if row['path'] in p['roots']), None)
            if match:
                aliases[row['id']] = match['id']
            else:
                projects.append({'id': row['id'], 'name': redact(row['name'], 200),
                                 'roots': [row['path']], 'path': row['path']})
    order = state.get('project-order', [])
    order = order if isinstance(order, list) else []
    projects.sort(key=lambda p: order.index(p['id']) if p['id'] in order else len(order))
    return projects, aliases


def project_for(row, state, projects, aliases):
    ids = {p['id'] for p in projects}
    assigned = state.get('thread-project-assignments', {}).get(row['id'], {})
    assigned = assigned.get('projectId') if isinstance(assigned, dict) and assigned.get('projectKind') == 'local' else ''
    for candidate in (row.get('project_id'), assigned):
        candidate = aliases.get(candidate, candidate)
        if candidate in ids:
            return candidate
    # Root hints preserve a worktree's real project. Never match on folder name.
    path = state.get('thread-workspace-root-hints', {}).get(row['id']) or row['cwd']
    matches = [(len(root), p['id']) for p in projects for root in p['roots']
               if isinstance(path, str) and (path == root or path.startswith(root.rstrip('/') + '/'))]
    return max(matches)[1] if matches else ''


def operation(row):
    """The SQL projection deliberately excludes message/output/argument fields."""
    kind = row['item_type']
    command = ''
    if kind == 'commandExecution':
        command = operation_evidence({'type': 'command_execution', 'command': row['command']})[1]
        label = 'Command'
    elif kind == 'fileChange':
        label = 'File changes'
    elif kind == 'webSearch':
        label = 'Web lookup'
    elif kind == 'mcpToolCall' and row['server'] == 'cua_repl':
        label = 'Browser action'
    else:
        tool = row['tool'] or row['name'] or 'Tool operation'
        label = redact((row['server'] + ' · ' if row['server'] else '') + tool, 160)
    return {'id': row['item_id'], 'kind': kind, 'label': label, 'command': command,
            'state': row['status'] or ('completed' if row['completed_at_ms'] else 'unknown'),
            'exitCode': row['exit_code'], 'at': row['created_at_ms'] / 1000,
            'finished': row['completed_at_ms'] / 1000 if row['completed_at_ms'] else None}


def operation_rows(db, identifier=None, limit=120):
    restriction = ' AND thread_id=?' if identifier else ''
    values = (identifier, limit) if identifier else (limit,)
    return db.execute('''SELECT thread_id,item_id,item_type,created_at_ms,completed_at_ms,
        json_extract(item_json,'$.command') AS command,
        json_extract(item_json,'$.status') AS status,
        json_extract(item_json,'$.exitCode') AS exit_code,
        json_extract(item_json,'$.tool') AS tool,
        json_extract(item_json,'$.server') AS server,
        json_extract(item_json,'$.name') AS name
        FROM thread_items WHERE item_type IN
        ('commandExecution','mcpToolCall','dynamicToolCall','fileChange','webSearch')''' +
        restriction + ' ORDER BY created_at_ms DESC LIMIT ?', values).fetchall()


class CodexMonitor:
    def __init__(self, root=None):
        self.root = Path(root or Path.home() / '.codex').absolute()
        self.guard = threading.Lock()
        self.cached = None
        self.cached_at = 0
        self.last_verified = None

    def snapshot(self):
        with self.guard:
            if self.cached is not None and time.time() - self.cached_at < 3:
                return self.cached
            try:
                result = self._snapshot()
                result.update(stale=False, errorCode='')
                self.last_verified = result
            except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, AttributeError) as error:
                code = read_error_code(error)
                if not self.cached or self.cached.get('errorCode') != code:
                    # Never log exception text: records and parser errors may contain private data.
                    logging.getLogger(__name__).warning('Codex observation refresh failed: %s', code)
                if isinstance(error, UnsafeRecordPath):
                    self.last_verified = None
                if self.last_verified is not None:
                    result = deepcopy(self.last_verified)
                    result.update(stale=True, errorCode=code, attemptedAt=time.time(),
                                  reason='Showing last verified Codex records. Live state is unknown until refresh succeeds.')
                    for chat in result['chats']:
                        chat['state'] = 'unknown'
                    for project in result['projects']:
                        project['workingCount'] = 0
                    for operation in result.get('operations', []):
                        if operation['state'] == 'inProgress':
                            operation['state'] = 'unknown'
                else:
                    result = {'available': False, 'stale': False, 'errorCode': code,
                              'observedAt': time.time(), 'projects': [], 'chats': [], 'operations': [],
                              'reason': 'Codex local records are unavailable or their schema is unsupported'}
            self.cached, self.cached_at = result, time.time()
            return result

    def _snapshot(self):
        state_path = self.root / '.codex-global-state.json'
        state = read_json(state_path) if state_path.exists() else {}
        with closing(connect(self.root / 'state_5.sqlite')) as db:
            projects, aliases = project_catalog(state, db)
            columns = {r['name'] for r in db.execute('PRAGMA table_info(threads)')}
            required = {'id', 'source', 'cwd', 'name', 'model', 'updated_at', 'archived'}
            if not required <= columns:
                raise ValueError('Unsupported Codex thread schema')
            # Explicit columns prevent first_user_message, preview and auth IDs escaping.
            optional = ['project_id', 'originator', 'agent_nickname', 'git_branch', 'rollout_path']
            extra = ','.join(name if name in columns else 'NULL AS ' + name for name in optional)
            rows = [dict(r) for r in db.execute('SELECT id,source,cwd,name,model,updated_at,' + extra + ' FROM threads WHERE archived=0 ORDER BY updated_at DESC LIMIT 10000')]
        titles = state.get('thread-titles', {}).get('titles', {})
        chats = []
        operations = []
        history_path = self.root / 'thread_history_1.sqlite'
        history = connect(history_path) if history_path.exists() else None
        try:
            for row in rows:
                if not valid_id(row['id']):
                    continue
                parent = parent_id(row['source'])
                # Prompt previews are not titles and can contain credentials.
                name = row['name'] or titles.get(row['id']) or row['agent_nickname'] or 'Untitled chat'
                name = name if isinstance(name, str) else 'Untitled chat'
                turn = history.execute('SELECT turn_id,status,started_at,completed_at FROM thread_turns WHERE thread_id=? ORDER BY rollout_ordinal DESC LIMIT 1', (row['id'],)).fetchone() if history else None
                saved = turn['status'] if turn else 'unknown'
                active = saved == 'inProgress' and writer_active(self.root, row['id'])
                status = 'working' if active else 'unfinished' if saved == 'inProgress' else 'idle' if saved == 'completed' else saved
                chats.append({'id': row['id'], 'title': redact(name, 200),
                              'project': project_for(row, state, projects, aliases), 'parent': parent,
                              'state': status, 'savedState': saved, 'model': redact(row['model'], 100),
                              'workspace': row['cwd'], 'branch': row['git_branch'] or '',
                              'updated': row['updated_at'], 'turnStarted': turn['started_at'] if turn else None,
                              'turnFinished': turn['completed_at'] if turn else None})
            if history:
                allowed = {c['id'] for c in chats}
                operations = [{**operation(row), 'chat': row['thread_id']} for row in operation_rows(history)
                              if row['thread_id'] in allowed]
        finally:
            if history:
                history.close()
        index = {chat['id']: chat for chat in chats}
        for chat in chats:
            parent = index.get(chat['parent'])
            visited = {chat['id']}
            while parent and parent['id'] not in visited:
                visited.add(parent['id'])
                if not chat['project'] and parent['project']:
                    chat['project'] = parent['project']
                parent = index.get(parent['parent'])
        for p in projects:
            members = [c for c in chats if c['project'] == p['id']]
            p['chatCount'] = len([c for c in members if not c['parent']])
            p['workingCount'] = len([c for c in members if c['state'] == 'working'])
        return {'available': True, 'observedAt': time.time(), 'projects': projects, 'chats': chats,
                'operations': operations,
                'reason': 'Read-only local Codex records; working means an unfinished turn with an active writer. Unobserved or remote activity stays unknown.'}

    def detail(self, identifier):
        if not valid_id(identifier):
            raise ValueError('Invalid Codex chat ID')
        snapshot = self.snapshot()
        chat = next((c for c in snapshot['chats'] if c['id'] == identifier), None)
        if not chat:
            return None
        operations = [deepcopy(op) for op in snapshot.get('operations', [])
                      if op['chat'] == identifier][:60]
        stale = snapshot.get('stale', False)
        error_code = snapshot.get('errorCode', '')
        path = self.root / 'thread_history_1.sqlite'
        if not stale and path.exists():
            try:
                with closing(connect(path)) as db:
                    operations = [{**operation(row), 'chat': identifier} for row in operation_rows(db, identifier, 60)]
            except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, AttributeError) as error:
                if isinstance(error, UnsafeRecordPath):
                    raise
                stale, error_code = True, read_error_code(error)
        if stale:
            for op in operations:
                if op['state'] == 'inProgress':
                    op['state'] = 'unknown'
        return {'chat': chat, 'operations': operations,
                'children': [c for c in snapshot['chats'] if c['parent'] == identifier],
                'historyAvailable': bool(operations), 'observedAt': snapshot['observedAt'] if stale else time.time(),
                'stale': stale, 'errorCode': error_code}
