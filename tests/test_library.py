import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import library, store
from app.main import app


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = patch.object(store, 'DATA', Path(self.temp.name))
        self.data.start()
        store.init()
        self.client = TestClient(app)

    def tearDown(self):
        self.data.stop()
        self.temp.cleanup()

    def test_defaults_are_seeded_and_parsed(self):
        prompts = {p['id']: p for p in self.client.get('/api/prompts').json()}
        self.assertTrue({'clarify', 'research', 'script', 'inspire', 'inspire-web'} <= set(prompts))
        self.assertEqual(prompts['research']['scope'], ['home', 'project', 'research'])
        self.assertIn('research.json', prompts['research']['body'])
        self.assertEqual({p['id'] for p in prompts.values() if 'script' in p['scope']},
                         {'script', 'script-natural', 'script-punchy', 'script-shorter', 'script-to-video'})
        self.assertIn('directions.json', prompts['research']['body'])
        self.assertTrue(prompts['research']['default'])
        self.assertFalse(prompts['research']['body'].startswith('---'))

    def test_prompts_can_be_created_edited_deleted_and_reset(self):
        created = self.client.post('/api/prompts', json={'label': '做封面', 'scope': ['project'], 'body': '为这条视频做三张封面图'}).json()
        self.assertTrue(created['id'].startswith('p-'))
        edited = self.client.put('/api/prompts/research', json={'label': '调研', 'scope': ['project', 'home'],
                                                                'description': '改过', 'body': '只查三个来源'}).json()
        self.assertEqual(edited['scope'], ['home', 'project'])
        self.assertEqual(library.prompt('research')['body'], '只查三个来源')
        self.assertEqual(self.client.delete('/api/prompts/research').status_code, 200)
        # A deleted default stays deleted; it is not seeded again.
        self.assertNotIn('research', [p['id'] for p in self.client.get('/api/prompts').json()])
        restored = self.client.post('/api/prompts/research/reset').json()
        self.assertIn('directions.json', restored['body'])
        for body in ({'label': '', 'scope': ['project'], 'body': 'x'}, {'label': 'x', 'scope': [], 'body': 'x'},
                     {'label': 'x', 'scope': ['elsewhere'], 'body': 'x'}, {'label': 'x', 'scope': ['project'], 'body': '  '},
                     {'label': '两\n行', 'scope': ['project'], 'body': 'x'}):
            with self.subTest(body=body):
                self.assertEqual(self.client.put('/api/prompts/custom', json=body).status_code, 400)
        self.assertEqual(self.client.put('/api/prompts/Bad_Name', json={'label': 'x', 'scope': ['project'], 'body': 'x'}).status_code, 400)
        self.assertEqual(self.client.post('/api/prompts/p-custom/reset').status_code, 400)
        self.assertEqual(self.client.get('/api/skills').status_code, 404)

    def test_untouched_copies_follow_new_defaults_and_edits_are_kept(self):
        shipped = Path(self.temp.name) / 'shipped'
        shipped.mkdir()
        (shipped / 'a.md').write_text('---\nlabel: A\nscope: project\n---\n旧版 A\n', encoding='utf-8')
        (shipped / 'b.md').write_text('---\nlabel: B\nscope: project\n---\n旧版 B\n', encoding='utf-8')
        with patch.object(library, 'DEFAULTS', shipped):
            library.folder()
            self.client.put('/api/prompts/b', json={'label': 'B', 'scope': ['project'], 'body': '我改过的 B'})
            (shipped / 'a.md').write_text('---\nlabel: A\nscope: project\n---\n新版 A\n', encoding='utf-8')
            (shipped / 'b.md').write_text('---\nlabel: B\nscope: project\n---\n新版 B\n', encoding='utf-8')
            self.assertEqual(library.prompt('a')['body'], '新版 A')
            self.assertEqual(library.prompt('b')['body'], '我改过的 B')
            self.client.post('/api/prompts/b/reset')
            (shipped / 'b.md').write_text('---\nlabel: B\nscope: project\n---\n第三版 B\n', encoding='utf-8')
            self.assertEqual(library.prompt('b')['body'], '第三版 B')

    def test_frontmatter_handles_windows_newlines(self):
        meta, body = library.split('---\r\nlabel: 名称\r\nscope: home\r\n---\r\n正文\r\n')
        self.assertEqual(meta, {'label': '名称', 'scope': 'home'})
        self.assertEqual(body, '正文\n')


if __name__ == '__main__':
    unittest.main()
