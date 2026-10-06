"""Regression tests for the product audit fixes."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import config, jobs, store, text_cli
from app.main import app


class ResultNormalizationTests(unittest.TestCase):
    """The real failure: valid topics returned next to empty optional message fields."""

    def test_valid_records_win_over_empty_optional_fields(self):
        result = {'topics': [{'id': 't', 'title': 'x'}], 'needs_clarification': '', 'advice': '  '}
        self.assertEqual(text_cli.validate_result(result, 'research'), {'topics': [{'id': 't', 'title': 'x'}]})

    def test_valid_records_win_over_a_non_empty_message(self):
        result = {'paragraphs': [{'id': 'p', 'text': 'hi'}], 'advice': '可以更短'}
        self.assertNotIn('advice', text_cli.validate_result(result, 'script'))

    def test_real_clarification_is_kept(self):
        result = text_cli.validate_result({'needs_clarification': '请补充平台'}, 'research')
        self.assertEqual(result['needs_clarification'], '请补充平台')

    def test_empty_everything_still_fails(self):
        with self.assertRaises(ValueError):
            text_cli.validate_result({'topics': [], 'needs_clarification': ''}, 'research')

    def test_api_path_normalizes_too(self):
        self.assertEqual(jobs.normalize_result({'topics': [{'id': 'a'}], 'advice': ''}, 'research'), {'topics': [{'id': 'a'}]})


class ProjectManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = patch.object(store, 'DATA', Path(self.temp.name))
        self.data.start()
        self.no_key = patch.object(config, 'has_api_key', return_value=False)
        self.no_key.start()
        self.context = TestClient(app)
        self.client = self.context.__enter__()

    def tearDown(self):
        self.context.__exit__(None, None, None)
        self.no_key.stop()
        self.data.stop()
        self.temp.cleanup()

    def create(self, idea='测试想法', **extra):
        return self.client.post('/api/creations', json={'idea': idea, **extra}).json()['project']

    def test_duration_and_orientation_written_in_the_idea_are_respected(self):
        p = self.create('做一个 30 秒的横屏视频', duration=75, aspect='9:16')
        self.assertEqual(p['requirements'], {'duration': 30, 'aspect': '16:9'})
        self.assertEqual(self.create('普通想法', duration=75)['requirements']['duration'], 75)

    def test_delete_removes_records_and_files_but_keeps_usage(self):
        p = self.create()
        folder = store.project_dir(p['id'])
        (folder / 'note.txt').write_text('x')
        store.put('usage', {'input_tokens': 1, 'output_tokens': 1, 'searches': 0, 'estimated_usd': .5}, p['id'])
        store.put('message', {'role': 'user', 'text': 'hi'}, p['id'])
        response = self.client.delete('/api/projects/' + p['id'])
        self.assertEqual(response.status_code, 200)
        self.assertFalse(folder.exists())
        self.assertEqual(store.listing('message', p['id']), [])
        self.assertEqual(len(store.listing('usage', p['id'])), 1)
        self.assertNotIn(p['id'], [x['id'] for x in self.client.get('/api/projects').json()])

    def test_delete_is_refused_while_a_task_runs(self):
        p = self.create()
        store.put('task', {'kind': 'research', 'status': 'running', 'logs': []}, p['id'])
        self.assertEqual(self.client.delete('/api/projects/' + p['id']).status_code, 400)

    def test_delete_keeps_storage_still_used_by_a_public_asset(self):
        p = self.create()
        folder = store.project_dir(p['id'])
        store.put('asset', {'name': 'a.png', 'path': 'assets/a.png', 'scope': 'public', 'project_id': None,
                            'storage_project_id': p['id']})
        self.assertTrue(self.client.delete('/api/projects/' + p['id']).json()['kept_files'])
        self.assertTrue(folder.exists())

    def test_listing_reports_latest_task_and_rename_is_sticky(self):
        p = self.create()
        store.put('task', {'kind': 'research', 'status': 'failed', 'error': 'boom', 'logs': []}, p['id'])
        listed = next(x for x in self.client.get('/api/projects').json() if x['id'] == p['id'])
        self.assertEqual(listed['last_task']['status'], 'failed')
        renamed = self.client.patch('/api/projects/' + p['id'], json={'name': '我的名字'}).json()
        self.assertTrue(renamed['name_custom'])


if __name__ == '__main__':
    unittest.main()
