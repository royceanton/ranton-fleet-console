import fcntl
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fleet.codex_monitor import CodexMonitor, writer_active

CHAT='11111111-2222-4333-8444-555555555555'
CHILD='11111111-2222-4333-8444-555555555556'

class CodexMonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name).resolve()
        self.state=self.root/'.codex-global-state.json'
        self.state.write_text(json.dumps({
            'local-projects':{'real':{'id':'real','name':'Real app project','rootPaths':['/projects/real']}},
            'project-order':['real'],
            'thread-project-assignments':{CHAT:{'projectKind':'local','projectId':'real'}},
            'thread-titles':{'titles':{CHAT:'Exact app title'}},
            'credential':'DO_NOT_EXPORT',
        }))
        with sqlite3.connect(self.root/'state_5.sqlite') as db:
            db.execute('CREATE TABLE threads(id TEXT,source TEXT,cwd TEXT,name TEXT,model TEXT,updated_at INTEGER,archived INTEGER,project_id TEXT,first_user_message TEXT)')
            db.execute('INSERT INTO threads VALUES(?,?,?,?,?,?,?,?,?)',(CHAT,'appServer','/worktrees/w1',None,'test-model',100,0,None,'PASSWORD_DO_NOT_EXPORT'))
            db.execute('INSERT INTO threads VALUES(?,?,?,?,?,?,?,?,?)',(CHILD,json.dumps({'subagent':{'thread_spawn':{'parent_thread_id':CHAT}}}),'/worktrees/w1','Agent title','test-model',101,0,None,'DO_NOT_EXPORT'))
        with sqlite3.connect(self.root/'thread_history_1.sqlite') as db:
            db.execute('CREATE TABLE thread_turns(thread_id TEXT,turn_id TEXT,status TEXT,started_at INTEGER,completed_at INTEGER,rollout_ordinal INTEGER)')
            db.execute('INSERT INTO thread_turns VALUES(?,?,?,?,?,?)',(CHAT,'turn','inProgress',99,None,1))
            db.execute('CREATE TABLE thread_items(thread_id TEXT,item_id TEXT,item_type TEXT,item_json TEXT,created_at_ms INTEGER,completed_at_ms INTEGER)')
            for number,value in enumerate([
                {'type':'commandExecution','command':'cat auth.json','status':'completed','exitCode':0,'aggregatedOutput':'DO_NOT_EXPORT'},
                {'type':'commandExecution','command':'curl -H "Authorization: Bearer PRIVATE_TOKEN" example.test','status':'completed'},
                {'type':'agentMessage','text':'DO_NOT_EXPORT'},
                {'type':'mcpToolCall','tool':'search_docs','arguments':'DO_NOT_EXPORT','result':'DO_NOT_EXPORT'},
            ]):
                db.execute('INSERT INTO thread_items VALUES(?,?,?,?,?,?)',(CHAT,str(number),value['type'],json.dumps(value),number*1000,number*1000+1))
        self.monitor=CodexMonitor(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_real_projects_titles_and_parent_membership_are_read_only(self):
        before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.iterdir()}
        with patch('fleet.codex_monitor.writer_active',return_value=True):
            snapshot=self.monitor.snapshot()
        self.assertTrue(snapshot['available'])
        self.assertEqual(snapshot['projects'][0]['name'],'Real app project')
        chats={c['id']:c for c in snapshot['chats']}
        self.assertEqual(chats[CHAT]['title'],'Exact app title')
        self.assertEqual(chats[CHAT]['project'],'real')
        self.assertEqual(chats[CHILD]['project'],'real')
        self.assertEqual(chats[CHAT]['state'],'working')
        self.assertEqual(chats[CHILD]['state'],'unknown')
        after={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.iterdir()}
        self.assertEqual(before,after)

    def test_dead_saved_turn_is_not_claimed_as_running_and_secrets_are_not_exported(self):
        snapshot=self.monitor.snapshot()
        self.assertEqual(next(c for c in snapshot['chats'] if c['id']==CHAT)['state'],'unfinished')
        detail=self.monitor.detail(CHAT)
        self.assertEqual(len(detail['operations']),3)
        self.assertEqual(detail['children'][0]['id'],CHILD)
        text=json.dumps(snapshot)+json.dumps(detail)
        self.assertNotIn('DO_NOT_EXPORT',text)
        self.assertNotIn('PRIVATE_TOKEN',text)
        self.assertNotIn('cat auth.json',text)
        self.assertNotIn('first_user_message',text)
        self.assertNotIn('aggregatedOutput',text)

    def test_missing_unsupported_and_symlinked_data_fail_without_creating_it(self):
        missing=self.root/'missing'
        self.assertFalse(CodexMonitor(missing).snapshot()['available'])
        self.assertFalse(missing.exists())
        with sqlite3.connect(self.root/'state_5.sqlite') as db:
            db.execute('DROP TABLE threads')
        self.assertFalse(CodexMonitor(self.root).snapshot()['available'])
        self.state.unlink()
        self.state.symlink_to('/etc/hosts')
        self.assertFalse(CodexMonitor(self.root).snapshot()['available'])
        for identifier in ('../auth.json','not-a-chat',''):
            with self.assertRaises(ValueError): self.monitor.detail(identifier)

    def test_newer_project_ids_map_to_actual_sidebar_roots(self):
        with sqlite3.connect(self.root/'state_5.sqlite') as db:
            db.execute('CREATE TABLE projects(id TEXT,name TEXT,position INTEGER)')
            db.execute('CREATE TABLE project_roots(project_id TEXT,path TEXT,position INTEGER)')
            db.execute('INSERT INTO projects VALUES(?,?,?)',('migrated','Real app project',0))
            db.execute('INSERT INTO project_roots VALUES(?,?,?)',('migrated','/projects/real',0))
            db.execute('UPDATE threads SET project_id=? WHERE id=?',('migrated',CHAT))
        snapshot=self.monitor.snapshot()
        self.assertEqual(len(snapshot['projects']),1)
        self.assertEqual(next(c for c in snapshot['chats'] if c['id']==CHAT)['project'],'real')

    def test_writer_probe_never_creates_a_lock(self):
        self.assertFalse(writer_active(self.root,CHAT))
        self.assertFalse((self.root/'thread-writer-locks').exists())

    def test_temporary_metadata_failure_retains_history_without_claiming_live_state(self):
        with patch('fleet.codex_monitor.writer_active', return_value=True):
            verified=self.monitor.snapshot()
        self.monitor.cached_at=0
        with patch('fleet.codex_monitor.read_json', side_effect=json.JSONDecodeError('Updating','',0)):
            stale=self.monitor.snapshot()
        self.assertTrue(stale['available'])
        self.assertTrue(stale['stale'])
        self.assertEqual(stale['observedAt'],verified['observedAt'])
        self.assertEqual(stale['errorCode'],'metadata_updating')
        self.assertEqual([p['id'] for p in stale['projects']],['real'])
        self.assertEqual(len(stale['chats']),2)
        self.assertTrue(all(c['state']=='unknown' for c in stale['chats']))
        self.assertEqual(stale['projects'][0]['workingCount'],0)
        self.assertEqual(next(c for c in verified['chats'] if c['id']==CHAT)['state'],'working')
        self.monitor.cached_at=0
        recovered=self.monitor.snapshot()
        self.assertFalse(recovered['stale'])
        self.assertEqual(recovered['errorCode'],'')

    def test_incomplete_metadata_read_is_retried_on_first_observation(self):
        metadata=json.loads(self.state.read_text())
        with patch('fleet.codex_monitor.json.load', side_effect=[json.JSONDecodeError('Updating','',0),metadata]), patch('fleet.codex_monitor.time.sleep'):
            snapshot=self.monitor.snapshot()
        self.assertTrue(snapshot['available'])
        self.assertFalse(snapshot['stale'])
        self.assertEqual(snapshot['projects'][0]['name'],'Real app project')

    def test_sqlite_write_lock_does_not_erase_verified_projects(self):
        verified=self.monitor.snapshot()
        self.monitor.cached_at=0
        with sqlite3.connect(self.root/'state_5.sqlite') as writer:
            writer.execute('BEGIN EXCLUSIVE')
            try:
                stale=self.monitor.snapshot()
            finally:
                writer.rollback()
        self.assertTrue(stale['available'])
        self.assertTrue(stale['stale'])
        self.assertEqual(stale['errorCode'],'database_busy')
        self.assertEqual(stale['observedAt'],verified['observedAt'])
        self.assertEqual(len(stale['projects']),1)

    def test_past_completed_chats_are_visible_without_an_active_writer(self):
        with sqlite3.connect(self.root/'thread_history_1.sqlite') as db:
            db.execute("UPDATE thread_turns SET status='completed',completed_at=100")
        with patch('fleet.codex_monitor.writer_active', return_value=False):
            snapshot=self.monitor.snapshot()
        self.assertTrue(snapshot['available'])
        self.assertEqual(next(c for c in snapshot['chats'] if c['id']==CHAT)['state'],'idle')
        self.assertEqual(snapshot['projects'][0]['chatCount'],1)

    def test_unsafe_path_cannot_reuse_cached_records(self):
        self.assertTrue(self.monitor.snapshot()['available'])
        self.monitor.cached_at=0
        self.state.unlink()
        self.state.symlink_to('/etc/hosts')
        snapshot=self.monitor.snapshot()
        self.assertFalse(snapshot['available'])
        self.assertEqual(snapshot['errorCode'],'unsafe_data_path')
        self.assertEqual(snapshot['projects'],[])

    def test_chat_history_write_lock_retains_verified_operation_evidence(self):
        self.monitor.snapshot()
        with sqlite3.connect(self.root/'thread_history_1.sqlite') as writer:
            writer.execute('BEGIN EXCLUSIVE')
            try:
                detail=self.monitor.detail(CHAT)
            finally:
                writer.rollback()
        self.assertTrue(detail['stale'])
        self.assertEqual(detail['errorCode'],'database_busy')
        self.assertEqual(len(detail['operations']),3)
        self.assertNotIn('DO_NOT_EXPORT',json.dumps(detail))

if __name__=='__main__':unittest.main()
