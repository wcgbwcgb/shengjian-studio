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
from app import assets as asset_store, claude_cli, cli_guard, config, docs, media, store
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


@unittest.skipUnless(FFMPEG and FFPROBE, '需要本机 FFmpeg / ffprobe')
class CliVideoTests(unittest.TestCase):
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
        self.temp = tempfile.TemporaryDirectory(prefix='cli workbench ')
        self.data = patch.object(store, 'DATA', Path(self.temp.name))
        self.data.start()
        self.command = patch.object(claude_cli, 'cli_command', return_value=[sys.executable, str(FAKE)])
        self.command.start()
        self.env = patch.dict(os.environ, {'CLI_TEST_VIDEO': str(self.video), 'CLI_TEST_ALT_VIDEO': str(self.alt_video),
                              'CLI_TEST_MODE': 'success', 'ANTHROPIC_API_KEY': 'sk-ant-test-never-forward-this-key'})
        self.env.start()
        config.save({'ffmpeg_path': FFMPEG, 'ffprobe_path': FFPROBE})
        self.context = TestClient(app)
        self.client = self.context.__enter__()
        self.project = self.client.post('/api/projects', json={'name': 'CLI 视频'}).json()
        self.url = '/api/projects/' + self.project['id']

    def tearDown(self):
        self.context.__exit__(None, None, None)
        self.env.stop()
        self.command.stop()
        self.data.stop()
        self.temp.cleanup()

    def wait_task(self, ident, seconds=15):
        for _ in range(int(seconds / .05)):
            task = store.get('task', ident)
            if task['status'] not in ('queued', 'running', 'cancelling'):
                return task
            time.sleep(.05)
        self.fail('CLI task did not finish')

    def submit(self, **body):
        response = self.client.post(self.url + '/tasks', json={'kind': 'cli_video', 'prompt': '制作测试视频', **body})
        self.assertEqual(response.status_code, 200, response.text)
        return self.wait_task(response.json()['id'])

    def work(self, project_id=None):
        return store.project_file(project_id or self.project['id'], claude_cli.WORKDIR)

    def received(self, project_id=None):
        return json.loads((self.work(project_id) / 'received.json').read_text(encoding='utf-8'))

    def upload(self, name, kind=None):
        # Trailing bytes give each upload its own content fingerprint; players ignore them.
        content = self.video.read_bytes() + name.encode() + str(time.time()).encode()
        response = self.client.post(self.url + '/assets' + (f'?kind={kind}' if kind else ''),
                                    files={'file': (name, content, 'video/mp4')})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_video_text_is_recorded_outside_the_work_folder_and_can_be_resent_edited(self):
        task = self.submit(prompt='做一条 20 秒竖屏视频')
        self.assertEqual(task['status'], 'completed', task.get('error'))
        sent = self.client.get(f"/api/tasks/{task['id']}/sent").json()
        self.assertEqual(sent['prompt'], '做一条 20 秒竖屏视频')
        self.assertEqual(sent['args'], self.received()['argv'][self.received()['argv'].index('-p'):])
        self.assertNotIn('sent', [p.name for p in self.work().iterdir()])
        response = self.client.post(f"/api/tasks/{task['id']}/resend", json={'prompt': '做一条 15 秒横屏视频'})
        self.assertEqual(response.status_code, 200, response.text)
        again = self.wait_task(response.json()['id'])
        self.assertEqual(again['status'], 'completed', again.get('error'))
        self.assertEqual(self.received()['prompt'], '做一条 15 秒横屏视频')
        self.assertNotEqual(again['output']['version_id'], task['output']['version_id'])

    def test_only_the_prompt_reaches_claude_and_followup_resumes(self):
        prompt = '制作视频，保留引号 " 和 shell 文本 $(whoami) & literal'
        task = self.submit(prompt=prompt)
        self.assertEqual(task['status'], 'completed', task.get('error'))
        received = self.received()
        self.assertEqual(received['prompt'], prompt)
        self.assertNotIn(prompt, received['argv'])
        self.assertFalse(received['has_api_key'])
        for flag in ('--bare', '--dangerously-skip-permissions', '--restricted', '--append-system-prompt', '--system-prompt'):
            self.assertNotIn(flag, received['argv'])
        self.assertEqual(received['argv'][received['argv'].index('--tools') + 1], 'default')
        self.assertNotIn('PRIVATE_REASONING', json.dumps(task))
        self.assertEqual(sorted(p.name for p in self.work().iterdir()), ['drafts', 'final.mp4', 'received.json'])
        version = store.get('version', task['output']['version_id'])
        self.assertEqual(version['result']['engine'], 'claude_cli')
        self.assertEqual(version['result']['summary'], 'fixture video complete')
        # The newest render is the deliverable, not the earlier draft.
        self.assertIn('claude/final.mp4', version['result']['notes'][0])
        self.assertEqual(version['preview']['video'], f'videos/{task["id"]}/video.mp4')
        self.assertTrue(version['preview']['checks']['decodable'])
        self.assertEqual(store.get('project', self.project['id'])['adopted']['edit'], version['id'])
        self.assertEqual(store.listing('usage'), [])
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
        second = self.submit(prompt='把开头调整一下', base_version_id=version['id'])
        self.assertEqual(second['status'], 'completed', second.get('error'))
        received = self.received()
        self.assertEqual(received['prompt'], '把开头调整一下')
        args = received['argv']
        self.assertEqual(args[args.index('--resume')+1], version['cli_session_id'])
        self.assertIn('--fork-session', args)
        self.assertNotEqual(second['cli_session_id'], task['cli_session_id'])
        self.assertTrue(store.project_file(self.project['id'], version['preview']['video']).is_file())

    def test_materials_are_copied_by_name_and_kept_in_sync(self):
        self.upload('main.mp4')
        self.upload('main.mp4')
        self.upload('配乐.mp4', 'music')
        folder = self.work() / claude_cli.MATERIALS
        folder.mkdir(parents=True)
        (folder / 'removed.mp4').write_bytes(b'from an asset that no longer exists')
        task = self.submit()
        self.assertEqual(task['status'], 'completed', task.get('error'))
        self.assertEqual(self.received()['materials'], ['main (2).mp4', 'main.mp4', '配乐.mp4'])
        self.assertEqual(self.received()['prompt'], '制作测试视频')
        # Reordered, the copies are renamed to match what 素材.md says about them.
        first, second, music = docs.materials(store.get('project', self.project['id']))
        self.client.put(self.url + '/workspace/materials', json={'order': [music['id'], second['id'], first['id']], 'sequence': 'fixed'})
        self.assertEqual(self.submit()['status'], 'completed')
        listed = docs.read(self.project['id'], 'materials')
        self.assertIn('1. main.mp4（视频 · 0:02）\n2. main (2).mp4（视频 · 0:02）', listed)
        self.assertIn('## 配乐\n- 配乐.mp4（音乐 · 0:02）', listed)
        for asset, name in [(second, 'main.mp4'), (first, 'main (2).mp4'), (music, '配乐.mp4')]:
            self.assertEqual((folder / name).read_bytes(), asset_store.file(asset).read_bytes())

    def test_run_without_a_new_video_fails_with_claudes_reply(self):
        for mode, message in [('missing', '我需要更多信息'), ('no_result', '未完成'), ('failed', 'test turn limit'), ('corrupt', '本地处理失败')]:
            with self.subTest(mode=mode), patch.dict(os.environ, {'CLI_TEST_MODE': mode}):
                task = self.submit()
                self.assertEqual(task['status'], 'failed')
                self.assertIn(message, task['error'])
        self.assertEqual(store.listing('version', self.project['id']), [])

    def test_video_left_by_an_unfinished_run_is_kept_with_a_note(self):
        with patch.dict(os.environ, {'CLI_TEST_MODE': 'failed_with_video'}):
            task = self.submit()
        self.assertEqual(task['status'], 'completed', task.get('error'))
        version = store.get('version', task['output']['version_id'])
        self.assertIn('没有正常结束', version['result']['notes'][0])

    def test_unplayable_video_is_converted_for_the_browser(self):
        with patch.dict(os.environ, {'CLI_TEST_MODE': 'convert'}):
            task = self.submit()
        self.assertEqual(task['status'], 'completed', task.get('error'))
        version = store.get('version', task['output']['version_id'])
        self.assertTrue(any('已转换' in note for note in version['result']['notes']))
        probe = media.probe(store.project_file(self.project['id'], version['preview']['video']), threading.Event())
        codecs = {s['codec_type']: s['codec_name'] for s in probe['streams']}
        self.assertEqual(codecs, {'video': 'h264', 'audio': 'aac'})
        self.assertTrue((self.work() / 'final.mkv').is_file())

    def test_sessions_from_the_old_working_folder_are_not_resumed(self):
        legacy = store.put('version', {'stage': 'edit', 'result': {}, 'cli_session_id': '6f0b2a5e-5c4f-4d6e-9b1a-2b3c4d5e6f70'},
                           self.project['id'])
        task = self.submit(base_version_id=legacy['id'])
        self.assertEqual(task['status'], 'completed', task.get('error'))
        self.assertNotIn('--resume', self.received()['argv'])

    def test_cancellation_interrupts_silent_process_and_preserves_session(self):
        with patch.dict(os.environ, {'CLI_TEST_MODE': 'hang'}):
            response = self.client.post(self.url + '/tasks', json={'kind':'cli_video','prompt':'等待测试'})
            ident = response.json()['id']
            for _ in range(100):
                task = store.get('task', ident)
                if task.get('cli_session_id') and (self.work() / 'child.pid').exists():
                    break
                time.sleep(.05)
            self.assertTrue(task.get('cli_session_id'))
            self.client.post('/api/tasks/' + ident + '/cancel', json={})
            result = self.wait_task(ident)
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual(store.listing('version', self.project['id']), [])
        self.assertTrue(result['cli_session_id'])

    def test_failure_usage_is_preserved_and_retry_freezes_configuration(self):
        with patch.dict(os.environ, {'CLI_TEST_MODE': 'failed'}):
            task = self.submit()
        self.assertEqual(task['status'], 'failed')
        self.assertEqual(task['cli_result']['reported_cost_usd'], .02)
        self.client.put('/api/claude-code', json={'model':'sonnet'})
        response = self.client.post('/api/tasks/' + task['id'] + '/retry', json={})
        result = self.wait_task(response.json()['id'])
        self.assertEqual(result['status'], 'completed', result.get('error'))
        self.assertEqual(result['cli_config']['model'], 'opus')
        args = self.received()['argv']
        self.assertEqual(args[args.index('--resume')+1], task['cli_session_id'])

    def test_read_only_cli_check_does_not_persist_account_details(self):
        response = self.client.post('/api/claude-code/check', json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['last_check']['logged_in'])
        self.assertNotIn('email', json.dumps(config.read()))
        self.assertNotIn('never-persist', response.text)
        self.assertEqual(store.listing('usage'), [])

    def test_timeout_stops_process_tree_and_does_not_publish_partial_video(self):
        settings = claude_cli.options() | {'timeout_sec': 1}
        with patch.object(claude_cli, 'options', return_value=settings), patch.dict(os.environ, {'CLI_TEST_MODE':'timeout'}):
            task = self.submit()
        self.assertEqual(task['status'], 'failed')
        self.assertIn('超时', task['error'])
        self.assertEqual(store.listing('version', self.project['id']), [])

    def test_cross_project_resume_and_missing_cli_rejected_before_start(self):
        other = self.client.post('/api/projects', json={'name':'另一个项目'}).json()
        version = store.put('version', {'stage':'edit','result':{}}, other['id'])
        response = self.client.post(self.url + '/tasks', json={'kind':'cli_video','prompt':'制作','base_version_id':version['id']})
        self.assertEqual(response.status_code, 400)
        with patch.object(claude_cli, 'cli_command', return_value=None):
            response = self.client.post(self.url + '/tasks', json={'kind':'cli_video','prompt':'制作'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(store.listing('task', self.project['id']), [])

    def test_direct_creation_sends_the_idea_verbatim_and_can_export_existing_video(self):
        response = self.client.post('/api/creations', json={'idea':'直接制作一个视频', 'intent':'video'})
        self.assertEqual(response.status_code, 200)
        task = self.wait_task(response.json()['task']['id'])
        self.assertEqual(task['kind'], 'cli_video')
        self.assertEqual(task['status'], 'completed', task.get('error'))
        project_id, version_id = response.json()['project']['id'], task['output']['version_id']
        self.assertEqual(self.received(project_id)['prompt'], '直接制作一个视频')
        response = self.client.post('/api/projects/' + project_id + '/tasks', json={'kind':'final','version_id':version_id})
        final = self.wait_task(response.json()['id'])
        self.assertEqual(final['status'], 'completed', final.get('error'))
        self.assertEqual(len(store.listing('version', project_id)), 1)


if __name__ == '__main__':
    unittest.main()
