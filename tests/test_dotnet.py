"""Integration tests execute the published .NET 8 server, not the Python reference."""
import copy
import json
import os
import socket
import sqlite3
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from dbflow.common import atomic_write, digest, encoded, now
from dbflow.configuration import repository, settings, openshift
from dbflow.core import Control
from dbflow.dotnet import assembly, call
from dbflow.installer import install
from dbflow.simulation import fixture, change


class NativeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='dbflow-native-á ')
        self.root = Path(self.temp.name)
        self.cfg = fixture(self.root)
        self.cfg['gitlab']['url'] = ''
        self.save()

    def tearDown(self):
        self.temp.cleanup()

    def save(self):
        atomic_write(self.root / '.db-control/project.json', json.dumps(self.cfg, ensure_ascii=False))

    def invoke(self, name, args=None, error=False):
        result = call(self.root, 'dbflow_' + name, args or {})
        self.assertEqual(result['isError'], error, result)
        return result['structuredContent']

    def revision(self, op='r1', parent=None, number=1):
        args = {'operation_id': op, 'demand_id': 'D1', 'title': 'Ação 😀 <teste>', 'changes': [change('PRO', number), change('PRI', number)]}
        if parent:
            args['parent_revision'] = parent
        return args, self.invoke('register_revision', args)['revision_id']

    def test_python_journal_and_hash_compatibility(self):
        args = {'operation_id': 'original', 'demand_id': 'D1', 'title': 'Ação 😀 <>& \u2028', 'changes': [change('PRO')]}
        args['changes'][0]['script'] += "\r\n-- ação 😀 \" \\ <>& \u2028"
        c = Control(self.root)
        old = c.register(args)
        destination = c.destination('PRO', 'HML')
        c.close()
        self.assertEqual(self.invoke('register_revision', args)['revision_id'], old['revision_id'])
        self.assertEqual(self.invoke('status')['destinations']['PRO']['HML'], destination)
        args['operation_id'] = 'native'
        args['parent_revision'] = old['revision_id']
        args['title'] += '2'
        rid = self.invoke('register_revision', args)['revision_id']
        native = self.invoke('get_revision', {'revision_id': rid})['revision']
        content = {k: v for k, v in native.items() if k != 'revision_id'}
        self.assertEqual(rid, 'r-' + digest(content)[:24])
        c = Control(self.root)
        self.assertEqual(len(c.revisions()), 2)
        c.close()

    def test_revision_bundle_partial_confirmation_and_remap(self):
        args, r1 = self.revision()
        self.assertEqual(self.invoke('register_revision', args)['revision_id'], r1)
        b = self.invoke('prepare_bundle', {'operation_id': 'b1', 'revision_ids': [r1], 'environment': 'HML'})
        _, r2 = self.revision('r2', r1, 2)
        self.invoke('report_dispatch', {'operation_id': 'mail', 'bundle_id': b['bundle_id'], 'reference': 'User reports sent', 'sent_at': now()})
        confirm = {'operation_id': 'c1', 'revision_id': r1, 'database': 'PRO', 'environment': 'HML', 'kind': 'manual_applied', 'evidence': 'DBA informed PRO complete', 'observed_at': now(), 'change_ids': ['pro-column'], 'target_fingerprint': b['targets']['PRO']['fingerprint'], 'bundle_id': b['bundle_id']}
        self.invoke('confirm', confirm)
        state = self.invoke('status')
        self.assertEqual(state['revisions'][0]['targets']['HML'][0]['execution'], 'manual_applied')
        self.assertEqual(state['revisions'][0]['targets']['HML'][1]['execution'], 'pending')
        self.assertTrue(all(s['execution'] == 'pending' for s in state['revisions'][1]['targets']['HML']))
        self.assertTrue((Path(b['local_directory']) / 'manifest.json').exists())
        self.cfg['databases']['PRO']['environments']['HML']['instance_alias'] = 'different'
        self.save()
        self.assertEqual(self.invoke('status')['revisions'][0]['targets']['HML'][0]['execution'], 'pending')
        confirm['operation_id'] = 'c2'
        confirm.pop('bundle_id')
        self.invoke('confirm', confirm, error=True)

    def test_legacy_confirmation_and_existing_package(self):
        c = Control(self.root)
        rid = c.register({'operation_id': 'old-r', 'demand_id': 'D1', 'title': 'Legacy', 'changes': [change('PRO')]})['revision_id']
        bundle = c.prepare({'operation_id': 'old-b', 'revision_ids': [rid], 'environment': 'HML'})
        args = {'operation_id': 'old-c', 'revision_id': rid, 'database': 'PRO', 'environment': 'HML', 'kind': 'manual_applied', 'evidence': 'Original DBA confirmation', 'observed_at': now(), 'change_ids': ['pro-column'], 'target_fingerprint': bundle['targets']['PRO']['fingerprint']}
        c.confirm(args); c.close()
        self.invoke('confirm', args)
        result = self.invoke('get_bundle', {'bundle_id': bundle['bundle_id']})
        self.assertIn('manifest.json', result['preserved_local_metadata_or_drafts'])
        path = Path(bundle['local_directory'])
        script = next(path.rglob('*.sql')); script.write_text('modified script', encoding='utf-8')
        self.invoke('get_bundle', {'bundle_id': bundle['bundle_id']}, error=True)

    @unittest.skipUnless(os.name == 'nt', 'DPAPI is Windows-specific')
    def test_dpapi_local_secret_is_not_plaintext(self):
        secret = 'synthetic-test-credential-' + 'x' * 40
        result = subprocess.run(['dotnet', assembly(), '--project', str(self.root), '--set-secret', 'DBFLOW_TEST_SECRET'], input=secret + '\n', encoding='utf-8', capture_output=True, check=True)
        stored = (self.root / '.db-control/local/credentials.dpapi.json').read_text()
        self.assertNotIn(secret, stored)
        self.assertNotIn(secret, result.stdout + result.stderr)
        self.invoke('status')  # Startup must successfully unprotect under this user.

    def test_candidates_snapshot_and_abandonment(self):
        script = change('PRO')['script']
        atomic_write(self.root / 'db.sql', script)
        self.assertEqual(len(self.invoke('scan')['candidates']), 1)
        c = change('PRO')
        c.update(source_path='db.sql', source_sha256=digest(script))
        r = self.invoke('register_revision', {'operation_id': 'r', 'demand_id': 'D1', 'title': 'Test', 'changes': [c]})['revision_id']
        self.assertEqual(self.invoke('scan')['candidates'], [])
        b = self.invoke('prepare_bundle', {'operation_id': 'b', 'revision_ids': [r], 'environment': 'HML'})
        snapshot = self.invoke('record_snapshot', {'operation_id': 's', 'database': 'PRO', 'environment': 'HML', 'captured_at': now(), 'source': 'user export', 'revision_id': r, 'target_fingerprint': b['targets']['PRO']['fingerprint'], 'objects': {}, 'covered_objects': [], 'limitations': ['Partial access']})
        self.assertEqual(snapshot['comparisons'][0]['comparison'], 'inconclusive')
        self.assertFalse(snapshot['application_status_changed'])
        self.invoke('abandon_local_revision', {'operation_id': 'a', 'revision_id': r, 'reason': 'Conflict requires reconciliation'})
        self.assertTrue(self.invoke('get_bundle', {'bundle_id': b['bundle_id']})['cancelled'])
        self.invoke('report_dispatch', {'operation_id': 'd', 'bundle_id': b['bundle_id'], 'reference': 'Email', 'sent_at': now()}, error=True)

    def test_invalid_requests_do_not_add_events(self):
        args, rid = self.revision()
        args['title'] = 'changed'
        self.invoke('register_revision', args, error=True)
        args['operation_id'] = 'bad'
        args['changes'][0]['filename'] = '../escape.sql'
        self.invoke('register_revision', args, error=True)
        self.assertEqual(self.invoke('status')['total_revisions'], 1)

    def test_stdio_sdk_handshake(self):
        process = subprocess.Popen(['dotnet', assembly(), '--project', str(self.root), '--stdio'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8')
        try:
            def send(message):
                process.stdin.write(json.dumps(message) + '\n')
                process.stdin.flush()
            send({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-11-25', 'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '1'}}})
            reply = json.loads(process.stdout.readline())
            self.assertEqual(reply['result']['serverInfo']['version'], '0.2.0')
            send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
            send({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}})
            reply = json.loads(process.stdout.readline())
            self.assertIn('dbflow_register_revision', [t['name'] for t in reply['result']['tools']])
        finally:
            process.terminate()
            process.communicate(timeout=10)

    def test_installer_repository_and_deployment(self):
        repo = repository('https://gitlab.example/grupo/subgrupo/scripts.git/')
        self.assertEqual(repo['project_id'], 'grupo/subgrupo/scripts')
        cfg = settings(self.cfg, mode='remote', repo_url=repo['repository_url'], mcp_url='https://mcp.example/mcp/' + self.cfg['project_key'], auth_type='oauth_access_token')
        install(self.root, apply=True, configuration={'repo_url': repo['repository_url'], 'mode': 'remote', 'mcp_url': cfg['remote_url']})
        client = json.loads((self.root / '.mcp.json').read_text())['mcpServers']['dbflow']
        self.assertTrue(client['command'].lower().endswith(('dotnet', 'dotnet.exe')))
        self.assertIn('--stdio', client['args'])
        out = self.root / 'deployment'
        openshift(cfg, out, route_host='mcp.example', apply=True)
        resources = json.loads((out / 'deployment.json').read_text())['items']
        deployment = next(r for r in resources if r['kind'] == 'Deployment')
        self.assertEqual(deployment['spec']['replicas'], 1)
        self.assertEqual(deployment['spec']['strategy']['type'], 'Recreate')
        self.assertNotIn('Secret', [r['kind'] for r in resources])
        self.assertTrue(deployment['spec']['template']['spec']['containers'][0]['securityContext']['runAsNonRoot'])


class NativeHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='dbflow-http-native-')
        cls.root = Path(cls.temp.name)
        cls.cfg = fixture(cls.root / 'client')
        cls.cfg['gitlab']['token_env'] = 'DBFLOW_TEST_ABSENT_TOKEN'
        key = cls.cfg['project_key']
        second = copy.deepcopy(cls.cfg)
        second['project_key'] = 'other-project'
        config = {'projects': {key: cls.cfg, 'other-project': second}, 'allowed_hosts': ['127.0.0.1'], 'allowed_origins': []}
        atomic_write(cls.root / 'server.json', json.dumps(config))
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        cls.url = f'http://127.0.0.1:{port}'
        cls.token = 'simulation-' + 'x' * 40
        env = os.environ.copy()
        env.update(DBFLOW_DATA=str(cls.root / 'data'), ASPNETCORE_URLS=cls.url, DBFLOW_SERVICE_TOKENS=json.dumps({cls.token: {'actor': 'tester', 'projects': [key]}}))
        cls.logs = open(cls.root / 'server.log', 'w+', encoding='utf-8')
        cls.process = subprocess.Popen(['dotnet', assembly(), '--config', str(cls.root / 'server.json')], env=env, stdout=cls.logs, stderr=cls.logs)
        for _ in range(100):
            try:
                urllib.request.urlopen(cls.url + '/health/ready', timeout=1).close(); return
            except OSError:
                if cls.process.poll() is not None:
                    cls.logs.seek(0); raise RuntimeError(cls.logs.read())
                time.sleep(.1)
        raise RuntimeError('Server failed to start')

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate(); cls.process.wait(timeout=10); cls.logs.close(); cls.temp.cleanup()

    def request(self, tool='dbflow_status', args=None, key=None, token=None, origin=None):
        headers = {'Authorization': 'Bearer ' + (token if token is not None else self.token), 'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream', 'MCP-Protocol-Version': '2025-11-25'}
        if origin:
            headers['Origin'] = origin
        payload = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': tool, 'arguments': args or {}}}
        req = urllib.request.Request(self.url + '/mcp/' + (key or self.cfg['project_key']), data=json.dumps(payload).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=15) as res:
            text = res.read().decode()
            if res.headers.get_content_type() == 'text/event-stream':
                messages = [json.loads(line[5:].strip()) for line in text.splitlines() if line.startswith('data:')]
                return next(m for m in messages if m.get('id') == 1)
            return json.loads(text)

    def test_http_auth_scope_origin_and_result(self):
        for kwargs, status in [({'token': 'invalid'}, 401), ({'key': 'other-project'}, 403), ({'origin': 'https://attacker.invalid'}, 403)]:
            with self.assertRaises(urllib.error.HTTPError) as failure:
                self.request(**kwargs)
            self.assertEqual(failure.exception.code, status)
        data = self.request()['result']
        self.assertFalse(data['isError'], data)
        self.assertEqual(data['structuredContent']['project_key'], self.cfg['project_key'])
        self.assertTrue(self.request('dbflow_scan')['result']['isError'])

    def test_remote_client_outbox_identity_and_server_actor(self):
        cfg = copy.deepcopy(self.cfg)
        cfg.update(mode='remote', remote_url=self.url + '/mcp/' + cfg['project_key'])
        client = self.root / 'remote-client'
        atomic_write(client / '.db-control/project.json', json.dumps(cfg))
        env = os.environ.copy(); env['DBFLOW_SERVICE_TOKEN'] = self.token
        args = {'operation_id': 'remote-r1', 'demand_id': 'REMOTE-1', 'title': 'Remote simulation', 'changes': [change('PRO')]}
        def run(data):
            process = subprocess.run(['dotnet', assembly(), '--project', str(client), '--call', 'dbflow_register_revision'], input=json.dumps(data), encoding='utf-8', capture_output=True, env=env, check=True)
            return json.loads(process.stdout)
        result = run(args)
        self.assertFalse(result['isError'], result)
        rid = result['structuredContent']['revision_id']
        self.assertEqual(run(args)['structuredContent']['revision_id'], rid)
        db = sqlite3.connect(self.root / 'data' / cfg['project_key'] / '.db-control/local/journal.sqlite3')
        event = json.loads(db.execute('SELECT payload FROM events WHERE id=?', ('remote-r1',)).fetchone()[0]); db.close()
        self.assertEqual(event['actor'], 'service:tester')
        cfg['gitlab']['project_id'] = 'wrong/repository'; atomic_write(client / '.db-control/project.json', json.dumps(cfg))
        args['operation_id'] = 'wrong-repo'
        failure = run(args)
        self.assertTrue(failure['isError'])
        db = sqlite3.connect(client / '.db-control/local/journal.sqlite3')
        self.assertEqual(db.execute('SELECT state FROM requests WHERE id=?', ('wrong-repo',)).fetchone()[0], 'pending'); db.close()


if __name__ == '__main__':
    unittest.main()
