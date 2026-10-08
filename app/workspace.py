"""V2 orchestration over the existing project, job, version and media stores."""
import copy
import hashlib
import re
import shutil
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter
from pydantic import BaseModel, Field

from . import assets as asset_store, claude_cli, config, docs, jobs, media, store, text_cli, timeline

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
    start: bool = True  # False: only create the project; the creator starts work inside it.


CHAT_ACTIONS = {'clarify': ('none', 'write_idea', 'research', 'write_script'),
                'angles': ('none', 'write_idea', 'research', 'write_script', 'angles'),
                'script': ('none', 'write_idea', 'research', 'write_script', 'angles', 'revise_script')}


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
        'workspace': {'idea': idea, 'intent': body.intent, 'brainstorm': body.idea.strip()},
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
    task = None
    if text_cli.available() and body.intent != 'assets':
        request, refs = docs.default_request(project, 'research'), docs.default_refs(project, 'research')
        task = submit_run(project, 'research', request, refs, docs.compose(project, 'research', request, refs))
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
    docs.sync(p, 'research', v)
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


def start_script(p, request, prompt, angle=None, research=None, topic=None, model=None):
    if research:
        p['adopted']['research'] = research['id']
    if topic:
        p.update(selected_topic=topic, selected_topic_id=topic['id'])
        if not p.get('name_custom') and topic.get('title'):
            p['name'] = str(topic['title'])[:80]
    p['status'] = '正在构思脚本'
    w = workspace(p)
    if angle:
        w['angle'] = angle
    else:
        w.pop('angle', None)
    if research:
        w['research_version'] = research['id']
    w.pop('script_version', None)
    invalidate(p, 'script', 'scenes', 'edit')
    clear_stale(p, 'research')
    store.put('project', p)
    return jobs.submit(p['id'], 'script', {'model': model, 'workspace': True, 'fresh_script': True,
                                           'angle': angle['title'] if angle else '', 'prompt': request},
                       prompt_override=prompt)


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
    context = {'stage': stage, 'brainstorm': w.get('brainstorm') or w.get('idea', p['name']),
               'my_idea': docs.read(project_id, 'idea'),
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
        if current.get('status') == '构思中':
            current['status'] = '明确需求中'
        store.put('project', current)
        limit = 12000 if action == 'write_idea' else 2000
        message = store.put('message', {
            'stage': 'chat', 'role': 'assistant', 'text': reply[:4000], 'question': str(result.get('question') or '').strip()[:1000],
            'options': options, 'action': action, 'action_input': str(result.get('action_input') or '').strip()[:limit],
            'action_label': str(result.get('action_label') or '').strip()[:40], 'task_id': task['id']}, project_id)
    return {'message_id': message['id']}


class ChatAction(BaseModel):
    message_id: str


@router.post('/projects/{project_id}/workspace/chat/action')
def chat_action(project_id: str, body: ChatAction):
    with jobs.LOCK:
        idle(project_id)
        message = store.get('message', body.message_id)
        if message.get('project_id') != project_id or message.get('stage') != 'chat' or message.get('action') in (None, 'none'):
            raise ValueError('这条消息没有可执行的建议')
        if message.get('action_task_id') or message.get('action_done'):
            raise ValueError('这个建议已经执行过了')
        p, text = store.get('project', project_id), message.get('action_input', '')
        research = current_research(p)
        if message['action'] == 'write_idea':
            if not text.strip():
                raise ValueError('这条建议没有可写入的内容')
            docs.write(p, 'idea', text.strip() + '\n', 'chat')
            store.put('project', p)
            message['action_done'] = True
            store.put('message', message, project_id)
            return {'doc': 'idea'}
        if message['action'] in ('research', 'write_script'):
            # These start from a text the creator checks first: see /workspace/compose and /workspace/run.
            raise ValueError('请在发送前确认原文')
        if message['action'] == 'angles':
            if not research or not current_topic(p, research):
                raise ValueError('还没有调研结果，请先开始调研')
            task = submit_angles(p, research, current_topic(p, research), text)
        else:
            script = current_script(p)
            if not script:
                raise ValueError('还没有文案，请先写文案')
            task = jobs.submit(project_id, 'script', {'prompt': text, 'base_version_id': script['id'], 'workspace': True})
        message['action_task_id'] = task['id']
        store.put('message', message, project_id)
        return task


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
               'feedback': payload.get('feedback', ''), 'my_idea': docs.read(project_id, 'idea'), 'idea': workspace(p).get('idea'),
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


class Brainstorm(BaseModel):
    text: str = Field(default='', max_length=20000)


@router.put('/projects/{project_id}/workspace/brainstorm')
def save_brainstorm(project_id: str, body: Brainstorm):
    with jobs.LOCK:
        p = store.get('project', project_id)
        workspace(p)['brainstorm'] = body.text
        store.put('project', p)
        return {'brainstorm': body.text}


class DocText(BaseModel):
    text: str = Field(default='', max_length=200000)
    origin: str = 'manual'


def doc_key(key):
    if key not in docs.NAMES:
        raise ValueError('文件不存在')
    return key


def editable_key(key):
    if doc_key(key) == 'materials':
        raise ValueError('素材.md 由素材列表生成，请在「素材」里修改用途和说明')
    return key


@router.put('/projects/{project_id}/workspace/docs/{key}')
def save_doc(project_id: str, key: str, body: DocText):
    """The creator's own text. 原封不动 saves the brainstorm as-is."""
    if body.origin not in ('manual', 'brainstorm') or (body.origin == 'brainstorm' and key != 'idea'):
        raise ValueError('来源不支持')
    with jobs.LOCK:
        p = store.get('project', project_id)
        docs.write(p, editable_key(key), body.text, body.origin)
        docs.meta(p)[key].pop('pending', None)
        store.put('project', p)
        return docs.status(p)[key]


@router.post('/projects/{project_id}/workspace/docs/{key}/undo')
def undo_doc(project_id: str, key: str):
    with jobs.LOCK:
        p = store.get('project', project_id)
        docs.undo(p, editable_key(key))
        store.put('project', p)
        return docs.status(p)[key]


@router.post('/projects/{project_id}/workspace/docs/{key}/sync')
def sync_doc(project_id: str, key: str):
    """Overwrite a hand-edited 调研.md / 文案.md with the newest result."""
    with jobs.LOCK:
        p = store.get('project', project_id)
        version = current_research(p) if editable_key(key) == 'research' else current_script(p) if key == 'script' else None
        if not version:
            raise ValueError('还没有可以写入的结果')
        docs.meta(p)[key] = {k: v for k, v in docs.meta(p).get(key, {}).items() if k != 'pending'} | {'origin': 'sync'}
        docs.sync(p, key, version)
        store.put('project', p)
        return docs.status(p)[key]


class Compose(BaseModel):
    module: str
    request: str | None = Field(default=None, max_length=20000)  # None: the module's default request
    refs: list[str] | None = None  # None: the module's default references
    abilities: list[str] = []  # video: guides in agent-ability/ Claude is told to read


def compose_parts(p, body):
    if body.module not in docs.MODULES:
        raise ValueError('不支持的模块')
    request = docs.default_request(p, body.module) if body.request is None else body.request
    refs = docs.default_refs(p, body.module) if body.refs is None else [doc_key(k) for k in body.refs]
    allowed = docs.ALLOWED_REFS.get(body.module)
    return request, [k for k in refs if allowed is None or k in allowed]


@router.post('/projects/{project_id}/workspace/compose')
def compose(project_id: str, body: Compose):
    """The exact text a run would send, for the creator to check and edit first."""
    p = store.get('project', project_id)
    request, refs = compose_parts(p, body)
    return {'module': body.module, 'request': request, 'refs': refs,
            'abilities': docs.abilities() if body.module == 'video' else {},
            'prompt': docs.compose(p, body.module, request, refs, workspace(p).get('brainstorm', ''), body.abilities)}


class AngleRef(BaseModel):
    version_id: str
    topic_id: str
    angle_id: str


class Run(Compose):
    prompt: str = Field(min_length=1, max_length=1_000_000)  # sent as-is
    model: str | None = None
    base_version_id: str | None = None  # video: continue from this version
    angle: AngleRef | None = None  # script: written from a research angle
    message_id: str | None = None  # the conversation suggestion this run carries out


def submit_run(p, module, request, refs, prompt, model=None, angle=None, base_version_id=None):
    if module == 'video':
        if not claude_cli.cli_command():
            raise ValueError('未找到 Claude Code。请在设置中配置并检测本机 CLI。')
        return jobs.submit(p['id'], 'cli_video', {'prompt': prompt, 'base_version_id': base_version_id, 'refs': refs})
    if not text_cli.available():
        raise ValueError('请先在设置中连接 Claude 订阅或 API 服务')
    if module == 'polish':
        return jobs.submit(p['id'], 'polish', {'prompt': request, 'model': model}, prompt_override=prompt)
    if module == 'research':
        if p.get('adopted'):
            invalidate(p, 'research', 'script', 'scenes', 'edit')
        store.put('project', p)
        return jobs.submit(p['id'], 'research', {'prompt': request, 'refs': refs, 'workspace': True, 'model': model},
                           prompt_override=prompt)
    research = topic = chosen = None
    if angle:
        research = owned(p['id'], angle.version_id, 'research')
        topic = next((t for t in research['result'].get('topics', []) if t['id'] == angle.topic_id), None)
        chosen = next((a for a in angles_for(topic, p) if a['id'] == angle.angle_id), None) if topic else None
        if not chosen:
            raise ValueError('这个角度已经不存在，请重新选择')
    return start_script(p, request, prompt, chosen, research, topic, model)


@router.post('/projects/{project_id}/workspace/run')
def run(project_id: str, body: Run):
    with jobs.LOCK:
        idle(project_id)
        p = store.get('project', project_id)
        request, refs = compose_parts(p, body)
        if not body.prompt.strip():
            raise ValueError('发送的原文不能为空')
        message = store.get('message', body.message_id) if body.message_id else None
        if message and (message.get('project_id') != project_id or message.get('stage') != 'chat'):
            raise ValueError('对话建议不属于当前项目')
        task = submit_run(p, body.module, request, refs, body.prompt, body.model, body.angle, body.base_version_id)
        if message:
            message['action_task_id'] = task['id']
            store.put('message', message, project_id)
        return task


class MaterialUpdate(BaseModel):
    purpose: str | None = None
    purpose_label: str | None = Field(default=None, max_length=40)
    note: str | None = Field(default=None, max_length=4000)


def own_material(project_id, asset_id):
    asset = store.get('asset', asset_id)
    if asset.get('project_id') != project_id or asset.get('generated'):
        raise ValueError('素材不属于当前项目')
    return asset


@router.patch('/projects/{project_id}/workspace/materials/{asset_id}')
def update_material(project_id: str, asset_id: str, body: MaterialUpdate):
    """What a material is for: cut into the video, a reference, the music, or the creator's own words."""
    with jobs.LOCK:
        asset = own_material(project_id, asset_id)
        if body.purpose is not None:
            if body.purpose not in docs.PURPOSES:
                raise ValueError('素材用途不支持')
            asset['purpose'] = body.purpose
        for field in ('purpose_label', 'note'):
            if getattr(body, field) is not None:
                asset[field] = getattr(body, field).strip()
        asset = store.put('asset', asset, project_id)
        p = store.get('project', project_id)
        docs.refresh_materials(p)
        store.put('project', p)
        return asset_store.describe(asset)


class MaterialOrder(BaseModel):
    order: list[str] | None = None
    sequence: str | None = None  # fixed: cut in this order; free: Claude decides


@router.put('/projects/{project_id}/workspace/materials')
def order_materials(project_id: str, body: MaterialOrder):
    with jobs.LOCK:
        p = store.get('project', project_id)
        w = workspace(p)
        if body.order is not None:
            own = {a['id'] for a in docs.materials(p)}
            if len(set(body.order)) != len(body.order) or not set(body.order) <= own:
                raise ValueError('素材顺序与当前素材不一致，请刷新页面')
            w['material_order'] = body.order + [a['id'] for a in docs.materials(p) if a['id'] not in body.order]
        if body.sequence is not None:
            if body.sequence not in ('fixed', 'free'):
                raise ValueError('剪辑顺序设置不支持')
            w['material_sequence'] = body.sequence
        docs.refresh_materials(p)
        store.put('project', p)
        return {'order': w.get('material_order', []), 'sequence': w.get('material_sequence', 'free')}


@router.delete('/projects/{project_id}/workspace/materials/{asset_id}')
def remove_material(project_id: str, asset_id: str):
    with jobs.LOCK:
        idle(project_id)
        asset = own_material(project_id, asset_id)
        store.delete('asset', asset_id)
        # The file goes too, unless another record (the library, another project) still uses it.
        owner = asset.get('storage_project_id', project_id)
        if not any(a.get('path') == asset['path'] and a.get('storage_project_id', a.get('project_id')) == owner
                   for a in store.listing('asset')):
            asset_store.file(asset).unlink(missing_ok=True)
        p = store.get('project', project_id)
        w = workspace(p)
        w['material_order'] = [i for i in w.get('material_order', []) if i != asset_id]
        invalidate(p, 'edit')
        docs.refresh_materials(p)
        store.put('project', p)
        return {'ok': True}


def execute_polish(task, event, report):
    from . import models
    result, _, _ = models.call(task, 'idea', {'effective': task['effective']}, event, report)
    text = str(result.get('idea') or '').strip()
    if not text:
        raise ValueError('Claude 没有给出整理后的想法，请重试')
    with jobs.LOCK:
        p = store.get('project', task['project_id'])
        docs.write(p, 'idea', text + '\n', 'polish')
        store.put('project', p)
    return {'doc': 'idea'}


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
        docs.sync(p, 'script', version)
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
        docs.sync(p, 'script', v)
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
        docs.refresh_materials(p)
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
        docs.sync(p, 'script', version)
        clear_stale(p, 'script')
        invalidate(p, 'scenes', 'edit')
        p['status'] = '待确认脚本'
        store.put('project', p)
