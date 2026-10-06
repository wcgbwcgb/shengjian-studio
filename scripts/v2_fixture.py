"""Isolated browser fixture: real FastAPI/store/worker/renderer, stubbed AI responses only.

Not imported by the product. Its data directory must be under artifacts.
"""
import copy
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import claude_cli, config, models, store
from app.main import app
import uvicorn

if not store.DATA.is_relative_to(ROOT / 'artifacts'):
    raise RuntimeError('Browser fixture must use isolated artifacts data')

# Reuse local executable configuration, never copy credentials into fixture data.
import json
machine = ROOT / 'data' / 'machine-config.json'
paths = json.loads(machine.read_text(encoding='utf-8')) if machine.exists() else {}
config.save({k: paths[k] for k in ('ffmpeg_path', 'ffprobe_path') if k in paths})
config.has_api_key = lambda: True
# This fixture exercises API workflows only; never invoke a real logged-in CLI.
claude_cli.cli_command = lambda *args, **kwargs: None

TOPIC = {'id': 'rhythm', 'title': '为什么有些歌，一听就让人松弛？',
         'question': '同样的拍子，为什么会有不同的感觉？',
         'what_happened': '从听众熟悉的节奏出发，用两段小演示，解释不同演奏方式带来的听感变化。',
         'why_now': '这是一个适合低成本演示的常青问题，能让不懂乐理的观众马上参与。',
         'content_gap': '少一点术语，多一点听得见的对比。用同一句旋律，让观众先感受，再解释。',
         'narratives': ['常见讲法是先解释术语，再举例；本次试着把顺序反过来。'],
         'audience_reactions': [], 'source_urls': ['https://example.org/rhythm-study'],
         'key_facts': [{'claim': '这份测试资料用于验证研究与脚本之间的引用关系，不代表真实研究结论。',
                        'source_urls': ['https://example.org/rhythm-study']}],
         'angles': [
             {'id': 'a1', 'title': '先别讲道理，听听差别', 'reason': '用声音制造好奇，让观众先给出自己的答案。',
              'audience': '不懂乐理的音乐爱好者', 'hook': '同一段旋律，为什么第二遍突然变松弛了？', 'difference': '先试听，再用一句话解释'},
             {'id': 'a2', 'title': '松弛感，是故意不整齐吗？', 'reason': '从一个常见误解切入，用对比回答。',
              'audience': '刚开始学音乐的人', 'hook': '节奏不准，和有松弛感，是一回事吗？', 'difference': '拆解一个误解，给出实际示范'},
             {'id': 'a3', 'title': '给同一首歌，换一种性格', 'reason': '像换衣服一样改编旋律，让观众参与选择。',
              'audience': '喜欢翻唱与改编的观众', 'hook': '如果给这段旋律换一种走路方式呢？', 'difference': '把解释变成创作过程'}]}
SCRIPT = {'angle': '先别讲道理，听听差别', 'paragraphs': [
    {'id': 'hook', 'beat': 'hook', 'speaker': 'A', 'text': '同一段旋律，为什么第二遍突然变松弛了？',
     'cue': '人物近景，抛出问题后切到键盘特写。', 'source_urls': [], 'claim_type': 'opinion', 'visual_keywords': ['键盘']},
    {'id': 'evidence', 'beat': 'evidence', 'speaker': 'B', 'text': '先别急着找术语，我们听一遍，再听另一种演奏。',
     'cue': '两段演奏画面对比，保留完整乐句。', 'source_urls': ['https://example.org/rhythm-study'],
     'claim_type': 'fact', 'visual_keywords': ['演奏']},
    {'id': 'cta', 'beat': 'cta', 'speaker': 'A', 'text': '哪一种更像你今天的心情？', 'cue': '回到人物，留一点空间给观众思考。',
     'source_urls': [], 'claim_type': 'opinion', 'visual_keywords': ['人物']}
], 'publishing': {'titles': ['给旋律换一种心情'], 'description': 'UI TEST FIXTURE · 不是真实调研', 'cover': '先听听差别'}}


def call(task, mode, context, cancel, report):
    report('测试数据：正在验证真实工作流')
    time.sleep(.25)
    if mode == 'research':
        return {'summary': 'UI TEST FIXTURE · 用可听见的对比讲一个小故事。', 'topics': [copy.deepcopy(TOPIC)],
                'sources': [{'url': 'https://example.org/rhythm-study', 'title': '研究资料 · 测试引用', 'platform': 'Web',
                             'evidence_note': '用于端到端验证的固定证据。没有真实热度数据。'}],
                'limitations': ['隔离测试数据，不代表真实联网研究。']}, [], {'model': 'fixture'}
    if mode == 'script':
        result = copy.deepcopy(context.get('current') or SCRIPT)
        for p in result['paragraphs']:
            if p['id'] in context.get('target_ids', []):
                p['text'] = '同一段旋律，听听这次有什么不同？'
        return result, [], {'model': 'fixture'}
    if mode == 'chat':
        answered = sum(1 for m in context['conversation'] if m['role'] == 'user')
        if context['stage'] == 'clarify' and answered < 1:
            return {'reply': 'UI TEST FIXTURE · 好的，先确认一件事。', 'question': '这条视频主要给谁看？',
                    'options': ['不懂乐理的普通听众', '正在学音乐的学生'], 'card': {'goal': '让观众听出松弛感从哪里来'},
                    'action': 'none'}, [], {'model': 'fixture'}
        if context['stage'] == 'clarify':
            return {'reply': '需求已经清楚了，可以开始研究。', 'question': '', 'options': [],
                    'card': {'audience': context['message']}, 'action': 'research', 'action_input': '用可听见的对比',
                    'action_label': '开始研究'}, [], {'model': 'fixture'}
        return {'reply': '那我们换个讲法。', 'question': '', 'options': [], 'card': {'tone': '轻松'},
                'action': 'angles', 'action_input': context['message'], 'action_label': '按这个想法重新构思方向'}, [], {'model': 'fixture'}
    if mode == 'angles':
        return {'angles': [{'title': '从一次排练讲起', 'reason': '用一个人的故事带出问题', 'hook': '那天排练，指挥只说了一句话。'},
                           {'title': '让观众自己当指挥', 'reason': '互动选择', 'hook': '你来决定下一遍怎么弹。'}]}, [], {'model': 'fixture'}
    if mode == 'inspire':
        INSPIRE['batch'] += 1
        batch = INSPIRE['batch']
        return {'ideas': [{'title': f'UI 灵感 第{batch}批 · {name}', 'description': '测试用的灵感描述，讲一个具体的小实验。',
                           'hook': '先听这一段。', 'format': '对比实验', 'tags': ['测试', tag]}
                          for name, tag in [('节奏', '音乐'), ('表达', '口播'), ('城市', '街访')]]}, [], {'model': 'fixture'}
    raise ValueError('该模型操作不属于本次浏览器测试')


INSPIRE = {'batch': 0}


models.call = call
uvicorn.run(app, host='127.0.0.1', port=int(os.environ.get('V2_TEST_PORT', '8877')), log_level='warning')
