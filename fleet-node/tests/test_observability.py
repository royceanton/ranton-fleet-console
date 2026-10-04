import json
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace

from fleet.observability import gateway_event, gateway_pick, operation_evidence, redact
from fleet.store import Store
from test_fleet import NOW, observation


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name).resolve()
        self.fleet = SimpleNamespace(runtime=root, store=Store(root/'state.sqlite'), guard=threading.RLock(), active={})
        self.binding = root/'gateway-accounts.json'
        self.binding.write_text(json.dumps({a: {'id': a+'-id', 'identityFingerprint': a} for a in ('harith', 'jill')}))
        self.binding.chmod(0o600)
        self.fleet.store.observe('harith', observation('harith'))
        self.fleet.store.observe('jill', observation('jill', NOW+7200))
        self.request = {'provider': 'codex', 'session': 'one', 'model': 'test', 'candidates': [{'id': a+'-id', 'provider': 'codex'} for a in ('harith', 'jill')]}

    def tearDown(self):
        self.tmp.cleanup()

    def pick(self, **changes):
        return gateway_pick(self.fleet, {**self.request, **changes}, NOW)

    def test_durable_affinity_parent_and_separate_client_scope(self):
        self.assertEqual(self.pick()['auth_id'], 'harith-id')
        self.fleet.store.observe('jill', observation('jill', NOW+10))
        self.fleet.store = Store(self.fleet.runtime/'state.sqlite')
        self.assertEqual(self.pick()['auth_id'], 'harith-id')
        self.assertEqual(self.pick(session='child', parent='one')['auth_id'], 'harith-id')
        self.assertEqual(self.pick(scope='other-client')['auth_id'], 'jill-id')

    def test_bound_account_cannot_fail_over_after_quota_or_candidate_loss(self):
        self.pick()
        depleted = observation('harith')
        depleted['quotaWindows'][0]['primary']['remainingPercent'] = 0
        self.fleet.store.observe('harith', depleted)
        self.assertTrue(self.pick()['reject'])
        self.assertTrue(self.pick(candidates=[{'id': 'jill-id', 'provider': 'codex'}])['reject'])
        self.assertEqual(self.pick(session='fresh')['auth_id'], 'jill-id')

    def test_stale_missing_identity_and_malformed_bindings_stop_safely(self):
        self.assertTrue(self.pick(session='')['reject'])
        self.assertTrue(gateway_pick(self.fleet, self.request, NOW+301)['reject'])
        self.binding.write_text('[]')
        self.assertTrue(self.pick()['reject'])
        self.binding.write_text(json.dumps({'harith': {'id': 'harith-id', 'identityFingerprint': 'changed'}}))
        self.assertTrue(self.pick()['reject'])

    def test_journal_records_lifecycle_without_payload_or_output(self):
        for state in ('running', 'succeeded'):
            gateway_event(self.fleet, {'id':'request', 'state':state, 'model':'test', 'statusCode':200, 'body':'PRIVATE', 'error':'PRIVATE'})
        records = self.fleet.store.operations()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['state'], 'succeeded')
        self.assertIsNotNone(records[0]['finished'])
        self.assertNotIn('PRIVATE', json.dumps(records))
        kind, command = operation_evidence({'type':'command_execution', 'command':'curl -H "Authorization: Bearer abc123" x', 'aggregated_output':'PRIVATE'})
        self.assertNotIn('abc123', command)
        self.assertNotIn('PRIVATE', command)
        self.assertEqual(operation_evidence({'type':'command_execution', 'command':'cat auth.json'})[1], '[Sensitive command details withheld]')
        self.assertNotIn('secret-password', redact('https://user:secret-password@example.test'))

    def test_operation_runs_and_unfinished_tasks_are_not_lost(self):
        store = self.fleet.store
        project = store.add_project('test', str(self.fleet.runtime), False, False)
        task = store.submit({'title':'old', 'goal':'read', 'project':project['id'], 'mode':'read-only', 'priority':10, 'timeout':60, 'idempotency':'old'})
        for n in range(305):
            entry = store.submit({'title':str(n), 'goal':'read', 'project':project['id'], 'mode':'read-only', 'priority':10, 'timeout':60, 'idempotency':str(n)})
            store.update(entry['id'], state='completed')
        self.assertIn(task['id'], [entry['id'] for entry in store.tasks()])
        store.operation(task['id'], 'turn1:item_0', 'command_execution', 'ls', 'succeeded', 0)
        store.operation(task['id'], 'turn2:item_0', 'command_execution', 'pwd', 'running')
        store.finish_operations(task['id'])
        self.assertEqual(len(store.operations(task['id'])), 2)
        self.assertEqual(store.operations(task['id'])[0]['state'], 'interrupted')
