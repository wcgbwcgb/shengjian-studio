"""V2 orchestration over the existing project, job, version and media stores."""
import copy
import hashlib
import re
import shutil
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter
from pydantic import BaseModel, Field

from . import assets as asset_store, claude_cli, config, jobs, media, store, text_cli, timeline

router = APIRouter(prefix='/api')
ACTIVE = ('queued', 'running', 'cancelling')


def idle(project_id):
    if any(t['status'] in ACTIVE for t in store.listing('task', project_id)):
        raise ValueError('正在完成当前创作，请稍候，或先取消任务。')


def owned(project_id, version_id, stage):
    v = store.get('version', version_id)
    if v['project_id'] != project_id or v['stage'] != stage:
        raise ValueError('内容不属于当前视频或阶段')
    return v


def workspace(project):
    return project.setdefault('workspace', {'idea': project['name'], 'intent': 'idea'})


def invalidate(project, *stages):
    project['stale_stages'] = sorted(set(project.get('stale_stages', [])) | set(stages))


def clear_stale(project, *stages):
    project['stale_stages'] = [s for s in project.get('stale_stages', []) if s not in stages]


class Creation(BaseModel):
    idea: str = Field(default='', max_length=12000)
    name: str = Field(default='', max_length=120)
    intent: str = 'idea'
    duration: int = Field(default=60, ge=10, le=3600)
    aspect: str = '9:16'
    clarify: bool = False
    start: bool = True  # False: only create the project; the creator starts work inside it.


CARD_FIELDS = {'audience': '给谁看', 'goal': '想让观众得到什么', 'core_message': '核心观点', 'tone': '语气风格',
               'format': '形式与时长', 'must_include': '必须包含', 'avoid': '要避免', 'notes': '其他'}
CHAT_ACTIONS = {'clarify': ('none', 'research'), 'angles': ('none', 'research', 'angles', 'custom_angle'),
                'script': ('none', 'research', 'angles', 'custom_angle', 'revise_script')}


def card_text(project):
    card = workspace(project).get('card') or {}
    lines = [f'{label}：{card[key]}' for key, label in CARD_FIELDS.items() if card.get(key)]
    return '\n已与创作者确认的需求（优先遵循）：\n' + '\n'.join(lines) if lines else ''


def research_prompt(project):
    w = workspace(project)
    return (f"创作者想制作：{w['idea']}\n入口：{w.get('intent', 'idea')}。"
            '围绕这个意图研究适合短视频的内容。具体题目聚焦一个选题；发现选题时给三个候选。'
            '每个选题给三个具体可拍的角度，包括受众、开场、差异化。'
            '提炼事实、不同观点、观众反应和内容空缺。只列实际可取得的来源，未取得的数据保持空缺。' + card_text(project))


def submit_research(project):
    return jobs.submit(project['id'], 'research', {'prompt': research_prompt(project), 'workspace': True})


@router.post('/creations')
def create(body: Creation):
    name = body.name.strip()
    idea = body.idea.strip() or name
    if not idea:
        raise ValueError('请填写项目名称或写下一个想法')
    if body.aspect not in ('9:16', '16:9', '1:1'):
        raise ValueError('请选择有效画幅')
    if body.intent not in ('idea', 'research', 'reference', 'discover', 'assets', 'video'):
        raise ValueError('创作入口不支持')
    # A duration or orientation written in the idea wins over the dropdown defaults,
    # matching what the task itself will use.
    stated = store.effective({'defaults': {'duration': body.duration, 'aspect': body.aspect}}, 'idea', idea)
    duration = stated['duration'] if 10 <= int(stated['duration']) <= 3600 else body.duration
    project = store.put('project', {
        'name': (name or idea)[:80], **({'name_custom': True} if name else {}),
        'requirements': {'duration': duration, 'aspect': stated['aspect']},
        'defaults': store.settings(), 'status': '构思中', 'adopted': {}, 'stale_stages': [],
        'stage_settings': {}, 'stage_prompts': {},
        'workspace': {'idea': idea, 'intent': body.intent},
    })
    if body.intent == 'assets':
        project['status'] = '待添加素材'
        project = store.put('project', project)
    # A pasted reference is preserved even when the provider is not configured yet.
    for url in re.findall(r'https?://[^\s<>"\u3002\uff0c]+', idea):
        parsed = urlparse(url)
        if parsed.hostname and not parsed.username and not parsed.password:
            store.put('source', {'url': url, 'title': '创作参考', 'platform': parsed.hostname,
                                'verification': 'user_supplied', 'metrics': {}, 'published_at': None,
                                'evidence_note': '创作者提供的链接，尚未读取', 'collected_at': store.now()}, project['id'])
    if not body.start:
        return {'project': project, 'task': None}
    if body.intent == 'video':
        task = jobs.submit(project['id'], 'cli_video', {'prompt': idea}) if claude_cli.cli_command() else None
        return {'project': project, 'task': task, 'needs_cli': not bool(task)}
    if body.clarify and body.intent in ('idea', 'research', 'reference', 'discover') and text_cli.available():
        # Talk the idea through first; research starts once the requirements are clear.
        project['status'] = '明确需求中'
        project = store.put('project', project)
        task = jobs.submit(project['id'], 'chat', {'prompt': idea, 'workspace': True})
        return {'project': project, 'task': task, 'needs_connection': False, 'clarify': True}
    task = submit_research(project) if text_cli.available() and body.intent != 'assets' else None
    return {'project': project, 'task': task, 'needs_connection': not text_cli.available()}


@router.get('/opportunities')
def opportunities():
    """Reuse actual research. Never label seed prompts as live trends."""
    projects = {p['id']: p for p in store.listing('project')}
    seen, items = set(), []
    for version in store.listing('version'):
        if version['stage'] != 'research' or version['project_id'] not in projects:
            continue
        for topic in version['result'].get('topics', []):
            key = topic.get('title', '')
            if not key or key in seen:
                continue
            seen.add(key)
            items.append({'topic': topic, 'project_id': version['project_id'], 'version_id': version['id'],
                          'collected_at': version['created_at'], 'sources': version['result'].get('sources', [])})
    return items


class IdeaUpdate(BaseModel):
    idea: str = Field(min_length=1, max_length=12000)
    model: str | None = None
    prompt: str = ''
    settings: dict = Field(default_factory=dict)


@router.post('/projects/{project_id}/workspace/research')
def research(project_id: str, body: IdeaUpdate):
    with jobs.LOCK:
        idle(project_id)
        p = store.get('project', project_id)
        if not body.idea.strip():
            raise ValueError('请写下想研究的内容')
        workspace(p)['idea'] = body.idea.strip()
        return start_research(p, body.prompt, body.model, body.settings)


def start_research(p, extra='', model=None, settings=None):
    if p.get('adopted'):
        invalidate(p, 'research', 'script', 'scenes', 'edit')
    store.put('project', p)
    if not text_cli.available():
        raise ValueError('想法已保存。请在设置中连接 Claude 订阅或 API 服务后开始调研。')
    return jobs.submit(p['id'], 'research', {'prompt': research_prompt(p) + ('\n' + extra if extra else ''),
                       'workspace': True, 'model': model, 'settings': settings or {}})


class AngleChoice(BaseModel):
    version_id: str
    topic_id: str
    angle_id: str
    model: str | None = None
    note: str = Field(default='', max_length=2000)


class ResearchStart(BaseModel):
    version_id: str
    topic_id: str


@router.post('/creations/from-research')
def create_from_research(body: ResearchStart):
    original = store.get('version', body.version_id)
    if original['stage'] != 'research':
        raise ValueError('请选择研究中的选题')
    topic = next((t for t in original['result'].get('topics', []) if t['id'] == body.topic_id), None)
    if not topic:
        raise ValueError('选题已不存在，请重新选择')
    p = store.put('project', {'name': topic['title'][:80], 'requirements': {}, 'defaults': store.settings(),
                 'status': '待选择角度', 'adopted': {}, 'stale_stages': [], 'stage_settings': {}, 'stage_prompts': {},
                 'workspace': {'idea': topic['title'], 'intent': 'idea'}})
    sources = []
    for source in original['result'].get('sources', []):
        sources.append(store.put('source', {k: v for k, v in source.items() if k not in
                      ('id', 'project_id', 'created_at', 'updated_at')}, p['id']))
    result = copy.deepcopy(original['result'])
    result.update(topics=[topic], sources=sources)
    v = store.put('version', {'stage': 'research', 'result': result, 'upstream': {},
                  'copied_from': original['id'], 'prompt': '从已有研究开始新视频',
                  'source_ids': [s['id'] for s in sources], 'model_config': {'model': 'local'}, 'stale': False}, p['id'])
    workspace(p)['research_version'] = v['id']
    store.put('project', p)
    return {'project': p, 'version': v}


def angles_for(topic, project=None):
    angles = []
    for i, original in enumerate(topic.get('angles', [])[:3]):
        a = dict(original) if isinstance(original, dict) else {'title': str(original)}
        a.setdefault('id', f'angle-{i + 1}')
        angles.append(a)
    # Directions added later (more angles, the creator's own) live with the project.
    if project:
        angles += workspace(project).get('extra_angles', {}).get(topic.get('id'), [])
    return angles


@router.post('/projects/{project_id}/workspace/angle')
def choose_angle(project_id: str, body: AngleChoice):
    with jobs.LOCK:
        idle(project_id)
        research = owned(project_id, body.version_id, 'research')
        topic = next((t for t in research['result'].get('topics', []) if t['id'] == body.topic_id), None)
        if not topic:
            raise ValueError('请选择当前调研中的选题')
        p = store.get('project', project_id)
        angle = next((a for a in angles_for(topic, p) if a['id'] == body.angle_id), None)
        if not angle:
            raise ValueError('请选择当前选题中的角度')
        return start_script(p, angle, research, topic, body.note, body.model)


def start_script(p, angle, research=None, topic=None, note='', model=None):
    if research:
        p['adopted']['research'] = research['id']
    if topic:
        p.update(selected_topic=topic, selected_topic_id=topic['id'])
        if not p.get('name_custom') and topic.get('title'):
            p['name'] = str(topic['title'])[:80]
    p['status'] = '正在构思脚本'
    w = workspace(p)
    w['angle'] = angle
    if research:
        w['research_version'] = research['id']
    w.pop('script_version', None)
    invalidate(p, 'script', 'scenes', 'edit')
    clear_stale(p, 'research')
    store.put('project', p)
    return jobs.submit(p['id'], 'script', {
        'model': model,
        'workspace': True, 'fresh_script': True, 'angle': angle['title'],
        'prompt': f"按用户选定的角度写完整短视频：{angle}。" + (f"用户对这个方向的补充要求：{note}。" if note.strip() else '')
                  + '直接提供开场、推进、关键转折和收尾。'
                  '重要事实在所属段落 source_urls 中引用调研原始来源；听感与观点分开。'
                  '为每段写具体画面 cue 和可搜索的 visual_keywords。不要要求用户再写提示词。',
    })


def current_research(p):
    versions = [v for v in store.listing('version', p['id']) if v['stage'] == 'research']
    return versions[0] if versions else None


def current_topic(p, research):
    topics = research['result'].get('topics', []) if research else []
    return next((t for t in topics if t.get('id') == p.get('selected_topic_id')), topics[0] if topics else None)


def current_script(p):
    ident = workspace(p).get('script_version') or p.get('adopted', {}).get('script')
    if not ident:
        return None
    v = store.get('version', ident)
    return v if v.get('result', {}).get('paragraphs') else None


def chat_history(project_id, limit=30, exclude_task=None):
    records = [m for m in reversed(store.listing('message', project_id))
               if m.get('stage') == 'chat' and (not exclude_task or m.get('task_id') != exclude_task)]
    return [{'role': m['role'], 'text': m['text'], **({'question': m['question']} if m.get('question') else {})}
            for m in records][-limit:]


def topic_digest(topic, p):
    facts = [f.get('claim') if isinstance(f, dict) else str(f) for f in topic.get('key_facts', [])][:10]
    return {**{k: topic.get(k) for k in ('id', 'title', 'question', 'what_happened', 'why_now', 'content_gap', 'narratives')},
            'key_facts': facts, 'source_urls': topic.get('source_urls', []),
            'angles': [{'title': a.get('title'), 'reason': a.get('reason')} for a in angles_for(topic, p)]}


class ChatInput(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


@router.post('/projects/{project_id}/workspace/chat')
def chat(project_id: str, body: ChatInput):
    with jobs.LOCK:
        idle(project_id)
        if not text_cli.available():
            raise ValueError('请先在设置中连接 Claude 订阅或 API 服务')
        return jobs.submit(project_id, 'chat', {'prompt': body.message.strip(), 'workspace': True})


def execute_chat(task, event, report):
    from . import models
    project_id = task['project_id']
    p = store.get('project', project_id)
    research, script = current_research(p), current_script(p)
    stage = 'script' if script else 'angles' if research else 'clarify'
    topic = current_topic(p, research)
    w = workspace(p)
    effective = task['effective']
    context = {'stage': stage, 'idea': w.get('idea', p['name']), 'card': w.get('card', {}),
               'defaults': {k: effective.get(k) for k in ('audience', 'style', 'platform', 'duration', 'aspect')},
               'research': topic_digest(topic, p) if topic else None, 'chosen_angle': w.get('angle'),
               'script': [{'speaker': x.get('speaker'), 'text': x.get('text')} for x in script['result']['paragraphs']] if script else None,
               'conversation': chat_history(project_id, exclude_task=task['id']), 'message': task['payload']['prompt'],
               'effective': effective}
    result, _, _ = models.call(task, 'chat', context, event, report)
    reply = str(result.get('reply') or '').strip()
    if not reply:
        raise ValueError('Claude 没有给出有效回复，请重试')
    action = result.get('action') if result.get('action') in CHAT_ACTIONS[stage] else 'none'
    options = [str(o).strip()[:200] for o in result.get('options') or [] if str(o).strip()][:4]
    with jobs.LOCK:
        current = store.get('project', project_id)
        card = workspace(current).setdefault('card', {})
        for key, value in (result.get('card') or {}).items():
            if key in CARD_FIELDS and isinstance(value, str) and value.strip():
                card[key] = value.strip()[:1000]
        if current.get('status') == '构思中':
            current['status'] = '明确需求中'
        store.put('project', current)
        message = store.put('message', {
            'stage': 'chat', 'role': 'assistant', 'text': reply[:4000], 'question': str(result.get('question') or '').strip()[:1000],
            'options': options, 'action': action, 'action_input': str(result.get('action_input') or '').strip()[:2000],
            'action_label': str(result.get('action_label') or '').strip()[:40], 'task_id': task['id']}, project_id)
    return {'message_id': message['id']}


class CardUpdate(BaseModel):
    card: dict[str, str]


@router.patch('/projects/{project_id}/workspace/card')
def update_card(project_id: str, body: CardUpdate):
    with jobs.LOCK:
        p = store.get('project', project_id)
        card = workspace(p).setdefault('card', {})
        for key, value in body.card.items():
            if key not in CARD_FIELDS:
                raise ValueError('需求卡字段不支持')
            if len(value) > 1000:
                raise ValueError('需求卡内容过长')
            if value.strip():
                card[key] = value.strip()
            else:
                card.pop(key, None)
        store.put('project', p)
        return card


class ChatAction(BaseModel):
    message_id: str


@router.post('/projects/{project_id}/workspace/chat/action')
def chat_action(project_id: str, body: ChatAction):
    with jobs.LOCK:
        idle(project_id)
        message = store.get('message', body.message_id)
        if message.get('project_id') != project_id or message.get('stage') != 'chat' or message.get('action') in (None, 'none'):
            raise ValueError('这条消息没有可执行的建议')
        if message.get('action_task_id'):
            raise ValueError('这个建议已经执行过了')
        p, text = store.get('project', project_id), message.get('action_input', '')
        research = current_research(p)
        if message['action'] == 'research':
            task = start_research(p, '研究重点：' + text if text else '')
        elif message['action'] == 'angles':
            if not research or not current_topic(p, research):
                raise ValueError('还没有研究结果，请先开始研究')
            task = submit_angles(p, research, current_topic(p, research), text)
        elif message['action'] == 'custom_angle':
            title, _, detail = text.partition('\n')
            topic = current_topic(p, research)
            task = start_script(p, remember_angle(p, topic, title or text, detail), research, topic)
        else:
            script = current_script(p)
            if not script:
                raise ValueError('还没有脚本，请先选择方向')
            task = jobs.submit(project_id, 'script', {'prompt': text, 'base_version_id': script['id'], 'workspace': True})
        message['action_task_id'] = task['id']
        store.put('message', message, project_id)
        return task


def remember_angle(p, topic, title, detail=''):
    angle = {'id': 'custom-' + store.uid()[:10], 'title': title.strip()[:200], 'reason': detail.strip()[:2000], 'custom': True}
    if topic:
        workspace(p).setdefault('extra_angles', {}).setdefault(topic['id'], []).append(angle)
    return angle


def submit_angles(p, research, topic, feedback='', model=None):
    return jobs.submit(p['id'], 'angles', {'workspace': True, 'research_version_id': research['id'],
                                           'topic_id': topic['id'], 'feedback': feedback[:2000], 'model': model})


class MoreAngles(BaseModel):
    version_id: str
    topic_id: str
    feedback: str = Field(default='', max_length=2000)
    model: str | None = None


@router.post('/projects/{project_id}/workspace/angles')
def more_angles(project_id: str, body: MoreAngles):
    with jobs.LOCK:
        idle(project_id)
        research = owned(project_id, body.version_id, 'research')
        topic = next((t for t in research['result'].get('topics', []) if t['id'] == body.topic_id), None)
        if not topic:
            raise ValueError('请选择当前调研中的选题')
        p = store.get('project', project_id)
        task = submit_angles(p, research, topic, body.feedback.strip(), body.model)
        if body.feedback.strip():
            # Keep the conversation complete, whichever control the feedback came from.
            store.put('message', {'stage': 'chat', 'role': 'user', 'text': '换几个方向：' + body.feedback.strip(),
                                  'task_id': task['id']}, project_id)
        return task


def execute_angles(task, event, report):
    from . import models
    payload, project_id = task['payload'], task['project_id']
    research = owned(project_id, payload['research_version_id'], 'research')
    topic = next((t for t in research['result'].get('topics', []) if t['id'] == payload['topic_id']), None)
    if not topic:
        raise ValueError('选题已不存在，请重新研究')
    p = store.get('project', project_id)
    context = {'topic': topic_digest(topic, p), 'existing_angles': [{'title': a.get('title'), 'hook': a.get('hook')} for a in angles_for(topic, p)],
               'feedback': payload.get('feedback', ''), 'card': workspace(p).get('card', {}), 'idea': workspace(p).get('idea'),
               'conversation': chat_history(project_id, 12), 'count': 3, 'effective': task['effective']}
    result, _, _ = models.call(task, 'angles', context, event, report)
    angles = []
    for item in result.get('angles') or []:
        if isinstance(item, dict) and str(item.get('title') or '').strip():
            angles.append({'id': 'more-' + store.uid()[:10], 'feedback': payload.get('feedback', ''),
                           **{k: str(item.get(k) or '').strip()[:1000] for k in ('title', 'reason', 'audience', 'hook', 'difference')}})
    if not angles:
        raise ValueError('这次没有得到新的方向，请换个说法再试')
    angles = angles[:5]
    with jobs.LOCK:
        current = store.get('project', project_id)
        workspace(current).setdefault('extra_angles', {}).setdefault(topic['id'], []).extend(angles)
        store.put('project', current)
        store.put('message', {'stage': 'chat', 'role': 'assistant', 'task_id': task['id'],
                              'text': f'新增了 {len(angles)} 个方向：' + '；'.join(a['title'] for a in angles)}, project_id)
    return {'angle_ids': [a['id'] for a in angles]}


class CustomAngle(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    detail: str = Field(default='', max_length=2000)
    version_id: str | None = None
    topic_id: str | None = None
    model: str | None = None


@router.post('/projects/{project_id}/workspace/angle/custom')
def custom_angle(project_id: str, body: CustomAngle):
    with jobs.LOCK:
        idle(project_id)
        p = store.get('project', project_id)
        research = owned(project_id, body.version_id, 'research') if body.version_id else current_research(p)
        topic = (next((t for t in research['result'].get('topics', []) if t['id'] == body.topic_id), None)
                 if research and body.topic_id else current_topic(p, research))
        if not body.title.strip():
            raise ValueError('请写下你的方向')
        return start_script(p, remember_angle(p, topic, body.title, body.detail), research, topic, model=body.model)


def ground_script(result, sources, previous=None):
    """Citations are references, not a claim that a model fact-checked the sentence."""
    result = copy.deepcopy(result)
    by_url = {s['url']: s for s in sources}
    before = {p['id']: p for p in (previous or {}).get('paragraphs', [])}
    for paragraph in result.get('paragraphs', []):
        urls = paragraph.get('source_urls', [])
        if not isinstance(urls, list):
            urls = []
        old = before.get(paragraph.get('id'))
        changed = old and old.get('text') != paragraph.get('text')
        paragraph['source_urls'] = list(dict.fromkeys(str(u) for u in urls if u in by_url))
        paragraph['citations'] = [
            {'source_id': by_url[u]['id'], 'url': u, 'title': by_url[u].get('title') or u,
             'verification': by_url[u].get('verification', 'unverified'),
             'evidence_note': by_url[u].get('evidence_note', '')}
            for u in paragraph['source_urls']
        ]
        paragraph['evidence_status'] = ('review' if changed else
            'referenced' if paragraph['citations'] else 'opinion' if paragraph.get('claim_type') == 'opinion' else 'unlinked')
    return result


def recommend(paragraph, assets):
    text = ' '.join([paragraph.get('cue', ''), paragraph.get('text', ''),
                     ' '.join(paragraph.get('visual_keywords', []))]).lower()
    tokens = set(re.findall(r'[a-z0-9]{2,}|[\u4e00-\u9fff]{2}', text))
    ranked = []
    for asset in assets:
        if asset.get('generated') or (asset_store.asset_type(asset) == 'Music' and paragraph.get('speaker') != '音乐'):
            continue
        haystack = (asset.get('name', '') + ' ' + asset.get('provenance', '') + ' ' +
                    ' '.join(t['text'] for t in asset.get('analysis', {}).get('transcript', []))).lower()
        score = sum(1 for t in tokens if t in haystack)
        ranked.append({'asset_id': asset['id'], 'score': score,
                       'reason': '与文案或画面关键词匹配' if score else '可用素材 · 请确认画面是否合适'})
    return sorted(ranked, key=lambda a: a['score'], reverse=True)[:3]


def build_scenes(project, script, previous=None):
    assets = store.listing('asset', project['id'])
    existing = {s['paragraph_id']: s for s in (previous or {}).get('scenes', [])}
    result, cursor = [], 0.0
    for index, p in enumerate(script['result']['paragraphs']):
        duration = max(2.0, min(45.0, float(p.get('estimated_sec') or len(p['text']) / 4.2)))
        options = recommend(p, assets)
        old = existing.get(p['id'], {})
        same_text = old.get('narration') == p['text']
        if same_text and old.get('duration_choice') == 'manual':
            duration = old['duration']
        selected = old.get('asset_id') if same_text and old.get('asset_choice') == 'manual' and any(a['id'] == old.get('asset_id') for a in assets) else (
            options[0 if options[0]['score'] > 0 else index % len(options)]['asset_id'] if options else None)
        result.append({'id': old.get('id', store.uid()), 'paragraph_id': p['id'], 'order': index,
                       'start': round(cursor, 2), 'end': round(cursor + duration, 2), 'duration': round(duration, 2),
                        'narration': p['text'], 'subtitle': '', 'source_urls': p.get('source_urls', []),
                       'visual': p.get('cue') or '重点文字卡，配合口播解释',
                       'asset_id': selected, 'asset_choice': 'manual' if same_text and old.get('asset_choice') == 'manual' else 'suggested',
                       'duration_choice': 'manual' if same_text and old.get('duration_choice') == 'manual' else 'suggested',
                       'recommendations': options, 'editing': '开场突出问题' if index == 0 else '直切 · 保持画面节奏'})
        cursor += duration
    return {'scenes': result, 'duration': round(cursor, 2), 'script_version_id': script['id'],
            'aspect': project.get('requirements', {}).get('aspect', project['defaults'].get('aspect', '9:16'))}


def save_scenes(project, script):
    old_id = workspace(project).get('scene_version')
    old = owned(project['id'], old_id, 'scenes') if old_id else None
    version = store.put('version', {'stage': 'scenes', 'parent_id': old_id,
                         'result': build_scenes(project, script, old['result'] if old else None),
                         'upstream': dict(project['adopted']), 'model_config': {'model': 'local'},
                         'prompt': '从已确认文案同步分镜', 'stale': False}, project['id'])
    workspace(project)['scene_version'] = version['id']
    invalidate(project, 'edit')
    clear_stale(project, 'scenes')
    return version


class ScriptSave(BaseModel):
    parent_id: str
    result: dict


@router.post('/projects/{project_id}/workspace/script')
def save_script(project_id: str, body: ScriptSave):
    with jobs.LOCK:
        parent = owned(project_id, body.parent_id, 'script')
        p = store.get('project', project_id)
        w = workspace(p)
        current = w.get('script_version')
        if current and current != body.parent_id:
            raise ValueError('脚本已有更新，请先查看最新版本。你的编辑仍保留在当前页面。')
        result = jobs.protect_script(ground_script(body.result, store.listing('source', project_id), parent['result']))
        version = store.put('version', {'stage': 'script', 'parent_id': parent['id'], 'result': result,
                             'upstream': dict(p['adopted']), 'selected_topic_id': p.get('selected_topic_id'),
                             'prompt': '直接编辑', 'effective': parent.get('effective', {}),
                             'model_config': {'model': 'manual'}, 'stale': False}, project_id)
        w['script_version'] = version['id']
        # Once scenes exist, direct editing synchronizes them without losing earlier cuts.
        if w.get('scene_version'):
            p['adopted']['script'] = version['id']
            save_scenes(p, version)
        clear_stale(p, 'script')
        store.put('project', p)
        return version


class ScriptAccept(BaseModel):
    version_id: str


@router.post('/projects/{project_id}/workspace/scenes')
def accept_script(project_id: str, body: ScriptAccept):
    with jobs.LOCK:
        idle(project_id)
        p = store.get('project', project_id)
        script = owned(project_id, body.version_id, 'script')
        if not script['result'].get('paragraphs'):
            raise ValueError('请先完成脚本')
        if 'script' in p.get('stale_stages', []) and workspace(p).get('script_version') != script['id']:
            raise ValueError('选题或角度已改变，请先生成相应脚本')
        p['adopted']['script'] = script['id']
        workspace(p)['script_version'] = script['id']
        clear_stale(p, 'script')
        result = save_scenes(p, script)
        p['status'] = '待确认画面'
        store.put('project', p)
        return result


@router.post('/projects/{project_id}/workspace/restore')
def restore_script(project_id: str, body: ScriptAccept):
    with jobs.LOCK:
        idle(project_id)
        script = owned(project_id, body.version_id, 'script')
        if not script['result'].get('paragraphs'):
            raise ValueError('请选择完整脚本')
        p = store.get('project', project_id)
        # Restore as a new branch, retaining old lineage and current project context.
        v = store.put('version', {'stage': 'script', 'parent_id': script['id'], 'result': script['result'],
                     'upstream': dict(p['adopted']), 'selected_topic_id': p.get('selected_topic_id'),
                     'model_config': {'model': 'manual'}, 'effective': script.get('effective', {}),
                     'prompt': '从历史版本继续创作', 'stale': False}, project_id)
        workspace(p)['script_version'] = v['id']
        p['adopted']['script'] = v['id']
        clear_stale(p, 'script')
        if workspace(p).get('scene_version'):
            save_scenes(p, v)
        invalidate(p, 'edit')
        store.put('project', p)
        return v


class SceneUpdate(BaseModel):
    version_id: str
    scene_id: str
    asset_id: str | None = None
    duration: float | None = Field(default=None, ge=1, le=120)


@router.patch('/projects/{project_id}/workspace/scenes')
def update_scene(project_id: str, body: SceneUpdate):
    with jobs.LOCK:
        idle(project_id)
        p = store.get('project', project_id)
        if workspace(p).get('scene_version') != body.version_id:
            raise ValueError('分镜已更新，请重新打开当前分镜')
        previous = owned(project_id, body.version_id, 'scenes')
        result = copy.deepcopy(previous['result'])
        scene = next((s for s in result['scenes'] if s['id'] == body.scene_id), None)
        if not scene:
            raise ValueError('分镜不存在')
        if body.asset_id:
            asset = store.get('asset', body.asset_id)
            if asset['project_id'] != project_id:
                raise ValueError('请选择当前视频的画面素材')
        if 'asset_id' in body.model_fields_set:
            scene.update(asset_id=body.asset_id, asset_choice='manual')
        if body.duration is not None:
            scene['duration'] = body.duration
            scene['duration_choice'] = 'manual'
        cursor = 0
        for item in result['scenes']:
            item['start'], item['end'] = round(cursor, 2), round(cursor + item['duration'], 2)
            cursor += item['duration']
        result['duration'] = round(cursor, 2)
        v = store.put('version', {k: v for k, v in previous.items() if k not in ('id', 'created_at', 'updated_at')} |
                      {'parent_id': previous['id'], 'result': result}, project_id)
        workspace(p)['scene_version'] = v['id']
        invalidate(p, 'edit')
        store.put('project', p)
        return v


class Rewrite(BaseModel):
    version_id: str
    paragraph_id: str
    instruction: str = Field(min_length=1, max_length=1000)
    model: str | None = None


@router.post('/projects/{project_id}/workspace/rewrite')
def rewrite(project_id: str, body: Rewrite):
    with jobs.LOCK:
        idle(project_id)
        v = owned(project_id, body.version_id, 'script')
        paragraph = next((p for p in v['result'].get('paragraphs', []) if p['id'] == body.paragraph_id), None)
        if not paragraph or paragraph.get('locked'):
            raise ValueError('该段落不存在或已锁定')
        return jobs.submit(project_id, 'script', {'workspace': True, 'base_version_id': v['id'],
                           'model': body.model,
                           'target_ids': [body.paragraph_id], 'prompt': body.instruction})


@router.post('/projects/{project_id}/workspace/first-cut')
def first_cut(project_id: str):
    with jobs.LOCK:
        idle(project_id)
        p = store.get('project', project_id)
        ident = workspace(p).get('scene_version')
        if not ident or 'scenes' in p.get('stale_stages', []):
            raise ValueError('请先确认最新脚本，更新分镜')
        owned(project_id, ident, 'scenes')
        return jobs.submit(project_id, 'first_cut', {'workspace': True, 'scene_version_id': ident})


@router.get('/library')
def library():
    projects = {p['id']: p['name'] for p in store.listing('project')}
    assets = [dict(asset_store.describe(a), project_name=projects.get(a.get('project_id'), '公共素材库')) for a in store.listing('asset') if not a.get('generated')]
    films = [dict(v, project_name=projects.get(v['project_id'], '')) for v in store.listing('version')
             if v['stage'] == 'edit' and (v.get('preview') or v.get('final'))]
    return {'assets': assets, 'public_assets': [a for a in assets if a['scope'] == 'public'], 'films': films}


class VideoStart(BaseModel):
    submit: bool = True


@router.post('/projects/{project_id}/workspace/start-video')
def start_video(project_id: str, body: VideoStart | None = None):
    with jobs.LOCK:
        idle(project_id)
        p = store.get('project', project_id)
        workspace(p)['intent'] = 'video'
        p['status'] = '待制作'
        store.put('project', p)
        if not claude_cli.cli_command():
            return {'task': None, 'needs_cli': True}
        if body and not body.submit:
            # Assets were just added; the user writes the prompt first.
            return {'task': None, 'needs_cli': False}
        task = jobs.submit(project_id, 'cli_video', {'prompt': workspace(p).get('idea', p['name'])})
        return {'task': task, 'needs_cli': False}


class AssetImport(BaseModel):
    asset_id: str


@router.post('/projects/{project_id}/workspace/import-asset')
def import_asset(project_id: str, body: AssetImport):
    with jobs.LOCK:
        idle(project_id)
        p = store.get('project', project_id)
        original = asset_store.describe(store.get('asset', body.asset_id))
        for a in store.listing('asset', project_id):
            if a.get('sha256') and a['sha256'] == original.get('sha256') and asset_store.asset_type(a) == original['type']:
                return a
        ident = store.uid()
        relative = f"assets/{ident}{asset_store.file(original).suffix}"
        target = store.project_file(project_id, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(asset_store.file(original), target)
        a = {k: v for k, v in original.items() if k not in ('id', 'created_at', 'updated_at', 'project_id', 'analysis', 'storage_project_id')}
        a.update(id=ident, path=relative, scope='project', storage_project_id=project_id)
        result = store.put('asset', a, project_id)
        invalidate(p, 'edit')
        store.put('project', p)
        return result


def make_graphic(project_id, scene, cancel, report, aspect='9:16'):
    """An actual editable graphic asset; deliberately no invented stock footage or voice."""
    fingerprint = hashlib.sha256(('card-v2' + aspect + scene['id'] + scene['narration'] + str(scene['duration'])).encode()).hexdigest()
    cached = next((a for a in store.listing('asset', project_id) if a.get('generated') and a.get('sha256') == fingerprint), None)
    if cached and store.project_file(project_id, cached['path']).exists():
        return cached
    ident = store.uid()
    relative = f'assets/{ident}.mp4'
    path = store.project_file(project_id, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    report('正在制作缺失画面的文字卡')
    if not media.font_path():
        raise ValueError('文字卡需要可用的中文字体，请在高级设置中检查本地字体')
    width, height = {'9:16': (540, 960), '16:9': (960, 540), '1:1': (540, 540)}[aspect]
    folder = store.project_dir(project_id) / 'analysis' / ident
    folder.mkdir(parents=True, exist_ok=True)
    ffmpeg = media.executable('ffmpeg')
    background = (f'color=c=0x172b26:s={width}x{height}:r=30,'
                  f'drawbox=x={round(width*.08)}:y={round(height*.1)}:w=4:h={round(height*.16)}:color=0xb7d69a:t=fill,'
                  f'drawbox=x={round(width*.08)}:y={round(height*.92)}:w={round(width*.84)}:h=2:color=0x466554:t=fill')
    args = [ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i', background]
    if media.has_ass_filter(ffmpeg):
        media.write_ass(folder / 'card.ass', [], width, height, scene['narration'], scene['duration'], 5)
        args += ['-vf', f"ass=card.ass:fontsdir='{media.escaped_filter_path(Path(media.font_path()).parent)}'"]
    else:
        inputs, filters, output = media.text_overlays(folder, [{'text': scene['narration'], 'start': 0, 'end': scene['duration']}],
                                                     width, height, cancel, card=True)
        args += [*inputs, '-filter_complex', filters, '-map', output]
    media.run([*args, '-t', str(scene['duration']), *media.video_encoding(), '-pix_fmt', 'yuv420p', str(path)], cancel, folder)
    return store.put('asset', {'id': ident, 'name': f"文字卡 {scene['order'] + 1:02}", 'kind': 'graphic',
                              'path': relative, 'generated': True, 'sha256': fingerprint, 'size': path.stat().st_size,
                              'protected': [], 'provenance': '工作台根据脚本自动制作的文字卡',
                              'analysis': {'duration': scene['duration'], 'has_video': True, 'has_audio': False,
                                           'transcript': [], 'frames': []}}, project_id)


def execute_first_cut(task, cancel, report):
    if not media.executable('ffmpeg') or not media.executable('ffprobe'):
        raise ValueError('视频处理组件尚未就绪。分镜和素材已保存，请在高级设置中连接本地视频工具后重试。')
    project_id = task['project_id']
    scenes = owned(project_id, task['payload']['scene_version_id'], 'scenes')
    by_id = {a['id']: a for a in task['asset_snapshot']}
    segments, captions, used, offsets, notes = [], [], {}, {}, []
    cursor = 0.0
    for scene in scenes['result']['scenes']:
        if cancel.is_set():
            raise media.Cancelled()
        asset = by_id.get(scene.get('asset_id'))
        if asset:
            asset = media.analyze(asset, cancel, report)
            start = offsets.get(asset['id'], 0)
            available = asset['analysis']['duration'] - start
            # Protected performances remain complete, even when the requested scene is shorter.
            if asset.get('protected'):
                start, duration = 0, asset['analysis']['duration']
                notes.append(f"{asset['name']} 的受保护片段已完整保留")
            elif asset.get('kind') == 'image':
                start, duration = 0, scene['duration']
                asset['analysis']['duration'] = max(asset['analysis']['duration'], duration)
            else:
                duration = min(scene['duration'], available)
                if 1 <= duration < scene['duration']:
                    notes.append(f"分镜 {scene['order'] + 1} 的素材较短，时长调整为 {duration:.1f} 秒")
            if duration < 1:
                asset = None
        if not asset:
            asset = make_graphic(project_id, scene, cancel, report, scenes['result']['aspect'])
            start, duration = 0, scene['duration']
            notes.append(f"分镜 {scene['order'] + 1} 使用文字卡，待补充画面或配音")
        used[asset['id']] = asset
        offsets[asset['id']] = start + duration
        segments.append({'id': store.uid(), 'scene_id': scene['id'], 'source_asset_id': asset['id'],
                         'source_start_sec': start, 'source_end_sec': start + duration,
                         'reason': scene['visual'], 'volume': 1})
        cursor += duration
    for original in task['asset_snapshot']:
        if asset_store.asset_type(original) == 'Music' and original['id'] not in used:
            asset = media.analyze(original, cancel, report)
            used[asset['id']] = asset
            segments.append({'id': store.uid(), 'source_asset_id': asset['id'], 'source_start_sec': 0,
                             'source_end_sec': asset['analysis']['duration'], 'reason': '完整保留 Music 素材', 'volume': 1})
            notes.append(f"{asset['name']} 已作为完整音乐片段保留")
    result = timeline.validate({'aspect': scenes['result']['aspect'], 'segments': segments, 'captions': captions,
                                'summary': '根据脚本分镜自动组装初剪', 'notes': list(dict.fromkeys(notes)),
                                'scene_version_id': scenes['id'],
                                'has_recorded_audio': any(a['analysis']['has_audio'] for a in used.values())}, list(used.values()))
    v = jobs.version(task, 'edit', result, task['upstream'].get('edit'))
    v['asset_ids'] = list(used)
    v['asset_snapshot'] = list(used.values())
    v['scene_version_id'] = scenes['id']
    v['preview'] = media.render(project_id, v, list(used.values()), cancel, report)
    with jobs.LOCK:
        p = store.get('project', project_id)
        current = workspace(p).get('scene_version') == scenes['id'] and p['adopted'].get('script') == task['upstream'].get('script')
        v['stale'] = not current
        store.put('version', v, project_id)
        if current:
            workspace(p)['edit_version'] = v['id']
            p['adopted']['edit'] = v['id']
            p['status'] = '待审片'
            clear_stale(p, 'edit')
            store.put('project', p)
    return {'version_id': v['id'], 'artifacts': v['preview']}


def completed_script(task, version):
    with jobs.LOCK:
        p = store.get('project', task['project_id'])
        expected = task['snapshot'].get('workspace', {}).get('script_version')
        w = workspace(p)
        if w.get('script_version') != expected:
            version['stale'] = True
            store.put('version', version, task['project_id'])
            return
        w['script_version'] = version['id']
        clear_stale(p, 'script')
        invalidate(p, 'scenes', 'edit')
        p['status'] = '待确认脚本'
        store.put('project', p)
