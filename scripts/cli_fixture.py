"""Isolated browser fixture with a fake CLI subprocess and actual encoded video."""
import json
import os
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import claude_cli, config, media, store
from app.main import app
import uvicorn

if not store.DATA.is_relative_to(ROOT / 'artifacts'):
    raise RuntimeError('CLI browser fixture requires isolated artifacts data')
machine = ROOT / 'data' / 'machine-config.json'
paths = json.loads(machine.read_text(encoding='utf-8')) if machine.exists() else {}
config.save({k: paths[k] for k in ('ffmpeg_path', 'ffprobe_path') if k in paths})
store.DATA.mkdir(parents=True, exist_ok=True)
video, frame = store.DATA / 'fixture.mp4', store.DATA / 'fixture.jpg'
event = threading.Event()
media.run([media.executable('ffmpeg'), '-y', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=270x480:rate=24',
           '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000', '-t', '2',
           *media.video_encoding(), '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(video)], event)
media.run([media.executable('ffmpeg'), '-y', '-v', 'error', '-i', str(video), '-frames:v', '1', str(frame)], event)
os.environ.update(CLI_TEST_VIDEO=str(video), CLI_TEST_FRAME=str(frame), CLI_TEST_DELAY='.4')
claude_cli.cli_command = lambda settings=None: [sys.executable, str(ROOT / 'tests' / 'fake_claude.py')]
config.has_api_key = lambda: False
uvicorn.run(app, host='127.0.0.1', port=int(os.environ.get('CLI_TEST_PORT', '8878')), log_level='warning')
