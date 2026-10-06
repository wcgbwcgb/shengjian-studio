import io
import json
import tempfile
import threading
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import assets, catalog, config, jobs, models, store, timeline
from app.main import app


def audio():
    output = io.BytesIO()
    with wave.open(output, 'wb') as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(8000)
        writer.writeframes(b'\0' * 1600)
    return output.getvalue()


class ConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = patch.object(store, 'DATA', Path(self.temp.name))
        self.data.start()
        self.env = patch.dict('os.environ', {'ANTHROPIC_API_KEY': ''})
        self.env.start()
        self.context = TestClient(app)
        self.client = self.context.__enter__()
        self.project = self.client.post('/api/projects', json={'name': '素材项目'}).json()
        self.url = '/api/projects/' + self.project['id']

    def tearDown(self):
        self.context.__exit__(None, None, None)
        self.env.stop()
        self.data.stop()
        self.temp.cleanup()

    def upload(self, url=None, media_type=None):
        result = self.client.post((url or self.url + '/assets') + ('?type=' + media_type if media_type else ''),
                                  files={'file': ('sample.wav', audio(), 'audio/wav')})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def service(self, protocol='anthropic', name='service A', model=None, prices=None):
        body = {'name': name, 'protocol': protocol, 'api_key': name.replace(' ', '-') + '-secret',
                'base_url': 'https://a.example' if protocol == 'anthropic' else 'https://b.example'}
        if model:
            body['model_defaults'] = {s: model for s in ('research', 'script', 'edit')}
        if prices:
            body['pricing'] = prices
        result = self.client.post('/api/connections', json=body)
        self.assertEqual(result.status_code, 200, result.text)
        return next(p['id'] for p in result.json()['connections'] if p['name'] == name)

    def test_upload_defaults_and_type_are_independent_of_entry(self):
        own = self.upload()
        shared = self.upload('/api/library/assets')
        self.assertEqual((own['scope'], own['project_id'], own['type']), ('project', self.project['id'], 'Audio'))
        self.assertEqual((shared['scope'], shared['project_id'], shared['type']), ('public', None, 'Audio'))
        for endpoint in (self.url + '/assets', '/api/library/assets'):
            music = self.upload(endpoint, 'Music')
            self.assertEqual(music['type'], 'Music')
            self.assertEqual(music['kind'], 'music')
        self.assertEqual(len(self.client.get(self.url).json()['assets']), 2)
        self.assertEqual(len(self.client.get('/api/library').json()['public_assets']), 2)

    def test_move_keeps_identity_bytes_and_updates_database_ownership(self):
        own = self.upload(media_type='Music')
        original = assets.file(own)
        shared = self.client.patch('/api/assets/' + own['id'], json={'scope': 'public'}).json()
        self.assertEqual(shared['id'], own['id'])
        self.assertEqual(shared['type'], 'Music')
        self.assertEqual(self.client.get(self.url).json()['assets'], [])
        self.assertEqual(assets.file(shared), original)
        self.assertEqual(self.client.get('/api/assets/' + own['id'] + '/file').content, audio())
        other = self.client.post('/api/projects', json={'name': '其他项目'}).json()
        moved = self.client.patch('/api/assets/' + own['id'], json={'scope': 'project', 'project_id': other['id']}).json()
        self.assertEqual(moved['type'], 'Music')
        self.assertEqual(store.listing('asset', other['id'])[0]['id'], own['id'])

    def test_public_copy_preserves_type_and_does_not_move_original(self):
        shared = self.upload('/api/library/assets', 'Music')
        copied = self.client.post(self.url + '/workspace/import-asset', json={'asset_id': shared['id']}).json()
        self.assertNotEqual(copied['id'], shared['id'])
        self.assertEqual((copied['scope'], copied['type']), ('project', 'Music'))
        self.assertEqual(store.get('asset', shared['id'])['scope'], 'public')
        self.assertEqual(assets.file(copied).read_bytes(), audio())

    def test_music_protection_is_derived_from_type_even_with_stale_kind(self):
        a = self.upload(media_type='Music')
        a.update(kind='speech', analysis={'duration': 4}, protected=[])
        seg = {'source_asset_id': a['id'], 'source_start_sec': 0, 'source_end_sec': 2}
        with self.assertRaises(ValueError):
            timeline.validate({'segments': [seg]}, [a])
        seg['source_end_sec'] = 4
        seg['volume'] = .5
        with self.assertRaises(ValueError):
            timeline.validate({'segments': [seg]}, [a])
        seg['volume'] = 1
        self.assertEqual(timeline.validate({'segments': [seg]}, [a])['duration'], 4)

    def test_invalid_type_and_active_asset_moves_are_rejected(self):
        response = self.client.post(self.url + '/assets?type=Image', files={'file': ('sample.wav', audio())})
        self.assertEqual(response.status_code, 400)
        own = self.upload()
        with patch.object(jobs.QUEUE, 'put'):
            task = self.client.post(self.url + '/tasks', json={'kind': 'analyze'}).json()
            self.assertEqual(self.client.patch('/api/assets/' + own['id'], json={'scope': 'public'}).status_code, 400)
            task['status'] = 'cancelled'
            store.put('task', task, self.project['id'])

    def test_legacy_asset_migration_is_idempotent(self):
        a = store.put('asset', {'path': 'assets/old.wav', 'name': 'old.wav', 'kind': 'music', 'analysis': {'duration': 4}}, self.project['id'])
        assets.migrate()
        migrated = store.get('asset', a['id'])
        self.assertEqual((migrated['scope'], migrated['type']), ('project', 'Music'))
        self.assertEqual(migrated['protected'], [{'start': 0, 'end': 4}])
        stamp = migrated['updated_at']
        assets.migrate()
        self.assertEqual(store.get('asset', a['id'])['updated_at'], stamp)

    def test_provider_model_filters_and_incompatible_submission(self):
        ident = self.service('openai', model='gpt-6-luna')
        self.client.post('/api/connections/' + ident + '/activate', json={})
        result = self.client.get('/api/settings').json()
        self.assertTrue(all(m['provider'] == 'openai' for m in result['models']))
        self.assertEqual(result['defaults']['model_script'], 'gpt-6-luna')
        with patch.object(jobs.QUEUE, 'put'):
            self.assertEqual(self.client.post(self.url + '/tasks', json={'kind': 'script', 'model': 'claude-opus-5-5'}).status_code, 400)
        bad = self.client.post('/api/connections', json={'name': 'bad', 'protocol': 'openai', 'api_key': 'test',
                              'model_defaults': {'script': 'claude-sonnet-5-5'}})
        self.assertEqual(bad.status_code, 400)

    def test_snapshot_freezes_credentials_provider_model_price_budget_and_retry(self):
        first = self.service(model='claude-opus-5-5', prices={'claude-opus-5-5': {'input_price': 7}})
        self.client.put('/api/settings/budget', json={'monthly_budget': 100, 'max_tokens': 1500})
        with patch.object(jobs.QUEUE, 'put'):
            task = self.client.post(self.url + '/tasks', json={'kind': 'script', 'prompt': '写稿'}).json()
            second = self.service('openai', 'service B', 'gpt-6-luna')
            self.client.post('/api/connections/' + second + '/activate', json={})
            self.client.put('/api/settings/budget', json={'monthly_budget': 1, 'max_tokens': 700})
            self.client.delete('/api/connections/' + first)
            response = {'type': 'message', 'content': [{'type': 'text', 'text': '{"advice":"OK"}'}],
                        'usage': {'input_tokens': 1000, 'output_tokens': 1}, 'stop_reason': 'end_turn'}
            with patch.object(models.httpx, 'Client') as client_type:
                client = client_type.return_value.__enter__.return_value
                client.post.return_value.status_code = 200
                client.post.return_value.json.return_value = response
                models.call(task, 'script', {}, threading.Event(), lambda _: None)
                call = client.post.call_args
                self.assertEqual(call.args[0], 'https://a.example/v1/messages')
                self.assertEqual(call.kwargs['headers']['x-api-key'], 'service-A-secret')
                self.assertEqual(call.kwargs['json']['model'], 'claude-opus-5-5')
                self.assertEqual(call.kwargs['json']['max_tokens'], 1500)
            self.assertEqual(task['model_config']['monthly_budget'], 100)
            self.assertEqual(task['model_config']['pricing']['input_price'], 7)
            self.assertNotIn('service-A-secret', self.client.get(self.url).text)
            task['status'] = 'failed'
            store.put('task', task, self.project['id'])
            retry = self.client.post('/api/tasks/' + task['id'] + '/retry', json={}).json()
            self.assertEqual(retry['model_config'], task['model_config'])
            self.assertEqual(config.frozen_credentials(retry), ('service-A-secret', 'https://a.example/v1/messages'))
            retry['status'] = 'cancelled'
            store.put('task', retry, self.project['id'])

    def test_settings_groups_validate_and_save_independently(self):
        before = store.settings()
        self.assertEqual(self.client.put('/api/settings/preferences', json={'style': '明快'}).status_code, 200)
        self.assertEqual(store.settings()['monthly_budget'], before['monthly_budget'])
        self.assertEqual(self.client.put('/api/settings/budget', json={'monthly_budget': 90}).status_code, 200)
        self.assertEqual(store.settings()['style'], '明快')
        for endpoint, body in (('preferences', {'monthly_budget': 900}), ('budget', {'style': '不应保存'})):
            self.assertEqual(self.client.put('/api/settings/' + endpoint, json=body).status_code, 400)
        self.assertEqual(store.settings()['monthly_budget'], 90)

    def test_assets_entry_starts_video_after_upload_and_uses_asset_snapshot(self):
        with patch.object(config, 'has_api_key', return_value=True):
            created = self.client.post('/api/creations', json={'idea': '从素材制作', 'intent': 'assets'}).json()
        p = created['project']
        self.assertIsNone(created['task'])
        self.assertEqual(p['status'], '待添加素材')
        own = self.upload('/api/projects/' + p['id'] + '/assets')
        with patch.object(claude_module(), 'cli_command', return_value=['fixture']), patch.object(jobs.QUEUE, 'put'):
            result = self.client.post('/api/projects/' + p['id'] + '/workspace/start-video', json={}).json()
            self.assertEqual(result['task']['kind'], 'cli_video')
            self.assertEqual(result['task']['asset_snapshot'][0]['id'], own['id'])
            task = result['task'];task['status'] = 'cancelled';store.put('task', task, p['id'])

    def test_missing_cli_keeps_assets_in_video_flow_without_false_processing(self):
        p = self.client.post('/api/creations', json={'idea': '从素材制作', 'intent': 'assets'}).json()['project']
        with patch.object(claude_module(), 'cli_command', return_value=None):
            result = self.client.post('/api/projects/' + p['id'] + '/workspace/start-video', json={}).json()
        self.assertTrue(result['needs_cli'])
        current = store.get('project', p['id'])
        self.assertEqual((current['workspace']['intent'], current['status']), ('video', '待制作'))

    def test_asr_is_disabled(self):
        self.assertEqual(self.client.post(self.url + '/tasks', json={'kind': 'analyze', 'transcribe': True}).status_code, 400)


def claude_module():
    from app import claude_cli
    return claude_cli
