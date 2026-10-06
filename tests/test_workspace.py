"""V2 invariants tested against the real API/store, with deterministic provider responses."""
import copy
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import config, jobs, media, models, store, workspace
from app.main import app


def research_result():
    return {'summary': '研究测试', 'topics': [{
        'id': 'topic', 'title': '为什么节奏让人松弛', 'source_urls': ['https://example.org/evidence'],
        'key_facts': [{'claim': '有来源的解释', 'source_urls': ['https://example.org/evidence']}],
        'angles': [{'id': f'angle-{i}', 'title': f'角度 {i}', 'hook': '听听这个变化',
                    'reason': '用对比说明', 'audience': '音乐爱好者', 'difference': '实际演示'} for i in range(1, 4)]}],
        'sources': [{'url': 'https://example.org/evidence', 'title': '原始证据', 'evidence_note': '测试引用'}]}


def script_result():
    return {'angle': '角度 1', 'paragraphs': [
        {'id': 'hook', 'speaker': '旁白', 'beat': 'hook', 'text': '听听这个节奏。', 'cue': '节奏示范',
         'source_urls': ['https://example.org/evidence'], 'visual_keywords': ['节奏'], 'claim_type': 'fact'},
        {'id': 'outro', 'speaker': '旁白', 'beat': 'cta', 'text': '你更喜欢哪一种？', 'locked': True,
         'cue': '文字提问', 'claim_type': 'opinion'}]}


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = patch.object(store, 'DATA', Path(self.temp.name))
        self.data.start()
        self.no_key = patch.object(config, 'has_api_key', return_value=False)
        self.no_key.start()
        self.context = TestClient(app)
        self.client = self.context.__enter__()
        self.p = self.client.post('/api/creations', json={'idea': '为什么节奏让人松弛'}).json()['project']
        self.url = '/api/projects/' + self.p['id']

    def tearDown(self):
        self.context.__exit__(None, None, None)
        self.no_key.stop()
        self.data.stop()
        self.temp.cleanup()

    def version(self, stage, result):
        return store.put('version', {'stage': stage, 'result': result, 'upstream': {},
                         'effective': {}, 'selected_topic_id': None}, self.p['id'])

    def seeded(self):
        source = store.put('source', {'url': 'https://example.org/evidence', 'title': '原始证据',
                           'verification': 'fetched', 'evidence_note': '证据摘录'}, self.p['id'])
        result = workspace.ground_script(jobs.protect_script(script_result()), [source])
        v = self.version('script', result)
        p = store.get('project', self.p['id'])
        p['workspace']['script_version'] = v['id']
        store.put('project', p)
        return v

    def wait_task(self, ident):
        for _ in range(100):
            t = store.get('task', ident)
            if t['status'] not in workspace.ACTIVE:
                return t
            time.sleep(.03)
        self.fail('Worker did not complete')

    def test_new_project_with_a_name_is_created_without_starting_work(self):
        response = self.client.post('/api/creations', json={'name': '老歌翻红', 'idea': '讲老歌为什么在短视频翻红',
                                                           'duration': 90, 'aspect': '16:9', 'start': False})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIsNone(body['task'])
        project = store.get('project', body['project']['id'])
        self.assertEqual((project['name'], project['name_custom']), ('老歌翻红', True))
        self.assertEqual(project['workspace']['idea'], '讲老歌为什么在短视频翻红')
        self.assertEqual(project['requirements'], {'duration': 90, 'aspect': '16:9'})
        self.assertEqual(store.listing('task', project['id']), [])
        # The idea is optional: the name stands in for it.
        named = self.client.post('/api/creations', json={'name': '只有名字', 'start': False}).json()['project']
        self.assertEqual(named['workspace']['idea'], '只有名字')
        self.assertEqual(self.client.post('/api/creations', json={'start': False}).status_code, 400)

    def test_project_from_the_prompt_only_workbench_opens_with_defaults(self):
        # That version saved only name and idea; the workspace must still open it.
        p = store.put('project', {'name': '街访', 'idea': '街访：单曲循环最久的歌'})
        detail = self.client.get('/api/projects/' + p['id'])
        self.assertEqual(detail.status_code, 200)
        project = detail.json()['project']
        self.assertEqual(project['requirements'], {})
        self.assertEqual(project['defaults']['aspect'], '9:16')
        self.assertEqual(project['workspace']['idea'], '街访：单曲循环最久的歌')

    def test_create_preserves_url_and_does_not_fake_research_without_provider(self):
        r = self.client.post('/api/creations', json={'idea': '研究 https://example.org/test'}).json()
        d = self.client.get('/api/projects/' + r['project']['id']).json()
        self.assertTrue(r['needs_connection'])
        self.assertFalse(d['tasks'])
        self.assertFalse(d['versions'])
        self.assertEqual(d['sources'][0]['verification'], 'user_supplied')
        self.assertEqual(self.client.get('/api/opportunities').json(), [])

    def test_angle_runs_script_using_research_context_without_extra_prompt(self):
        research = self.version('research', research_result())
        def provider(task, kind, context, event, report):
            self.assertEqual(kind, 'script')
            self.assertEqual(context['selected_angle'], '角度 2')
            self.assertEqual(context['research']['topics'][0]['id'], 'topic')
            self.assertNotIn('current', context)
            return script_result(), [], {'model': 'test'}
        with patch.object(models, 'call', side_effect=provider):
            r = self.client.post(self.url + '/workspace/angle', json={
                'version_id': research['id'], 'topic_id': 'topic', 'angle_id': 'angle-2'})
            self.assertEqual(r.status_code, 200)
            t = self.wait_task(r.json()['id'])
        self.assertEqual(t['status'], 'completed', t.get('error'))
        p = store.get('project', self.p['id'])
        self.assertEqual(p['workspace']['angle']['title'], '角度 2')
        self.assertEqual(p['adopted']['research'], research['id'])
        self.assertEqual(p['workspace']['script_version'], t['output']['version_id'])
        self.assertNotIn('script', p['stale_stages'])

    def test_accept_script_creates_timed_scenes_and_keeps_citations(self):
        v = self.seeded()
        r = self.client.post(self.url + '/workspace/scenes', json={'version_id': v['id']})
        self.assertEqual(r.status_code, 200)
        scenes = r.json()['result']['scenes']
        self.assertEqual(len(scenes), 2)
        self.assertEqual(scenes[0]['narration'], v['result']['paragraphs'][0]['text'])
        self.assertEqual(scenes[1]['start'], scenes[0]['end'])
        self.assertEqual(scenes[0]['source_urls'], ['https://example.org/evidence'])
        self.assertIsNone(scenes[0]['asset_id'])

    def test_edit_updates_scenes_marks_video_stale_and_preserves_old_version(self):
        v = self.seeded()
        old_scene = self.client.post(self.url + '/workspace/scenes', json={'version_id': v['id']}).json()
        result = copy.deepcopy(v['result'])
        result['paragraphs'][0]['text'] = '改变后的事实也要复核。'
        saved = self.client.post(self.url + '/workspace/script', json={'parent_id': v['id'], 'result': result})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()['result']['paragraphs'][0]['evidence_status'], 'review')
        p = store.get('project', self.p['id'])
        scenes = store.get('version', p['workspace']['scene_version'])
        self.assertNotEqual(scenes['id'], old_scene['id'])
        self.assertEqual(scenes['result']['scenes'][0]['narration'], '改变后的事实也要复核。')
        self.assertIn('edit', p['stale_stages'])
        self.assertEqual(store.get('version', v['id'])['result']['paragraphs'][0]['text'], '听听这个节奏。')

    def test_stale_editor_cannot_overwrite_newer_draft(self):
        v = self.seeded()
        first = self.client.post(self.url + '/workspace/script', json={'parent_id': v['id'], 'result': v['result']})
        self.assertEqual(first.status_code, 200)
        stale = self.client.post(self.url + '/workspace/script', json={'parent_id': v['id'], 'result': v['result']})
        self.assertEqual(stale.status_code, 400)

    def test_unknown_citation_is_not_promoted_to_evidence(self):
        result = script_result()
        result['paragraphs'][0]['source_urls'].append('https://invented.invalid/fake')
        grounded = workspace.ground_script(result, [{'id': 'real', 'url': 'https://example.org/evidence', 'verification': 'search_only'}])
        self.assertEqual(grounded['paragraphs'][0]['source_urls'], ['https://example.org/evidence'])
        self.assertEqual(grounded['paragraphs'][0]['citations'][0]['verification'], 'search_only')

    def test_scene_replacement_and_duration_are_validated_and_versioned(self):
        v = self.seeded()
        scenes = self.client.post(self.url + '/workspace/scenes', json={'version_id': v['id']}).json()
        a = store.put('asset', {'name': '画面.png', 'kind': 'image', 'path': 'assets/image.png'}, self.p['id'])
        body = {'version_id': scenes['id'], 'scene_id': scenes['result']['scenes'][0]['id'], 'asset_id': a['id'], 'duration': 9}
        r = self.client.patch(self.url + '/workspace/scenes', json=body)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['result']['scenes'][1]['start'], 9)
        self.assertEqual(r.json()['result']['scenes'][0]['asset_id'], a['id'])
        self.assertEqual(self.client.patch(self.url + '/workspace/scenes', json=body).status_code, 400)
        foreign = store.put('asset', {'kind': 'image'}, 'other-project')
        body.update(version_id=r.json()['id'], asset_id=foreign['id'])
        self.assertEqual(self.client.patch(self.url + '/workspace/scenes', json=body).status_code, 400)

    def test_locked_rewrite_rejected(self):
        v = self.seeded()
        r = self.client.post(self.url + '/workspace/rewrite', json={'version_id': v['id'], 'paragraph_id': 'outro', 'instruction': '缩短'})
        self.assertEqual(r.status_code, 400)

    def test_opportunity_starts_independent_video_and_copies_evidence_without_mutation(self):
        research = self.version('research', research_result())
        r = self.client.post('/api/creations/from-research', json={'version_id': research['id'], 'topic_id': 'topic'})
        self.assertEqual(r.status_code, 200)
        self.assertNotEqual(r.json()['project']['id'], self.p['id'])
        self.assertEqual(store.get('version', research['id'])['project_id'], self.p['id'])
        self.assertEqual(len(self.client.get('/api/projects/' + r.json()['project']['id']).json()['sources']), 1)
        self.assertFalse(store.get('project', self.p['id'])['adopted'])

    def test_unmodified_manual_scene_duration_survives_script_sync(self):
        v = self.seeded()
        scene_version = self.client.post(self.url + '/workspace/scenes', json={'version_id': v['id']}).json()
        scene = scene_version['result']['scenes'][1]
        self.client.patch(self.url + '/workspace/scenes', json={'version_id': scene_version['id'], 'scene_id': scene['id'], 'duration': 12})
        result = copy.deepcopy(v['result'])
        result['paragraphs'][0]['text'] = '只改变开场。'
        self.client.post(self.url + '/workspace/script', json={'parent_id': v['id'], 'result': result})
        p = store.get('project', self.p['id'])
        current = store.get('version', p['workspace']['scene_version'])
        self.assertEqual(current['result']['scenes'][1]['duration'], 12)

    def test_model_completion_does_not_replace_manual_edit_made_during_generation(self):
        v = self.seeded()
        with patch.object(jobs.QUEUE, 'put'):
            task = jobs.submit(self.p['id'], 'script', {'workspace': True, 'base_version_id': v['id'], 'prompt': '更自然'})
        result = copy.deepcopy(v['result'])
        result['paragraphs'][0]['text'] = '生成期间的新编辑'
        saved = self.client.post(self.url + '/workspace/script', json={'parent_id': v['id'], 'result': result}).json()
        generated = self.version('script', script_result())
        workspace.completed_script(task, generated)
        self.assertEqual(store.get('project', self.p['id'])['workspace']['script_version'], saved['id'])
        self.assertTrue(store.get('version', generated['id'])['stale'])

    def test_legacy_version_adoption_updates_workspace_and_marks_scenes_stale(self):
        v = self.seeded()
        self.client.post(self.url + '/workspace/scenes', json={'version_id': v['id']})
        result = copy.deepcopy(v['result'])
        result['paragraphs'][0]['text'] = '在原有编辑器中改过的表达'
        edited = self.client.post(self.url + '/versions', json={'stage': 'script', 'parent_id': v['id'], 'result': result}).json()
        self.client.post(self.url + '/versions/' + edited['id'] + '/adopt', json={})
        p = store.get('project', self.p['id'])
        self.assertEqual(p['workspace']['script_version'], edited['id'])
        self.assertIn('scenes', p['stale_stages'])
        self.assertEqual(edited['result']['paragraphs'][0]['evidence_status'], 'review')

    def test_older_script_can_be_restored_as_new_branch(self):
        v = self.seeded()
        self.client.post(self.url + '/workspace/scenes', json={'version_id': v['id']})
        r = self.client.post(self.url + '/workspace/restore', json={'version_id': v['id']})
        self.assertEqual(r.status_code, 200)
        self.assertNotEqual(r.json()['id'], v['id'])
        self.assertEqual(r.json()['parent_id'], v['id'])
        self.assertEqual(store.get('project', self.p['id'])['adopted']['script'], r.json()['id'])

    def test_first_cut_missing_engine_has_recoverable_failure_and_retains_scenes(self):
        v = self.seeded()
        scenes = self.client.post(self.url + '/workspace/scenes', json={'version_id': v['id']}).json()
        with patch.object(media, 'executable', return_value=None):
            r = self.client.post(self.url + '/workspace/first-cut', json={})
            task = self.wait_task(r.json()['id'])
        self.assertEqual(task['status'], 'failed')
        self.assertIn('组件', task['error'])
        self.assertEqual(store.get('project', self.p['id'])['workspace']['scene_version'], scenes['id'])

    def test_first_cut_uses_scenes_not_an_empty_or_full_source_timeline(self):
        v = self.seeded()
        scenes = self.client.post(self.url + '/workspace/scenes', json={'version_id': v['id']}).json()
        a = store.put('asset', {'name': '节奏.mp4', 'kind': 'speech', 'path': 'assets/a.mp4', 'protected': [],
                     'analysis': {'duration': 20, 'has_video': True, 'has_audio': True, 'transcript': []}}, self.p['id'])
        for scene in scenes['result']['scenes']:
            scene['asset_id'] = a['id']
        store.put('version', scenes, self.p['id'])
        with patch.object(media, 'executable', return_value='test-ffmpeg'), patch.object(media, 'analyze', side_effect=lambda a,*args:a), \
             patch.object(media, 'render', return_value={'video': 'test.mp4'}) as render:
            r = self.client.post(self.url + '/workspace/first-cut', json={})
            task = self.wait_task(r.json()['id'])
        self.assertEqual(task['status'], 'completed', task.get('error'))
        result = store.get('version', task['output']['version_id'])['result']
        self.assertEqual(len(result['segments']), 2)
        self.assertGreater(result['segments'][1]['source_start_sec'], 0)
        self.assertLess(result['duration'], 20)
        self.assertEqual(result['captions'], [])
        self.assertFalse(store.get('version', task['output']['version_id'])['subtitles_enabled'])
        self.assertEqual(result['scene_version_id'], scenes['id'])
        self.assertTrue(render.called)
        self.assertNotIn('edit', store.get('project', self.p['id'])['stale_stages'])


if __name__ == '__main__':
    unittest.main()
