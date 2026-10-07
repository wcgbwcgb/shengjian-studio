import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import claude_cli, docs, inspiration, models, store, text_cli
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
                        'action': 'revise_script'}, [], {}
            return {'reply': '想法清楚了。', 'question': '', 'options': [], 'action': 'write_idea',
                    'action_input': '# 我的想法\n讲给上班族的松弛感', 'action_label': '写入 我的idea.md'}, [], {}
        if mode == 'idea':
            return {'idea': '# 润色后的想法\n松弛感从哪里来'}, [], {}
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

    def test_conversation_clarifies_the_idea_and_writes_my_idea_md(self):
        created = self.client.post('/api/creations', json={'idea': '讲讲松弛感', 'start': False}).json()
        pid = created['project']['id']
        self.assertIsNone(created['task'])
        self.assertEqual(store.get('project', pid)['workspace']['brainstorm'], '讲讲松弛感')
        self.assertEqual(self.client.post(f'/api/projects/{pid}/workspace/chat', json={'message': '讲讲松弛感'}).status_code, 200)
        self.wait_project_idle(pid)
        mode, context = self.fake.calls[-1]
        self.assertEqual((mode, context['brainstorm'], context['my_idea']), ('chat', '讲讲松弛感', ''))
        reply = next(m for m in store.listing('message', pid) if m['role'] == 'assistant')
        self.assertEqual(reply['options'], ['学生', '上班族'])
        self.assertEqual(reply['action'], 'none')  # revise_script is not available before a script exists
        self.assertEqual(self.client.post(f'/api/projects/{pid}/workspace/chat/action', json={'message_id': reply['id']}).status_code, 400)
        self.client.post(f'/api/projects/{pid}/workspace/chat', json={'message': '上班族，想轻松一点'})
        self.wait_project_idle(pid)
        proposal = next(m for m in store.listing('message', pid) if m['role'] == 'assistant')
        self.assertEqual(proposal['action'], 'write_idea')
        self.assertEqual(self.fake.calls[-1][1]['conversation'][-1]['text'], '好的，先确认受众。')
        written = self.client.post(f'/api/projects/{pid}/workspace/chat/action', json={'message_id': proposal['id']})
        self.assertEqual(written.status_code, 200, written.text)
        idea = self.client.get(f'/api/projects/{pid}').json()['docs']['idea']
        self.assertEqual((idea['text'], idea['origin']), ('# 我的想法\n讲给上班族的松弛感\n', 'chat'))
        self.assertTrue(docs.path(pid, 'idea').is_file())
        self.assertEqual(self.client.post(f'/api/projects/{pid}/workspace/chat/action', json={'message_id': proposal['id']}).status_code, 400)
        # Research and writing never start from the conversation without the creator seeing the text.
        suggestion = store.put('message', {'stage': 'chat', 'role': 'assistant', 'text': '去调研吧', 'action': 'research',
                                           'action_input': '对比实验'}, pid)
        self.assertEqual(self.client.post(f'/api/projects/{pid}/workspace/chat/action', json={'message_id': suggestion['id']}).status_code, 400)
        prompt = self.client.post(f'/api/projects/{pid}/workspace/compose', json={'module': 'research', 'request': '对比实验'}).json()['prompt']
        task = self.client.post(f'/api/projects/{pid}/workspace/run', json={'module': 'research', 'request': '对比实验',
                                'prompt': prompt, 'message_id': suggestion['id']}).json()
        self.assertEqual(store.get('message', suggestion['id'])['action_task_id'], task['id'])
        self.wait_project_idle(pid)

    def test_compose_shows_the_exact_text_and_run_sends_it(self):
        pid = self.client.post('/api/creations', json={'name': '松弛感', 'idea': '想讲松弛感', 'start': False}).json()['project']['id']
        url = f'/api/projects/{pid}/workspace'
        # Without 我的idea.md the research request carries the idea itself and references nothing.
        empty = self.client.post(url + '/compose', json={'module': 'research'}).json()
        self.assertEqual(empty['refs'], [])
        self.assertIn('我的想法：想讲松弛感', empty['request'])
        self.assertTrue(empty['prompt'].startswith(models.SYSTEM))
        self.client.put(url + '/docs/idea', json={'text': '# 我的想法\n讲给上班族'})
        composed = self.client.post(url + '/compose', json={'module': 'research'}).json()
        self.assertEqual(composed['refs'], ['idea'])
        self.assertIn('【参考：我的idea.md】\n# 我的想法\n讲给上班族', composed['prompt'])
        self.assertIn('【这次的要求】\n' + composed['request'], composed['prompt'])
        self.assertNotIn('我的idea.md', self.client.post(url + '/compose', json={'module': 'research', 'refs': []}).json()['prompt'])
        self.assertEqual(self.client.post(url + '/compose', json={'module': 'research', 'refs': ['bogus']}).status_code, 400)

        task = self.client.post(url + '/run', json={'module': 'research', 'request': '调研对比实验',
                                                    'prompt': '我改过的原文', 'refs': ['idea']}).json()
        self.assertEqual((task['kind'], task['payload']['prompt'], task['payload']['refs']), ('research', '调研对比实验', ['idea']))
        self.assertTrue(task['prompt_override'])
        self.assertIsNone(task['edited_from'])
        self.assertEqual(claude_cli.prompt_override(task), '我改过的原文')
        self.wait_project_idle(pid)
        research_doc = docs.read(pid, 'research')
        self.assertIn('## 松弛感从哪里来', research_doc)
        self.assertIn('### 角度 2：原始方向 2', research_doc)
        self.assertTrue(docs.meta(store.get('project', pid))['research']['origin'].startswith('research:'))

        # A hand-edited 调研.md is not overwritten by the next result until the creator asks.
        self.client.put(url + '/docs/research', json={'text': '我自己整理的调研'})
        self.client.post(url + '/run', json={'module': 'research', 'prompt': '再调研一次'})
        self.wait_project_idle(pid)
        state = self.client.get(f'/api/projects/{pid}').json()['docs']['research']
        self.assertEqual(state['text'], '我自己整理的调研')
        self.assertTrue(state['pending'])
        synced = self.client.post(url + '/docs/research/sync').json()
        self.assertIn('## 松弛感从哪里来', synced['text'])
        self.assertFalse(synced['pending'])
        self.assertEqual(self.client.post(url + '/docs/research/undo').json()['text'], '我自己整理的调研')

        # Writing from a research angle records the angle; the text is the creator's.
        research = next(v for v in store.listing('version', pid) if v['stage'] == 'research')
        task = self.client.post(url + '/run', json={'module': 'script', 'prompt': '按角度写', 'refs': ['idea', 'research'],
                                'angle': {'version_id': research['id'], 'topic_id': 't1', 'angle_id': 'angle-2'}}).json()
        self.assertEqual(task['payload']['angle'], '原始方向 2')
        self.wait_project_idle(pid)
        project = store.get('project', pid)
        self.assertEqual(project['workspace']['angle']['title'], '原始方向 2')
        self.assertIn('开场', docs.read(pid, 'script'))
        video = self.client.post(url + '/compose', json={'module': 'video', 'request': '做成 30 秒'}).json()
        self.assertEqual(video['prompt'], '做成 30 秒\n\n参考当前文件夹里的 文案.md。')
        both = self.client.post(url + '/compose', json={'module': 'video', 'request': '做', 'refs': ['research', 'script']}).json()
        self.assertTrue(both['prompt'].endswith('参考当前文件夹里的 调研.md、文案.md。'))

    def test_script_does_not_need_research(self):
        pid = self.client.post('/api/creations', json={'name': '我的故事', 'start': False}).json()['project']['id']
        url = f'/api/projects/{pid}/workspace'
        composed = self.client.post(url + '/compose', json={'module': 'script'}).json()
        self.assertEqual(composed['refs'], [])
        self.assertIn('写一条约 60 秒的竖屏短视频文案', composed['request'])
        task = self.client.post(url + '/run', json={'module': 'script', 'prompt': composed['prompt']}).json()
        self.wait_project_idle(pid)
        self.assertEqual(store.get('task', task['id'])['status'], 'completed')
        self.assertEqual(self.client.get(f'/api/projects/{pid}').json()['docs']['script']['text'].splitlines()[0], '# 文案')

    def test_brainstorm_kept_as_is_or_polished(self):
        pid = self.client.post('/api/creations', json={'name': '我的故事', 'start': False}).json()['project']['id']
        url = f'/api/projects/{pid}/workspace'
        self.client.put(url + '/brainstorm', json={'text': '会计转行做配音\n第一次录音很紧张'})
        kept = self.client.put(url + '/docs/idea', json={'text': '会计转行做配音\n第一次录音很紧张', 'origin': 'brainstorm'}).json()
        self.assertEqual(kept['origin'], 'brainstorm')
        self.assertEqual(self.client.put(url + '/docs/research', json={'text': 'x', 'origin': 'brainstorm'}).status_code, 400)
        composed = self.client.post(url + '/compose', json={'module': 'polish'}).json()
        self.assertIn('【我的零散想法】\n会计转行做配音\n第一次录音很紧张', composed['prompt'])
        self.assertTrue(composed['prompt'].startswith(models.SYSTEM + models.SCHEMAS['idea']))
        task = self.client.post(url + '/run', json={'module': 'polish', 'prompt': composed['prompt']}).json()
        self.assertEqual(task['kind'], 'polish')
        self.wait_project_idle(pid)
        idea = self.client.get(f'/api/projects/{pid}').json()['docs']['idea']
        self.assertEqual((idea['text'], idea['origin'], idea['can_undo']), ('# 润色后的想法\n松弛感从哪里来\n', 'polish', True))
        self.assertEqual(self.client.post(url + '/docs/idea/undo').json()['text'], '会计转行做配音\n第一次录音很紧张')

    def test_materials_say_what_each_file_is_for(self):
        pid = self.client.post('/api/creations', json={'name': '我的故事', 'start': False}).json()['project']['id']
        url = f'/api/projects/{pid}'
        upload = lambda name, kind=None: self.client.post(url + '/assets' + (f'?type={kind}' if kind else ''),
                                                          files={'file': (name, name.encode() * 10)}).json()
        interview, still, style, bgm = upload('采访.mp4'), upload('录音棚.png'), upload('风格参考.mp4'), upload('bgm.mp3', 'Music')
        self.assertEqual(self.client.get(url).json()['docs']['materials']['text'].count('## '), 2)  # 剪辑素材 + 配乐
        self.client.patch(url + f"/workspace/materials/{style['id']}", json={'purpose': 'reference', 'note': '学它的开场节奏'})
        self.client.patch(url + f"/workspace/materials/{interview['id']}", json={'note': '只用 0:30–1:10，保留原声'})
        self.assertEqual(self.client.patch(url + f"/workspace/materials/{bgm['id']}", json={'purpose': 'nope'}).status_code, 400)
        ordered = self.client.put(url + '/workspace/materials', json={'order': [still['id'], interview['id']], 'sequence': 'fixed'}).json()
        self.assertEqual(ordered['order'][:2], [still['id'], interview['id']])
        self.assertEqual(self.client.put(url + '/workspace/materials', json={'order': ['bogus']}).status_code, 400)
        text = self.client.get(url).json()['docs']['materials']['text']
        self.assertIn('## 剪辑素材\n剪辑顺序：按下面的顺序\n1. 录音棚.png（图片）\n2. 采访.mp4（视频） 只用 0:30–1:10，保留原声', text)
        self.assertIn('## 参考（不剪进成片）\n- 风格参考.mp4（视频） 学它的开场节奏', text)
        self.assertIn('## 配乐\n- bgm.mp3（音乐）', text)
        self.assertTrue(docs.path(pid, 'materials').is_file())
        # The list is the source: the file itself is not edited by hand.
        self.assertEqual(self.client.put(url + '/workspace/docs/materials', json={'text': 'x'}).status_code, 400)
        # Video references 文案.md and 素材.md by default; polishing may only reference the materials.
        self.assertEqual(self.client.post(url + '/workspace/compose', json={'module': 'video', 'request': '做'}).json()['prompt'],
                         '做\n\n参考当前文件夹里的 素材.md。')
        polish = self.client.post(url + '/workspace/compose', json={'module': 'polish', 'refs': ['materials', 'research']}).json()
        self.assertEqual(polish['refs'], ['materials'])
        self.assertIn('【参考：素材.md】\n# 素材', polish['prompt'])
        # The copies Claude Code gets carry the names 素材.md uses.
        project = store.get('project', pid)
        self.assertEqual(list(docs.material_names(docs.materials(project)).values()),
                         ['录音棚.png', '采访.mp4', '风格参考.mp4', 'bgm.mp3'])
        stored = store.get('asset', still['id'])
        self.assertEqual(self.client.delete(url + f"/workspace/materials/{still['id']}").status_code, 200)
        self.assertFalse(store.project_file(pid, stored['path']).exists())
        self.assertNotIn('录音棚.png', self.client.get(url).json()['docs']['materials']['text'])

    def test_more_angles_reuse_research(self):
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
        task = self.client.post(f'/api/projects/{pid}/workspace/run', json={
            'module': 'script', 'prompt': '按新方向写', 'angle': {'version_id': research['id'], 'topic_id': 't1', 'angle_id': extra[0]['id']}}).json()
        self.assertEqual(task['payload']['angle'], '新方向：讲一个人的故事')
        self.wait_project_idle(pid)

if __name__ == '__main__':
    unittest.main()
