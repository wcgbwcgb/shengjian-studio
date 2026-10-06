import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import claude_cli, cli_guard, config, media, projects, store
from app.main import app

FFMPEG, FFPROBE = media.executable('ffmpeg'), media.executable('ffprobe')
FAKE = Path(__file__).with_name('fake_claude.py')


class CliPolicyTests(unittest.TestCase):
    def test_large_input_copy_checks_cancellation(self):
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder) / 'original.mp4', Path(folder) / 'copy.mp4'
            source.write_bytes(b'original media')
            event = threading.Event()
            claude_cli.copy_input(source, target, event)
            self.assertEqual(target.read_bytes(), source.read_bytes())
            event.set()
            with self.assertRaises(media.Cancelled):
                claude_cli.copy_input(source, target, event)
            self.assertEqual(source.read_bytes(), b'original media')

    def guard(self, folder):
        root = run = Path(folder) / 'claude'
        toolbox, temp = Path(folder) / 'toolbox', Path(folder) / 'temp'
        env = Path(folder) / 'workbench' / '.env'
        def decide(name, tool_input, cwd=None):
            event = {'cwd': str(cwd or root), 'tool_name': name, 'tool_input': tool_input}
            return cli_guard.decide(event, root, run, {'workbench_python': str(Path(folder) / 'venv' / 'python.exe')},
                                    toolbox, temp, 4242, str(env))[0]
        return root, run, toolbox, env, decide

    def test_file_tools_are_open_except_protected_inputs_and_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            root, run, toolbox, env, decide = self.guard(folder)
            file = lambda name, path: decide(name, {'file_path': str(path)})
            self.assertTrue(file('Read', run / '素材' / 'a.mp4'))
            self.assertTrue(file('Read', Path(folder) / 'elsewhere' / 'reference.png'))
            self.assertTrue(file('Write', run / 'work' / 'edit.py'))
            self.assertTrue(file('Write', run / 'final.mp4'))
            self.assertTrue(file('Write', toolbox / 'templates' / 'remotion' / 'package.json'))
            self.assertFalse(file('Write', run / '素材' / 'a.mp4'))
            self.assertFalse(file('Write', Path(folder) / 'videos' / 'other' / 'video.mp4'))
            self.assertFalse(file('Write', Path(folder) / 'Documents' / 'notes.txt'))
            self.assertFalse(file('Read', env))
            self.assertFalse(file('Read', Path(folder) / 'data' / 'machine-config.json'))
            self.assertFalse(file('Read', Path.home() / '.ssh' / 'id_ed25519'))
            self.assertTrue(decide('WebSearch', {'query': 'motion design references'}))
            self.assertTrue(decide('Task', {'prompt': 'render the intro'}))

    def test_shell_is_open_but_destructive_commands_are_blocked(self):
        with tempfile.TemporaryDirectory(prefix='cli space ') as folder:
            root, run, toolbox, env, decide = self.guard(folder)
            bash = lambda command, cwd=None: decide('Bash', {'command': command}, cwd)
            output = (run / 'output' / 'v.mp4').as_posix()
            for command in [f'ffmpeg -f lavfi -i "color=red:s=320x240" -filter_complex "[0:v]split[a][b];[a]format=gray[c];[c][b]hstack" "{output}"',
                            'python -c "print(1)"', 'pip install pillow numpy && python work/render.py',
                            'npx remotion render src/index.ts Main out/video.mp4 | tail -5',
                            'npm install remotion @remotion/cli', 'curl -L -o work/font.ttf https://example.org/font.ttf',
                            f'rm -rf "{(run / "work" / "frames").as_posix()}"', f'rm -rf "{(toolbox / "cache").as_posix()}"',
                            'taskkill /PID 999 /F', 'echo done > work/log.txt']:
                with self.subTest(command=command):
                    self.assertTrue(bash(command, run))
            for command in ['rm -rf ../other', 'cd .. && rm -rf other', f'rm -rf "{Path(folder).as_posix()}"',
                            'Remove-Item -Recurse -Force C:/Users/someone/Documents', r'del /s /q C:\Windows\Temp',
                            'find / -name "*.mp4" -delete', 'mv ../../agent ../trash', 'format c:', 'diskpart',
                            r'reg delete HKCU\Software\x /f', 'npm install -g some-package', 'taskkill /IM python.exe /F',
                            'kill 4242', 'git push origin main', f'cat "{env.as_posix()}"',
                            'type C:/data/machine-config.json', 'echo x > C:/Windows/evil.txt', 'shutdown /s /t 0',
                            f'"{(Path(folder) / "venv" / "python.exe").as_posix()}" -m pip install requests',
                            'powershell -Command "Remove-Item -Recurse C:/Users"', 'rm -rf $HOME/projects']:
                with self.subTest(command=command):
                    self.assertFalse(bash(command, run))
            self.assertFalse(decide('PowerShell', {'command': 'Get-ChildItem | Remove-Item -Recurse -Force ~'}, run))

    def test_environment_does_not_inherit_workbench_credentials(self):
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'secret', 'ANTHROPIC_BASE_URL': 'https://proxy.invalid',
                                     'CLAUDE_CODE_OAUTH_TOKEN': 'secret', 'CLAUDE_CONFIG_DIR': '/own/cli/config'}):
            env = claude_cli.environment()
        self.assertNotIn('ANTHROPIC_API_KEY', env)
        self.assertNotIn('ANTHROPIC_BASE_URL', env)
        self.assertNotIn('CLAUDE_CODE_OAUTH_TOKEN', env)
        self.assertEqual(env['CLAUDE_CONFIG_DIR'], '/own/cli/config')




class Workbench(unittest.TestCase):
    """An isolated data folder, the fake CLI and a test client."""
    mode = 'success'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cli workbench ')
        self.data = patch.object(store, 'DATA', Path(self.temp.name))
        self.data.start()
        self.command = patch.object(claude_cli, 'cli_command', return_value=[sys.executable, str(FAKE)])
        self.command.start()
        self.env = patch.dict(os.environ, {'CLI_TEST_MODE': self.mode, 'CLI_TEST_FILES': '{}',
                                           'ANTHROPIC_API_KEY': 'sk-ant-test-never-forward-this-key'})
        self.env.start()
        if FFMPEG and FFPROBE:
            config.save_tools({'ffmpeg_path': FFMPEG, 'ffprobe_path': FFPROBE})
        self.context = TestClient(app)
        self.client = self.context.__enter__()
        self.project = self.client.post('/api/projects', json={'name': 'CLI 视频'}).json()['project']
        self.url = '/api/projects/' + self.project['id']

    def tearDown(self):
        self.context.__exit__(None, None, None)
        self.env.stop()
        self.command.stop()
        self.data.stop()
        self.temp.cleanup()

    def wait_task(self, ident, seconds=20):
        for _ in range(int(seconds / .05)):
            task = store.get('task', ident)
            if task['status'] not in ('queued', 'running', 'cancelling'):
                return task
            time.sleep(.05)
        self.fail('CLI task did not finish')

    def run_prompt(self, prompt='制作测试视频', **body):
        response = self.client.post(self.url + '/run', json={'prompt': prompt, **body})
        self.assertEqual(response.status_code, 200, response.text)
        return self.wait_task(response.json()['id'])

    def work(self, project_id=None):
        return store.project_file(project_id or self.project['id'], claude_cli.WORKDIR)

    def received(self, project_id=None):
        return json.loads((self.work(project_id) / '.claude' / 'received.json').read_text(encoding='utf-8'))


class TextRunTests(Workbench):
    mode = 'text'

    def test_prompt_is_sent_verbatim_with_no_added_rules(self):
        prompt = '调研一下，保留引号 " 和 shell 文本 $(whoami) & literal'
        task = self.run_prompt(prompt)
        self.assertEqual(task['status'], 'completed', task.get('error'))
        received = self.received()
        self.assertEqual(received['prompt'], prompt)
        self.assertNotIn(prompt, received['argv'])
        self.assertFalse(received['has_api_key'])
        for flag in ('--bare', '--dangerously-skip-permissions', '--restricted', '--append-system-prompt',
                     '--system-prompt', '--json-schema'):
            self.assertNotIn(flag, received['argv'])
        self.assertEqual(received['argv'][received['argv'].index('--tools') + 1], 'default')
        # Nothing is placed in the working folder for Claude to pick up besides the materials.
        self.assertFalse((self.work() / '.claude' / 'skills').exists())
        self.assertNotIn('PRIVATE_REASONING', json.dumps(task))
        out = task['output']
        self.assertEqual(out['reply'], 'fixture video complete')
        self.assertEqual(out['web'], ['https://example.org/read-page'])
        self.assertIsNone(out['version_id'])
        self.assertEqual(store.listing('version', self.project['id']), [])

    def test_followups_continue_the_conversation_unless_fresh(self):
        first = self.run_prompt('第一步')
        second = self.run_prompt('第二步')
        args = self.received()['argv']
        self.assertEqual(args[args.index('--resume') + 1], first['cli_session_id'])
        self.assertIn('--fork-session', args)
        self.assertNotEqual(second['cli_session_id'], first['cli_session_id'])
        self.run_prompt('重新开始', fresh=True)
        self.assertNotIn('--resume', self.received()['argv'])

    def test_tab_files_are_read_and_text_files_can_be_edited(self):
        directions = {'directions': [{'title': '方向一', 'hook': '开场'}, {'title': '方向二'}, {'reason': '没有标题会被忽略'}]}
        research = {'title': '夜跑', 'summary': 7, 'what_happened': ['不是字符串'],
                    'facts': [{'claim': '事实一', 'sources': ['https://example.org/a', 'ftp://bad']}, {'sources': []}, '坏数据'],
                    'viewpoints': ['观点', 3, None], 'sources': [{'url': 'https://example.org/a', 'title': '甲'}, {'url': 'javascript:x'}]}
        files = {'调研.md': '# 调研\n事实', 'directions.json': json.dumps(directions, ensure_ascii=False),
                 'research.json': json.dumps(research, ensure_ascii=False), '脚本.md': '# 脚本\n第一句',
                 'site/index.html': '<script>fetch("/api/projects")</script>', 'node_modules/x/index.js': 'skip'}
        with patch.dict(os.environ, {'CLI_TEST_FILES': json.dumps(files, ensure_ascii=False)}):
            task = self.run_prompt('调研并给方向')
        self.assertEqual(task['status'], 'completed', task.get('error'))
        written = ['directions.json', 'research.json', 'site/index.html', '脚本.md', '调研.md']
        self.assertEqual(sorted(task['output']['files']), written)
        detail = self.client.get(self.url).json()
        self.assertEqual([d['title'] for d in detail['directions']], ['方向一', '方向二'])
        # Malformed parts of research.json are dropped instead of breaking the 调研 tab.
        r = detail['research']
        self.assertEqual((r['title'], r['summary'], r['what_happened']), ('夜跑', '7', ''))
        self.assertEqual(r['facts'], [{'claim': '事实一', 'sources': ['https://example.org/a']}])
        self.assertEqual(r['viewpoints'], ['观点', '3'])
        self.assertEqual(r['sources'], [{'url': 'https://example.org/a', 'title': '甲'}])
        self.assertEqual(detail['script'], '# 脚本\n第一句')
        self.assertEqual(sorted(f['path'] for f in detail['files']), written)
        self.assertNotIn('asset_snapshot', detail['tasks'][0])
        response = self.client.get(self.url + '/work/' + '调研.md')
        self.assertEqual(response.text, '# 调研\n事实')
        self.assertTrue(response.headers['content-type'].startswith('text/plain'))
        page = self.client.get(self.url + '/work/site/index.html')
        # Pages Claude wrote run in an isolated origin and cannot call the workbench API.
        self.assertEqual(page.headers['content-security-policy'], 'sandbox allow-scripts')
        self.assertEqual(self.client.put(self.url + '/work/' + '调研.md', json={'text': '# 改过'}).status_code, 200)
        self.assertEqual((self.work() / '调研.md').read_text(encoding='utf-8'), '# 改过')
        for path in ('site/index.html', '..%2Foutside.md', '素材/a.md', '.claude/settings.json'):
            with self.subTest(path=path):
                self.assertEqual(self.client.put(self.url + '/work/' + path, json={'text': 'x'}).status_code, 400)
        self.assertFalse((self.work().parent / 'outside.md').exists())

    def test_a_reply_without_files_is_a_normal_answer(self):
        with patch.dict(os.environ, {'CLI_TEST_REPLY': '你想给谁看？A. 学生 B. 上班族'}):
            task = self.run_prompt('先问我几个问题')
        self.assertEqual(task['status'], 'completed', task.get('error'))
        self.assertEqual(task['output']['reply'], '你想给谁看？A. 学生 B. 上班族')
        self.assertEqual(task['output']['files'], [])

    def test_failures_without_output_fail_and_retry_freezes_configuration(self):
        for mode, message in [('no_result', '没有完成'), ('failed', 'test turn limit')]:
            with self.subTest(mode=mode), patch.dict(os.environ, {'CLI_TEST_MODE': mode}):
                task = self.run_prompt()
                self.assertEqual(task['status'], 'failed')
                self.assertIn(message, task['error'])
        self.assertEqual(task['cli_result']['reported_cost_usd'], .02)
        self.client.put('/api/claude-code', json={'model': 'sonnet'})
        response = self.client.post('/api/tasks/' + task['id'] + '/retry', json={})
        result = self.wait_task(response.json()['id'])
        self.assertEqual(result['status'], 'completed', result.get('error'))
        self.assertEqual(result['cli_config']['model'], 'opus')
        self.assertEqual(result['payload']['prompt'], '制作测试视频')
        args = self.received()['argv']
        self.assertEqual(args[args.index('--resume') + 1], task['cli_session_id'])

    def test_one_run_at_a_time_and_cancellation_stops_a_silent_process(self):
        with patch.dict(os.environ, {'CLI_TEST_MODE': 'hang'}):
            ident = self.client.post(self.url + '/run', json={'prompt': '等待测试'}).json()['id']
            for _ in range(100):
                task = store.get('task', ident)
                if task.get('cli_session_id') and (self.work() / '.claude' / 'child.pid').exists():
                    break
                time.sleep(.05)
            self.assertTrue(task.get('cli_session_id'))
            self.assertEqual(self.client.post(self.url + '/run', json={'prompt': '再来'}).status_code, 400)
            self.client.post('/api/tasks/' + ident + '/cancel', json={})
            result = self.wait_task(ident)
        self.assertEqual(result['status'], 'cancelled')
        self.assertTrue(result['cli_session_id'])

    def test_timeout_stops_the_process_tree(self):
        settings = claude_cli.options() | {'timeout_sec': 1}
        with patch.object(claude_cli, 'options', return_value=settings), patch.dict(os.environ, {'CLI_TEST_MODE': 'timeout'}):
            task = self.run_prompt()
        self.assertEqual(task['status'], 'failed')
        self.assertIn('超时', task['error'])

    def test_creating_with_a_prompt_runs_it_and_missing_cli_keeps_the_idea(self):
        response = self.client.post('/api/projects', json={'prompt': '做一条关于城市夜跑的视频\n要轻松'})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body['project']['name'], '做一条关于城市夜跑的视频')
        task = self.wait_task(body['task']['id'])
        self.assertEqual(self.received(body['project']['id'])['prompt'], '做一条关于城市夜跑的视频\n要轻松')
        self.assertEqual(task['status'], 'completed', task.get('error'))
        with patch.object(claude_cli, 'cli_command', return_value=None):
            body = self.client.post('/api/projects', json={'prompt': '没有 CLI 的想法'}).json()
            self.assertTrue(body['needs_cli'])
            self.assertIsNone(body['task'])
            self.assertEqual(store.get('project', body['project']['id'])['idea'], '没有 CLI 的想法')
            self.assertEqual(self.client.post(self.url + '/run', json={'prompt': '制作'}).status_code, 400)
        self.assertEqual(self.client.post(self.url + '/run', json={'prompt': '   '}).status_code, 400)

    def test_read_only_cli_check_does_not_persist_account_details(self):
        response = self.client.post('/api/claude-code/check', json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['last_check']['logged_in'])
        self.assertNotIn('email', json.dumps(config.read()))
        self.assertNotIn('never-persist', response.text)


@unittest.skipUnless(FFMPEG and FFPROBE, '需要本机 FFmpeg / ffprobe')
class VideoRunTests(Workbench):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = tempfile.TemporaryDirectory(prefix='cli fixtures ')
        cls.video = Path(cls.fixtures.name) / 'sample.mp4'
        cls.alt_video = Path(cls.fixtures.name) / 'sample.mkv'
        event = threading.Event()
        media.run([FFMPEG, '-y', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x240:rate=24',
                   '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000', '-t', '2',
                   *media.video_encoding(), '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(cls.video)], event)
        # Not browser-playable as delivered: MPEG-4 Part 2 video with PCM audio in MKV.
        media.run([FFMPEG, '-y', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x240:rate=24',
                   '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000', '-t', '2',
                   '-c:v', 'mpeg4', '-c:a', 'pcm_s16le', str(cls.alt_video)], event)

    @classmethod
    def tearDownClass(cls):
        cls.fixtures.cleanup()

    def setUp(self):
        super().setUp()
        self.videos = patch.dict(os.environ, {'CLI_TEST_VIDEO': str(self.video), 'CLI_TEST_ALT_VIDEO': str(self.alt_video)})
        self.videos.start()

    def tearDown(self):
        self.videos.stop()
        super().tearDown()

    def upload(self, name, kind=None):
        # Trailing bytes give each upload its own content fingerprint; players ignore them.
        content = self.video.read_bytes() + name.encode() + str(time.time()).encode()
        response = self.client.post(self.url + '/assets' + (f'?type={kind}' if kind else ''),
                                    files={'file': (name, content, 'video/mp4')})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_newest_video_becomes_a_playable_version(self):
        task = self.run_prompt()
        self.assertEqual(task['status'], 'completed', task.get('error'))
        self.assertEqual(task['output']['files'][:2], ['final.mp4', 'drafts/draft.mp4'])
        version = store.get('version', task['output']['version_id'])
        self.assertEqual(version['result']['engine'], 'claude_cli')
        self.assertEqual(version['result']['summary'], 'fixture video complete')
        # The newest render is the deliverable, not the earlier draft.
        self.assertIn('claude/final.mp4', version['result']['notes'][0])
        self.assertEqual(version['preview']['video'], f'videos/{task["id"]}/video.mp4')
        self.assertTrue(version['preview']['checks']['decodable'])
        self.assertEqual(store.get('project', self.project['id'])['adopted']['edit'], version['id'])
        settings = store.project_file(self.project['id'], 'agent-control/' + task['id'] + '/settings.json')
        hook = json.loads(settings.read_text(encoding='utf-8'))['hooks']['PreToolUse'][0]['hooks'][0]
        # Exercise the generated hook using real argv, including paths with spaces.
        hook_command = [hook['command'], *hook['args']]
        for path, decision in [(self.work() / 'work' / 'edit.py', 'allow'), (self.work() / '素材' / 'original.mp4', 'deny')]:
            check = subprocess.run(hook_command, input=json.dumps({'cwd': str(self.work()),
                'tool_name': 'Write', 'tool_input': {'file_path': str(path)}}),
                text=True, capture_output=True, encoding='utf-8', timeout=10)
            self.assertEqual(check.returncode, 0, check.stderr)
            self.assertEqual(json.loads(check.stdout)['hookSpecificOutput']['permissionDecision'], decision)
        video_response = self.client.get(self.url + '/files/' + version['preview']['video'])
        self.assertEqual(video_response.status_code, 200)
        self.assertEqual(video_response.headers['content-type'], 'video/mp4')
        films = self.client.get('/api/library').json()['films']
        self.assertEqual([f['id'] for f in films], [version['id']])
        publication = self.client.post(self.url + '/publications', json={'platform': 'B站', 'url': 'https://example.org/v'})
        self.assertEqual(publication.status_code, 200, publication.text)

    def test_materials_are_copied_by_name_and_kept_in_sync(self):
        self.upload('main.mp4')
        self.upload('main.mp4')
        self.upload('配乐.mp4', 'Music')
        folder = self.work() / claude_cli.MATERIALS
        folder.mkdir(parents=True)
        (folder / 'removed.mp4').write_bytes(b'from an asset that no longer exists')
        task = self.run_prompt()
        self.assertEqual(task['status'], 'completed', task.get('error'))
        self.assertEqual(self.received()['materials'], ['main (2).mp4', 'main.mp4', '配乐.mp4'])
        self.assertNotIn('素材/main.mp4', task['output']['files'])

    def test_video_left_by_an_unfinished_run_is_kept_with_a_note(self):
        with patch.dict(os.environ, {'CLI_TEST_MODE': 'failed_with_video'}):
            task = self.run_prompt()
        self.assertEqual(task['status'], 'completed', task.get('error'))
        self.assertIn('没有正常结束', task['output']['warnings'][0])
        version = store.get('version', task['output']['version_id'])
        self.assertIn('没有正常结束', version['result']['notes'][0])

    def test_unreadable_video_is_reported_without_losing_the_reply(self):
        with patch.dict(os.environ, {'CLI_TEST_MODE': 'corrupt'}):
            task = self.run_prompt()
        self.assertEqual(task['status'], 'completed', task.get('error'))
        self.assertIsNone(task['output']['version_id'])
        self.assertIn('无法保存为版本', task['output']['warnings'][0])
        self.assertEqual(task['output']['reply'], 'fixture video complete')

    def test_unplayable_video_is_converted_for_the_browser(self):
        with patch.dict(os.environ, {'CLI_TEST_MODE': 'convert'}):
            task = self.run_prompt()
        self.assertEqual(task['status'], 'completed', task.get('error'))
        version = store.get('version', task['output']['version_id'])
        self.assertTrue(any('已转换' in note for note in version['result']['notes']))
        probe = media.probe(store.project_file(self.project['id'], version['preview']['video']), threading.Event())
        codecs = {s['codec_type']: s['codec_name'] for s in probe['streams']}
        self.assertEqual(codecs, {'video': 'h264', 'audio': 'aac'})
        self.assertTrue((self.work() / 'final.mkv').is_file())


class LegacyProjectTests(unittest.TestCase):
    def legacy_project(self, **extra):
        p = store.put('project', {'name': '旧作品', 'adopted': {}, 'selected_topic_id': 't2', **extra, 'workspace': {
            'idea': '旧想法', 'card': {'audience': '学生', 'tone': ''},
            'extra_angles': {'t1': [{'id': 'more-1', 'title': '追加方向'}]}}})
        store.put('version', {'stage': 'research', 'result': {'summary': '总结', 'topics': [
            {'id': 't1', 'title': '没选的选题', 'angles': [{'id': 'angle-1', 'title': '方向甲', 'hook': '开场'}]},
            {'id': 't2', 'title': '选中的选题', 'narratives': ['观点'],
             'key_facts': [{'claim': '事实', 'source_urls': ['https://example.org']}]}],
            'sources': [{'url': 'https://example.org', 'title': '来源'}]}}, p['id'])
        store.put('version', {'stage': 'script', 'result': {'angle': '方向甲', 'paragraphs': [
            {'id': 'a', 'speaker': 'A', 'text': '第一句', 'cue': '特写'}]}}, p['id'])
        return p

    def test_old_research_scripts_and_card_become_the_tab_files(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(store, 'DATA', Path(folder)):
            store.init()
            fresh, earlier = self.legacy_project(), self.legacy_project(v3=True)  # v3: migrated once already
            before = store.get('project', fresh['id'])['updated_at']
            projects.migrate()
            for p in (fresh, earlier):
                work = store.project_file(p['id'], claude_cli.WORKDIR)
                research = projects.research(p['id'])
                self.assertEqual(research['title'], '选中的选题')
                self.assertEqual(research['facts'], [{'claim': '事实', 'sources': ['https://example.org']}])
                self.assertEqual(research['viewpoints'], ['观点'])
                self.assertIn('第一句', projects.script(p['id']))
                self.assertIn('学生', (work / '需求.md').read_text(encoding='utf-8'))
                self.assertEqual([d['title'] for d in projects.directions(p['id'])], ['方向甲', '追加方向'])
                self.assertEqual(store.get('project', p['id'])['migration'], projects.MIGRATION)
            migrated = store.get('project', fresh['id'])
            self.assertEqual(migrated['idea'], '旧想法')
            self.assertEqual(migrated['updated_at'], before)
            # Files Claude or the creator changed afterwards are never overwritten.
            (work / '脚本.md').write_text('我改过', encoding='utf-8')
            p = store.get('project', earlier['id'])
            p['migration'] = 1
            store.put('project', p)
            projects.migrate()
            self.assertEqual(projects.script(earlier['id']), '我改过')


if __name__ == '__main__':
    unittest.main()
