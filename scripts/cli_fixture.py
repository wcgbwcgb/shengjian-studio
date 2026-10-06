"""Isolated browser fixture: the real workbench with a fake Claude Code subprocess and an actual encoded video."""
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
config.save_tools({k: paths[k] for k in ('ffmpeg_path', 'ffprobe_path') if k in paths})
store.DATA.mkdir(parents=True, exist_ok=True)
video = store.DATA / 'fixture.mp4'
media.run([media.executable('ffmpeg'), '-y', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=270x480:rate=24',
           '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000', '-t', '2',
           *media.video_encoding(), '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(video)], threading.Event())
directions = {'directions': [{'title': '从一次夜跑讲城市的松弛感', 'reason': '具体、有画面', 'hook': '晚上十点的江边', 'audience': '上班族'},
                             {'title': '跑者的耳机里在放什么', 'reason': '音乐切入'}]}
research = {'title': '城市夜跑为什么流行', 'summary': '夜跑成了下班后的社交', 'what_happened': '多个城市夜跑团人数增长',
            'why_now': '天气转凉', 'content_gap': '缺少普通人的视角',
            'facts': [{'claim': '事实一', 'sources': ['https://example.org/read-page']}, {'claim': '事实二', 'sources': ['https://example.org/b']}],
            'viewpoints': ['跑步是解压'], 'audience_reactions': ['想加入'], 'to_verify': ['人数数据'],
            'sources': [{'url': 'https://example.org/read-page', 'title': '甲'}, {'url': 'https://example.org/b', 'title': '乙'}]}
script = '# 脚本\n\n## 01 开场\n\n晚上十点的江边。\n\n- 画面：跑者剪影\n- 来源 [甲](https://example.org/a)\n'
os.environ.update(CLI_TEST_VIDEO=str(video), CLI_TEST_DELAY='.6', CLI_TEST_REPLY='做好了：调研写在 research.json，方向和脚本也写好了，视频是 final.mp4。',
                  CLI_TEST_FILES=json.dumps({'需求.md': '# 需求', 'research.json': json.dumps(research, ensure_ascii=False),
                                             'directions.json': json.dumps(directions, ensure_ascii=False), '脚本.md': script},
                                            ensure_ascii=False))
claude_cli.cli_command = lambda settings=None: [sys.executable, str(ROOT / 'tests' / 'fake_claude.py')]
uvicorn.run(app, host='127.0.0.1', port=int(os.environ.get('CLI_TEST_PORT', '8878')), log_level='warning')
