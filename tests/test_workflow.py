import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import catalog, config, jobs, models, store
from app.main import app


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patch = patch.object(store, 'DATA', Path(self.temp.name))
        self.patch.start()
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()
        self.project = self.client.post('/api/projects', json={'name': '双人测试'}).json()
        self.prefix = '/api/projects/' + self.project['id']

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        self.patch.stop()
        self.temp.cleanup()

    def script(self, text='旧开场'):
        return self.client.post(self.prefix + '/versions', json={'stage': 'script', 'result': {
            'paragraphs': [{'id': 'intro', 'speaker': 'A', 'text': text}, {'id': 'locked', 'speaker': 'B', 'text': '不变', 'locked': True}]}}).json()

    def test_version_adoption_is_separate_and_new_script_marks_edit_stale(self):
        v = self.script()
        self.assertEqual(self.client.get(self.prefix).json()['project']['adopted'], {})
        self.client.post(self.prefix + '/versions/' + v['id'] + '/adopt', json={})
        p = store.get('project', self.project['id'])
        p['adopted']['edit'] = 'old-edit'
        store.put('project', p)
        updated = self.script('新开场')
        self.client.post(self.prefix + '/versions/' + updated['id'] + '/adopt', json={})
        self.assertIn('edit', self.client.get(self.prefix).json()['project']['stale_stages'])

    def test_rewrite_preserves_untargeted_and_locked_paragraphs(self):
        current = self.script()['result']
        generated = {'paragraphs': [{'id': 'intro', 'text': '新开场'}, {'id': 'locked', 'text': '偷偷改动'}]}
        r = jobs.protect_script(generated, current, ['intro'])
        self.assertEqual(r['paragraphs'][0]['text'], '新开场')
        self.assertEqual(r['paragraphs'][1]['text'], '不变')

    def test_missing_key_fails_without_fabricated_result(self):
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': ''}):
            with self.assertRaisesRegex(ValueError, '未配置'):
                models.call({'id': 'task', 'project_id': self.project['id']}, 'script', {}, threading.Event(), lambda _: None)
        self.assertEqual(store.listing('version', self.project['id']), [])
        self.assertEqual(store.listing('usage'), [])

    def test_cross_project_version_cannot_be_adopted(self):
        v = self.script()
        other = self.client.post('/api/projects', json={'name': '其他项目'}).json()
        result = self.client.post('/api/projects/' + other['id'] + '/versions/' + v['id'] + '/adopt', json={})
        self.assertEqual(result.status_code, 400)

    def test_source_urls_are_deduplicated_and_unread_metrics_not_fabricated(self):
        body = {'url': 'https://www.bilibili.com/video/test', 'title': '用户参考'}
        self.client.post(self.prefix + '/sources', json=body)
        self.client.post(self.prefix + '/sources', json=body)
        src = self.client.get(self.prefix).json()['sources']
        self.assertEqual(len(src), 1)
        self.assertEqual(src[0]['metrics'], {})
        self.assertIsNone(src[0]['published_at'])
        self.assertEqual(src[0]['verification'], 'user_supplied')

    def test_invented_research_link_never_becomes_verified(self):
        result = models.ground_research({'topics': [{'source_urls': ['https://example.com/fake']}],
                                        'sources': [{'url': 'https://example.com/fake', 'verification': 'fetched', 'metrics': {'likes': 999}}]}, [], [])
        self.assertEqual(result['sources'][0]['verification'], 'unverified')
        self.assertEqual(result['sources'][0]['metrics'], {})
        self.assertEqual(result['topics'][0]['evidence'], '探索选题')

    def test_cross_site_requests_are_rejected(self):
        response = self.client.post('/api/projects', json={'name': 'attack'}, headers={'Origin': 'https://example.com'})
        self.assertEqual(response.status_code, 403)

    def test_saved_key_is_private_blank_preserves_and_clear_disables_env_fallback(self):
        secret = 'sk-ant-unit-test-not-a-real-key'
        response = self.client.put('/api/connection', json={'api_key': secret})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['api_configured'])
        self.assertEqual(config.api_key(), secret)
        self.client.put('/api/connection', json={'api_key': '', 'base_url': 'https://api.anthropic.com'})
        self.assertEqual(config.api_key(), secret)
        for endpoint in ('/api/settings', '/api/environment', self.prefix):
            self.assertNotIn(secret, self.client.get(endpoint).text)
        if response.json()['key_storage'] == 'windows_account':
            self.assertNotIn(secret, (store.DATA / 'machine-config.json').read_text(encoding='utf-8'))
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'legacy-environment-key'}):
            self.client.put('/api/connection', json={'clear_key': True})
            self.assertFalse(config.has_api_key())
            self.assertEqual(config.api_key(), '')

    def test_windows_without_dpapi_profile_can_still_save_local_configuration(self):
        secret = 'sk-ant-portable-test-not-a-real-key'
        with patch.object(config, '_dpapi', side_effect=ValueError('missing user profile')):
            result = self.client.put('/api/connection', json={'api_key': secret})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['key_storage'], 'local_file')
        self.assertEqual(config.api_key(), secret)
        self.assertNotIn(secret, self.client.get('/api/settings').text)

    def test_invalid_base_url_and_model_are_rejected(self):
        for url in ('https://user:secret@example.com', 'https://example.com?api_key=secret', 'file:///tmp/private',
                    'http://external.example.com', 'https://api.anthropic.com/v1/messages'):
            response = self.client.put('/api/connection', json={'base_url': url})
            self.assertEqual(response.status_code, 400)
        response = self.client.put('/api/settings', json={'model_script': 'unknown'})
        self.assertEqual(response.status_code, 400)
        response = self.client.post(self.prefix + '/tasks', json={'kind': 'script', 'model': 'unknown'})
        self.assertEqual(response.status_code, 400)

    def test_luna_default_and_responses_connection(self):
        self.assertEqual(self.client.put('/api/settings', json={'model_script': 'gpt-6-luna'}).status_code, 200)
        self.assertEqual(self.client.put('/api/connection', json={'protocol': 'openai', 'base_url': 'https://api.openai.com/v1'}).status_code, 200)
        self.assertEqual(config.messages_url(), 'https://api.openai.com/v1/responses')
        response = {'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'OK'}]}],
                    'usage': {'input_tokens': 80, 'output_tokens': 1, 'input_tokens_details': {'cached_tokens': 20}}}
        with patch.object(config, 'api_key', return_value='unit-test-key'), patch.object(models.httpx, 'Client') as client_type:
            client = client_type.return_value.__enter__.return_value
            client.post.return_value.status_code = 200
            client.post.return_value.json.return_value = response
            result = self.client.post('/api/connection/test', json={'model': 'gpt-6-luna'})
            self.assertEqual(result.status_code, 200)
            args = client.post.call_args.kwargs
            self.assertEqual(args['headers']['Authorization'], 'Bearer unit-test-key')
            self.assertEqual(args['json']['model'], 'gpt-6-luna')
            self.assertNotIn('messages', args['json'])
            usage = store.listing('usage')[0]
            self.assertEqual(usage['input_tokens'], 60)
            self.assertEqual(usage['cache_read_tokens'], 20)
            self.assertAlmostEqual(usage['estimated_usd'], .0000067)

    def test_key_profiles_preserve_switch_replace_and_delete_without_exposing_keys(self):
        self.client.put('/api/connection', json={'api_key': 'legacy-test-secret', 'base_url': 'https://api.anthropic.com'})
        result = self.client.post('/api/connections', json={'name': 'OpenAI test', 'protocol': 'openai',
                     'base_url': 'https://api.openai.com', 'api_key': 'openai-test-secret'})
        self.assertEqual(result.status_code, 200)
        profiles = result.json()['connections']
        self.assertEqual(len(profiles), 2)
        self.assertEqual(config.api_key(), 'legacy-test-secret')
        ident = next(p['id'] for p in profiles if p['name'] == 'OpenAI test')
        for secret in ('legacy-test-secret', 'openai-test-secret'):
            self.assertNotIn(secret, result.text)
            self.assertNotIn(secret, self.client.get('/api/settings').text)
        self.client.post(f'/api/connections/{ident}/activate', json={})
        self.assertEqual(config.api_key(), 'openai-test-secret')
        self.assertEqual(config.messages_url(), 'https://api.openai.com/v1/responses')
        self.client.post('/api/connections', json={'id': ident, 'name': 'Renamed', 'api_key': ''})
        self.assertEqual(config.api_key(), 'openai-test-secret')
        self.client.post('/api/connections', json={'id': ident, 'name': 'Renamed', 'api_key': 'replacement-test-secret'})
        self.assertEqual(config.api_key(), 'replacement-test-secret')
        self.client.delete(f'/api/connections/{ident}')
        self.assertFalse(config.has_api_key())
        self.assertEqual(len(config.public()['connections']), 1)
        self.assertNotIn('replacement-test-secret', config._path().read_text(encoding='utf-8'))

    def test_key_profile_validation_leaves_connection_unchanged(self):
        for body in ({'name': 'empty'}, {'name': 'bad', 'api_key': 'contains space'},
                     {'name': 'bad', 'api_key': 'test', 'protocol': 'unknown'}):
            self.assertEqual(self.client.post('/api/connections', json=body).status_code, 400)
        self.assertEqual(config.public()['connections'], [])

    def test_responses_search_evidence_remains_grounded(self):
        from unittest.mock import Mock
        client = Mock()
        client.post.return_value.status_code = 200
        client.post.return_value.json.return_value = {'output': [
            {'type': 'web_search_call', 'action': {'type': 'search', 'sources': [{'url': 'https://example.com/evidence'}]}},
            {'type': 'message', 'content': [{'type': 'output_text', 'text': '{}'}]}], 'usage': {}}
        result = models.request(client, {'model': 'gpt-6-luna', 'max_tokens': 1024,
                                'messages': [{'role': 'user', 'content': 'research'}],
                                'tools': [{'max_uses': 3}]}, 'unit-test-key', 'https://api.openai.com/v1/responses')
        self.assertEqual(client.post.call_args.kwargs['json']['max_tool_calls'], 3)
        research = models.ground_research({'sources': [{'url': 'https://example.com/evidence'}, {'url': 'https://invented.example'}]}, result['content'], [])
        self.assertEqual(research['sources'][0]['verification'], 'search_only')
        self.assertEqual(research['sources'][1]['verification'], 'unverified')

    def test_task_model_and_retry_are_frozen_independently_of_defaults(self):
        with patch.object(jobs.QUEUE, 'put'):
            t = self.client.post(self.prefix + '/tasks', json={'kind': 'script', 'prompt': '写稿', 'model': 'claude-opus-5-5'}).json()
            self.client.put('/api/settings', json={'model_script': 'claude-sonnet-5-5'})
            self.assertEqual(store.get('task', t['id'])['model_config']['model'], 'claude-opus-5-5')
            self.assertNotIn('api_key', str(t))
            t['status'] = 'failed'
            store.put('task', t, self.project['id'])
            retried = self.client.post('/api/tasks/' + t['id'] + '/retry', json={}).json()
            self.assertEqual(retried['model_config']['model'], 'claude-opus-5-5')
            self.assertEqual(retried['model_config']['pricing']['input_price'], 4)
            retried['status'] = 'cancelled'
            store.put('task', retried, self.project['id'])
            default_task = self.client.post(self.prefix + '/tasks', json={'kind': 'angles'}).json()
            self.assertEqual(default_task['model_config']['model'], 'claude-sonnet-5-5')

    def test_selected_model_is_sent_to_provider_and_billed_at_its_own_rate(self):
        response = {'type': 'message', 'content': [{'type': 'text', 'text': '{"advice":"OK"}'}],
                    'usage': {'input_tokens': 1000, 'output_tokens': 100}, 'stop_reason': 'end_turn'}
        with patch.object(config, 'api_key', return_value='unit-test-key'), patch.object(models.httpx, 'Client') as client_type:
            client = client_type.return_value.__enter__.return_value
            client.post.return_value.status_code = 200
            client.post.return_value.json.return_value = response
            for ident in ('claude-sonnet-5-5', 'claude-opus-5-5'):
                task = {'id': ident, 'project_id': self.project['id'], 'model_config': {'model': ident, 'pricing': catalog.pricing(ident)}}
                _, _, metadata = models.call(task, 'script', {}, threading.Event(), lambda _: None)
                self.assertEqual(client.post.call_args.kwargs['json']['model'], ident)
                self.assertEqual(metadata['model'], ident)
        costs = {u['model']: u['estimated_usd'] for u in store.listing('usage', self.project['id'])}
        self.assertAlmostEqual(costs['claude-sonnet-5-5'], .003)
        self.assertAlmostEqual(costs['claude-opus-5-5'], .006)

    def test_connection_probe_uses_requested_model_and_records_real_returned_usage(self):
        response = {'type': 'message', 'content': [{'type': 'text', 'text': 'OK'}],
                    'usage': {'input_tokens': 8, 'output_tokens': 1}, 'stop_reason': 'end_turn'}
        with patch.object(config, 'api_key', return_value='unit-test-key'), patch.object(models.httpx, 'Client') as client_type:
            client = client_type.return_value.__enter__.return_value
            client.post.return_value.status_code = 200
            client.post.return_value.json.return_value = response
            result = self.client.post('/api/connection/test', json={'model': 'claude-sonnet-5-5'})
            self.assertEqual(result.status_code, 200)
            self.assertTrue(result.json()['ok'])
            self.assertEqual(client.post.call_args.kwargs['json']['max_tokens'], 16)
            self.assertEqual(client.post.call_args.kwargs['json']['model'], 'claude-sonnet-5-5')
            self.assertEqual(store.listing('usage')[0]['purpose'], 'connection_test')
            self.assertEqual(config.public()['last_test']['model'], 'claude-sonnet-5-5')


if __name__ == '__main__':
    unittest.main()
