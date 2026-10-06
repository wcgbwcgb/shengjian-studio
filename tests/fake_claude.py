"""Subprocess fixture only: never used by the application outside tests."""
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

if '--version' in sys.argv:
    print('2.1.300 (Claude Code fixture)')
    sys.exit(0)
if sys.argv[1:3] == ['auth', 'status']:
    print(json.dumps({'loggedIn': True, 'authMethod': 'claude.ai', 'email': 'never-persist@example.org'}))
    sys.exit(0)
prompt = sys.stdin.read()
work = Path.cwd()
(work / 'received.json').write_text(json.dumps({'argv': sys.argv, 'prompt': prompt,
    'materials': sorted(p.name for p in (work / '素材').iterdir()) if (work / '素材').is_dir() else [],
    'has_api_key': bool(os.environ.get('ANTHROPIC_API_KEY')), 'has_proxy': bool(os.environ.get('ANTHROPIC_BASE_URL'))},
    ensure_ascii=False), encoding='utf-8')
mode = os.environ.get('CLI_TEST_MODE', 'success')
session = str(uuid.uuid4())
print(json.dumps({'type': 'system', 'subtype': 'init', 'session_id': session}), flush=True)
if mode in ('hang', 'timeout'):
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
    (work / 'child.pid').write_text(str(child.pid))
    sys.stdout.write('unfinished line')
    sys.stdout.flush()
    time.sleep(120)
print(json.dumps({'type': 'assistant', 'message': {'content': [
    {'type': 'thinking', 'thinking': 'PRIVATE_REASONING_MUST_NOT_APPEAR'},
    {'type': 'tool_use', 'name': 'Bash', 'input': {'command': 'ffmpeg fixture-render'}}]}}), flush=True)
time.sleep(float(os.environ.get('CLI_TEST_DELAY', '0')))
if mode in ('success', 'failed_with_video'):
    # A draft first, then the final render: the newest file is the deliverable.
    (work / 'drafts').mkdir(exist_ok=True)
    shutil.copyfile(os.environ['CLI_TEST_VIDEO'], work / 'drafts' / 'draft.mp4')
    time.sleep(.05)
    shutil.copyfile(os.environ['CLI_TEST_VIDEO'], work / 'final.mp4')
elif mode == 'convert':
    shutil.copyfile(os.environ['CLI_TEST_ALT_VIDEO'], work / 'final.mkv')
elif mode == 'corrupt':
    (work / 'final.mp4').write_bytes(b'not a video')
if mode in ('failed', 'failed_with_video'):
    print(json.dumps({'type': 'result', 'subtype': 'error_max_turns', 'is_error': True,
                      'errors': ['test turn limit reached'], 'session_id': session, 'total_cost_usd': .02}), flush=True)
    sys.exit(1)
if mode != 'no_result':
    print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False, 'session_id': session,
                      'total_cost_usd': .05, 'num_turns': 4, 'duration_ms': 100,
                      'usage': {'input_tokens': 100, 'output_tokens': 30}, 'modelUsage': {},
                      'result': 'fixture video complete' if mode != 'missing' else '我需要更多信息才能开始。'}), flush=True)
