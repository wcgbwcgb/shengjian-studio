import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import claude_cli, inspiration, models, store, text_cli
from app.main import app

TOPIC = {'id': 't1', 'title': '松弛感从哪里来', 'question': '为什么有些歌让人放松？', 'key_facts': [{'claim': '测试事实'}],
         'angles': [{'id': f'angle-{i}', 'title': f'原始方向 {i}'} for i in (1, 2, 3)]}


class FakeModel:
    """Deterministic stand-in for models.call that records what each mode received."""
    def __init__(self):
        self.calls, self.batch = [], 0

    def __call__(self, task, mode, context, event, report):
        self.calls.append((mode, context))
        if mode == 'inspire':
            self.batch += 1
            return {'ideas': [{'title': f'第{self.batch}批 灵感{i}', 'description': '讲一个具体的小实验', 'hook': '先听这一段',
                               'tags': ['音乐'], 'sources': [{'url': 'https://example.org/a', 'title': 'A'}]} for i in range(3)]}, \
                [{'type': 'web_search_tool_result', 'content': [{'type': 'web_search_result', 'url': 'https://example.org/a'}]}], {}
        if mode == 'chat':
            if context['stage'] == 'clarify' and len([m for m in context['conversation'] if m['role'] == 'user']) < 1:
                return {'reply': '好的，先确认受众。', 'question': '这条视频给谁看？', 'options': ['学生', '上班族', ''],
                        'card': {'audience': '不懂乐理的人', 'bogus': 'x'}, 'action': 'revise_script'}, [], {}
            return {'reply': '需求清楚了。', 'question': '', 'options': [], 'card': {'tone': '轻松'},
                    'action': 'research', 'action_input': '对比实验', 'action_label': '开始研究'}, [], {}
        if mode == 'angles':
            return {'angles': [{'title': '新方向：讲一个人的故事', 'hook': '那天晚上…'}, {'title': ''}]}, [], {}
        if mode == 'research':
            return {'summary': '测试研究', 'topics': [TOPIC], 'sources': []}, [], {'model': 'fake'}
        if mode == 'script':
            return {'paragraphs': [{'id': 'p1', 'text': '开场', 'speaker': '旁白'}]}, [], {'model': 'fake'}
        raise AssertionError(mode)


class IdeationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patches = [patch.object(store, 'DATA', Path(self.temp.name)), patch.object(text_cli, 'enabled', return_value=True),
                        patch.object(text_cli, 'available', return_value=True),
                        patch.object(claude_cli, 'cli_command', return_value=['fixture'])]
        self.fake = FakeModel()
        self.patches.append(patch.object(models, 'call', self.fake))
        for item in self.patches:
            item.start()
        self.context = TestClient(app)
        self.client = self.context.__enter__()

    def tearDown(self):
        self.context.__exit__(None, None, None)
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def wait(self, predicate, seconds=10):
        for _ in range(int(seconds / .05)):
            value = predicate()
            if value:
                return value
            time.sleep(.05)
        self.fail('condition not reached')

    def generate(self, **body):
        response = self.client.post('/api/inspirations/generate', json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return self.wait(lambda: (lambda s: s if s['job']['status'] not in inspiration.ACTIVE else None)(self.client.get('/api/inspirations').json()))

    def wait_project_idle(self, project_id):
        return self.wait(lambda: all(t['status'] not in ('queued', 'running', 'cancelling') for t in store.listing('task', project_id)))

    def test_batches_replace_undo_dismiss_and_keep_favorites(self):
        first = self.generate(web=True, direction='音乐')
        self.assertEqual(first['job']['status'], 'completed', first['job'].get('error'))
        self.assertEqual([i['title'] for i in first['ideas']], ['第1批 灵感0', '第1批 灵感1', '第1批 灵感2'])
        self.assertEqual(first['ideas'][0]['sources'][0]['verification'], 'search_only')
        favorite = first['ideas'][0]
        self.client.post('/api/inspirations/favorites', json={'id': favorite['id']})
        second = self.generate(feedback='太学术了')
        self.assertTrue(second['ideas'][0]['title'].startswith('第2批'))
        self.assertEqual(second['previous'], 3)
        mode, context = self.fake.calls[-1]
        self.assertFalse(context['web'])
        self.assertIn('第1批 灵感1', context['avoid_titles'])
        self.assertEqual(context['feedback'], '太学术了')
        self.assertIn('第1批 灵感0', context['liked'])
        self.assertEqual(second['ideas'][0]['sources'], [])
        undone = self.client.post('/api/inspirations/undo').json()
        self.assertTrue(undone['ideas'][0]['title'].startswith('第1批'))
        self.assertTrue(undone['ideas'][0]['saved'])
        self.client.post(f"/api/inspirations/{undone['ideas'][1]['id']}/dismiss")
        third = self.generate()
        self.assertTrue(third['ideas'][0]['title'].startswith('第3批'))
        self.assertIn('第1批 灵感1', self.fake.calls[-1][1]['avoid_titles'])
        self.assertIn('太学术了', self.fake.calls[-1][1]['recent_feedback'])
        # Batch 2 was the undo target before batch 3 and is now discarded; batch 1 is the undoable one.
        self.assertEqual(third['previous'], 2)
        self.assertEqual([f['title'] for f in third['favorites']], ['第1批 灵感0'])
        self.client.post('/api/inspirations/undo')
        self.client.post('/api/inspirations/undo')
        self.assertTrue(self.client.get('/api/inspirations').json()['ideas'][0]['title'].startswith('第3批'))

    def test_clarifying_conversation_builds_card_and_proposes_research(self):
        created = self.client.post('/api/creations', json={'idea': '讲讲松弛感', 'clarify': True}).json()
        self.assertTrue(created['clarify'])
        pid = created['project']['id']
        self.wait_project_idle(pid)
        project = store.get('project', pid)
        self.assertEqual(project['workspace']['card'], {'audience': '不懂乐理的人'})
        reply = next(m for m in store.listing('message', pid) if m['role'] == 'assistant')
        self.assertEqual(reply['options'], ['学生', '上班族'])
        self.assertEqual(reply['action'], 'none')  # revise_script is not available before a script exists
        self.assertEqual(self.client.post(f'/api/projects/{pid}/workspace/chat/action', json={'message_id': reply['id']}).status_code, 400)
        self.client.patch(f'/api/projects/{pid}/workspace/card', json={'card': {'avoid': '术语', 'audience': ''}})
        self.assertEqual(store.get('project', pid)['workspace']['card'], {'avoid': '术语'})
        self.assertEqual(self.client.post(f'/api/projects/{pid}/workspace/chat', json={'message': '上班族，想轻松一点'}).status_code, 200)
        self.wait_project_idle(pid)
        proposal = next(m for m in store.listing('message', pid) if m['role'] == 'assistant')
        self.assertEqual(proposal['action'], 'research')
        self.assertEqual(self.fake.calls[-1][1]['conversation'][-1]['text'], '好的，先确认受众。')
        task = self.client.post(f'/api/projects/{pid}/workspace/chat/action', json={'message_id': proposal['id']}).json()
        self.assertEqual(task['kind'], 'research')
        self.assertIn('要避免：术语', task['payload']['prompt'])
        self.assertIn('语气风格：轻松', task['payload']['prompt'])
        self.assertIn('研究重点：对比实验', task['payload']['prompt'])
        self.wait_project_idle(pid)
        self.assertEqual(self.client.post(f'/api/projects/{pid}/workspace/chat/action', json={'message_id': proposal['id']}).status_code, 400)

    def test_more_angles_custom_angle_and_adjusted_angle(self):
        pid = self.client.post('/api/projects', json={'name': '方向'}).json()['id']
        research = store.put('version', {'stage': 'research', 'result': {'topics': [TOPIC], 'sources': []}}, pid)
        response = self.client.post(f'/api/projects/{pid}/workspace/angles',
                                    json={'version_id': research['id'], 'topic_id': 't1', 'feedback': '都太严肃'})
        self.assertEqual(response.status_code, 200, response.text)
        self.wait_project_idle(pid)
        context = self.fake.calls[-1][1]
        self.assertEqual(context['feedback'], '都太严肃')
        self.assertEqual([a['title'] for a in context['existing_angles']], ['原始方向 1', '原始方向 2', '原始方向 3'])
        extra = store.get('project', pid)['workspace']['extra_angles']['t1']
        self.assertEqual([a['title'] for a in extra], ['新方向：讲一个人的故事'])
        chat = [m['text'] for m in reversed(store.listing('message', pid)) if m.get('stage') == 'chat']
        self.assertEqual(chat[0], '换几个方向：都太严肃')
        task = self.client.post(f'/api/projects/{pid}/workspace/angle', json={'version_id': research['id'], 'topic_id': 't1',
                                'angle_id': extra[0]['id'], 'note': '开场换成一个故事'}).json()
        self.assertIn('开场换成一个故事', task['payload']['prompt'])
        self.assertIn('新方向：讲一个人的故事', task['payload']['prompt'])
        self.wait_project_idle(pid)
        task = self.client.post(f'/api/projects/{pid}/workspace/angle/custom', json={'title': '我自己的方向', 'detail': '从合唱团讲起'}).json()
        self.assertIn('我自己的方向', task['payload']['prompt'])
        self.wait_project_idle(pid)
        project = store.get('project', pid)
        self.assertEqual(project['workspace']['angle']['title'], '我自己的方向')
        self.assertTrue(any(a.get('custom') for a in project['workspace']['extra_angles']['t1']))


if __name__ == '__main__':
    unittest.main()
