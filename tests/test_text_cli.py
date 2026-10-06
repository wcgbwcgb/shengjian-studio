import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import claude_cli, config, jobs, store, text_cli
from app.main import app

FAKE = Path(__file__).with_name('fake_text_claude.py')


class SubscriptionTextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = patch.object(store, 'DATA', Path(self.temp.name));self.data.start()
        self.env = patch.dict('os.environ', {'ANTHROPIC_API_KEY': 'must-not-be-inherited', 'TEXT_CLI_MODE': 'success', 'TEXT_CLI_AUTH': 'claude.ai'});self.env.start()
        self.command = patch.object(claude_cli, 'cli_command', return_value=[sys.executable, str(FAKE)]);self.command.start()
        self.context = TestClient(app);self.client = self.context.__enter__()
        text_cli.save({'engine': 'claude_cli'})
        self.project = self.client.post('/api/projects', json={'name': '订阅流程'}).json()
        self.url = '/api/projects/' + self.project['id']

    def tearDown(self):
        self.context.__exit__(None, None, None)
        self.command.stop();self.env.stop();self.data.stop();self.temp.cleanup()

    def wait(self, task):
        for _ in range(160):
            current = store.get('task', task['id'])
            if current['status'] not in ('queued', 'running', 'cancelling'):
                return current
            time.sleep(.05)
        self.fail('CLI task did not finish')

    def submit(self, kind, **payload):
        response = self.client.post(self.url + '/tasks', json={'kind': kind, 'prompt': '订阅任务', **payload})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_subscription_research_evidence_and_script_rewrite(self):
        task = self.wait(self.submit('research', workspace=True))
        self.assertEqual(task['status'], 'completed', task.get('error'))
        research = store.get('version', task['output']['version_id'])
        by_url = {s['url']: s['verification'] for s in research['result']['sources']}
        self.assertEqual(list(by_url.values()), ['fetched', 'search_only', 'unverified'])
        self.client.post(self.url + '/versions/' + research['id'] + '/adopt', json={'topic_id': 'topic'})
        script_task = self.wait(self.submit('script', workspace=True))
        self.assertEqual(script_task['status'], 'completed', script_task.get('error'))
        script = store.get('version', script_task['output']['version_id'])
        response = self.client.post(self.url + '/workspace/rewrite', json={'version_id': script['id'], 'paragraph_id': 'hook', 'instruction': '更自然', 'model': 'sonnet'})
        rewritten = self.wait(response.json())
        self.assertEqual(rewritten['status'], 'completed', rewritten.get('error'))
        paragraphs = store.get('version', rewritten['output']['version_id'])['result']['paragraphs']
        self.assertEqual([p['text'] for p in paragraphs], ['订阅局部改写', '保留的结尾'])
        self.assertEqual(store.listing('usage'), [])
        received = json.loads((store.project_dir(self.project['id']) / 'text-tasks' / task['id'] / 'received.json').read_text(encoding='utf-8'))
        self.assertFalse(received['has_api_key'])
        self.assertIn('WebSearch,WebFetch', received['argv'])
        self.assertNotIn('Read,Write,Edit,Glob,Grep,Bash', received['argv'])
        self.assertTrue(task['cli_result']['reported_cost_usd'])

    def test_cli_receives_object_schema_for_research_script_and_angles(self):
        for mode, field in [('research', 'topics'), ('script', 'paragraphs'), ('angles', 'angles')]:
            with self.subTest(mode=mode):
                task = self.wait(self.submit(mode))
                self.assertEqual(task['status'], 'completed', task.get('error'))
                received = json.loads((store.project_dir(self.project['id']) / 'text-tasks' / task['id'] / 'received.json').read_text(encoding='utf-8'))
                argv = received['argv']
                sent = json.loads(argv[argv.index('--json-schema') + 1])
                self.assertEqual(sent['type'], 'object')
                self.assertTrue(set(sent).isdisjoint({'oneOf', 'allOf', 'anyOf'}))
                self.assertEqual(sent['properties'][field]['type'], 'array')
                self.assertEqual(sent['properties']['needs_clarification']['type'], 'string')
                self.assertEqual(sent['properties']['advice']['type'], 'string')
                result = store.get('version', task['output']['version_id'])['result']
                self.assertIn(field, result)

    def test_text_messages_are_supported_and_empty_results_are_rejected(self):
        for mode in ('advice', 'clarification', 'empty'):
            with self.subTest(mode=mode), patch.dict('os.environ', {'TEXT_CLI_MODE': mode}):
                task = self.wait(self.submit('script'))
                if mode == 'empty':
                    self.assertEqual(task['status'], 'failed')
                    self.assertIn('结构化', task['error'])
                else:
                    self.assertEqual(task['status'], 'completed', task.get('error'))
                    self.assertIn('conversation', task['output'])
                self.assertEqual(store.listing('version', self.project['id']), [])

    def test_creation_without_api_key_automatically_uses_subscription(self):
        with patch.dict('os.environ', {'ANTHROPIC_API_KEY': ''}):
            result = self.client.post('/api/creations', json={'idea': '从订阅调研开始'}).json()
            self.assertFalse(result['needs_connection'])
            completed = self.wait(result['task'])
        self.assertEqual(completed['status'], 'completed', completed.get('error'))
        self.assertEqual(completed['model_config']['billing'], 'subscription')
        self.assertIsNone(store.task_credentials(completed['id']))

    def test_retry_keeps_subscription_model_and_options_after_global_switch(self):
        with patch.object(jobs.QUEUE, 'put'):
            task = self.submit('script', model='opus')
            task['status'] = 'failed';store.put('task', task, self.project['id'])
            text_cli.save({'engine': 'api', 'models': {'script': 'haiku'}})
            claude_cli.save({'model': 'haiku', 'timeout_sec': 60})
            retry = self.client.post('/api/tasks/' + task['id'] + '/retry', json={}).json()
            self.assertEqual(retry['model_config'], task['model_config'])
            self.assertEqual(retry['cli_config'], task['cli_config'])
            self.assertEqual(retry['cli_config']['model'], 'opus')
            retry['status'] = 'cancelled';store.put('task', retry, self.project['id'])

    def test_console_login_is_rejected_without_api_fallback(self):
        with patch.dict('os.environ', {'TEXT_CLI_AUTH': 'api_key'}):
            task = self.wait(self.submit('script'))
        self.assertEqual(task['status'], 'failed')
        self.assertIn('订阅账号', task['error'])
        self.assertEqual(store.listing('usage'), [])

    def test_invalid_result_and_cancellation_do_not_create_versions(self):
        with patch.dict('os.environ', {'TEXT_CLI_MODE': 'invalid'}):
            task = self.wait(self.submit('script'))
        self.assertEqual(task['status'], 'failed')
        self.assertIn('结构化', task['error'])
        with patch.dict('os.environ', {'TEXT_CLI_MODE': 'hang'}):
            task = self.submit('script')
            for _ in range(80):
                if store.get('task', task['id']).get('cli_session_id'):
                    break
                time.sleep(.05)
            self.client.post('/api/tasks/' + task['id'] + '/cancel', json={})
            task = self.wait(task)
        self.assertEqual(task['status'], 'cancelled')
        self.assertEqual(store.listing('version', self.project['id']), [])

    def test_subscription_models_templates_and_independent_settings(self):
        original = store.settings()
        self.assertEqual(self.client.put('/api/settings/text-service', json={'engine': 'claude_cli', 'models': {'script': 'opus'}}).status_code, 200)
        self.assertEqual(store.settings(), original)
        result = self.client.get('/api/settings').json()
        self.assertEqual(result['defaults']['model_script'], 'opus')
        self.assertEqual([m['id'] for m in result['models']], ['sonnet', 'opus', 'haiku'])
        self.assertEqual(self.client.post('/api/templates', json={'name': '订阅模板', 'stage': 'script', 'model': 'sonnet'}).status_code, 200)
        self.assertEqual(self.client.post(self.url + '/tasks', json={'kind': 'script', 'model': 'gpt-6-luna'}).status_code, 400)
        self.assertEqual(self.client.put('/api/settings/text-service', json={'models': {'script': 'gpt-6-luna'}}).status_code, 400)
        with patch.object(claude_cli, 'cli_command', return_value=None):
            self.assertEqual(self.client.post(self.url + '/tasks', json={'kind': 'script'}).status_code, 400)
