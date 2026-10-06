import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import claude_cli, inspiration, store
from app.main import app

FAKE = Path(__file__).with_name('fake_claude.py')
IDEAS = {'ideas': [
    {'title': '夜跑的人', 'description': '城市夜跑', 'hook': '晚上十点', 'tags': ['城市'],
     'sources': [{'url': 'https://example.org/read-page', 'title': '读过'}, {'url': 'https://example.org/never-opened'}]},
    {'title': '菜市场的声音', 'format': '街访'}, {'description': '没有标题会被忽略'}]}


class InspirationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patches = [patch.object(store, 'DATA', Path(self.temp.name)),
                        patch.object(claude_cli, 'cli_command', return_value=[sys.executable, str(FAKE)]),
                        patch.dict(os.environ, {'CLI_TEST_MODE': 'text', 'CLI_TEST_FILES': json.dumps({'ideas.json': json.dumps(IDEAS, ensure_ascii=False)}, ensure_ascii=False)})]
        for p in self.patches:
            p.start()
        store.init()
        self.client = TestClient(app)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def generate(self, **body):
        response = self.client.post('/api/inspirations/generate', json=body)
        self.assertEqual(response.status_code, 200, response.text)
        for _ in range(400):
            job = self.client.get('/api/inspirations').json()['job']
            if job['status'] not in inspiration.ACTIVE:
                return job
            time.sleep(.05)
        self.fail('inspiration did not finish')

    def received(self):
        return json.loads((inspiration.folder() / '.claude' / 'received.json').read_text(encoding='utf-8'))

    def test_library_prompt_plus_creator_text_produces_cards(self):
        job = self.generate(web=True, direction='城市生活', feedback='太严肃')
        self.assertEqual(job['status'], 'completed', job.get('error'))
        prompt = self.received()['prompt']
        self.assertTrue(prompt.startswith(inspiration.library.prompt('inspire-web')['body']))
        self.assertIn('想探索的方向：城市生活', prompt)
        self.assertIn('我对上一批的意见：太严肃', prompt)
        ideas = self.client.get('/api/inspirations').json()['ideas']
        self.assertEqual([i['title'] for i in ideas], ['夜跑的人', '菜市场的声音'])
        self.assertEqual([s['verification'] for s in ideas[0]['sources']], ['search_only', 'unverified'])
        # This batch's feedback is in the prompt; later batches read it from 偏好/反馈.md.
        self.generate()
        self.assertIn('太严肃', (inspiration.folder() / '偏好' / '反馈.md').read_text(encoding='utf-8'))

    def test_dismissed_and_shown_titles_reach_claude_as_files(self):
        self.generate()
        first = self.client.get('/api/inspirations').json()['ideas'][0]
        self.client.post(f"/api/inspirations/{first['id']}/dismiss")
        self.generate()
        prefs = inspiration.folder() / '偏好'
        self.assertIn('夜跑的人', (prefs / '不感兴趣.md').read_text(encoding='utf-8'))
        self.assertIn('菜市场的声音', (prefs / '已出现.md').read_text(encoding='utf-8'))
        self.assertEqual(self.client.get('/api/inspirations').json()['previous'], 1)

    def test_missing_ideas_file_fails_and_starting_a_project_keeps_the_idea(self):
        with patch.dict(os.environ, {'CLI_TEST_FILES': '{}', 'CLI_TEST_REPLY': '我需要先知道方向'}):
            job = self.generate()
        self.assertEqual(job['status'], 'failed')
        self.assertIn('ideas.json', job['error'])
        self.assertIn('我需要先知道方向', job['error'])
        job = self.generate()
        idea = self.client.get('/api/inspirations').json()['ideas'][0]
        project = self.client.post('/api/inspirations/create', json={'id': idea['id']}).json()['project']
        self.assertEqual(project['name'], '夜跑的人')
        self.assertIn('开场：晚上十点', project['idea'])
        self.assertEqual(store.listing('task', project['id']), [])


if __name__ == '__main__':
    unittest.main()
