import csv
import hashlib
import io
import json
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / '.env')

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import assets as asset_store, catalog, claude_cli, config, docs, jobs, media, models, store, text_cli, timeline


@asynccontextmanager
async def lifespan(app):
    store.init()
    asset_store.migrate()
    jobs.start()
    yield
    jobs.close()


app = FastAPI(title='声间 AI 视频创作空间', lifespan=lifespan)


@app.middleware('http')
async def local_only(request: Request, call_next):
    host = request.headers.get('host', '').split(':')[0]
    if host not in ('127.0.0.1', 'localhost', 'testserver'):
        return JSONResponse({'detail': '工作台只支持本机访问'}, status_code=403)
    origin = request.headers.get('origin')
    if origin and urlparse(origin).netloc != request.headers.get('host'):
        return JSONResponse({'detail': '跨站请求被拒绝'}, status_code=403)
    response = await call_next(request)
    # Local upgrades must not mix cached frontend files from different releases.
    if request.url.path in ('/', '/index.html', '/app.js', '/workspace.js', '/assets.js', '/settings.js', '/discovery.js', '/styles.css', '/v2.css', '/polish.css'):
        response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@app.exception_handler(ValueError)
async def value_error(request, exc):
    return JSONResponse({'detail': str(exc)}, status_code=400)


class ProjectInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    requirements: dict = Field(default_factory=dict)


class TaskInput(BaseModel):
    kind: str
    model: str | None = None
    prompt: str = Field(default='', max_length=20000)
    settings: dict = Field(default_factory=dict)
    intent: str = 'generate'
    version_id: str | None = None
    base_version_id: str | None = None
    target_ids: list[str] = Field(default_factory=list)
    angle: str | None = None
    transcribe: bool = False
    workspace: bool = False


@app.get('/api/environment')
def env():
    return media.environment() | {'claude_cli': claude_cli.status(), 'creation_engine': text_cli.settings()['engine'],
                                  'creation_configured': text_cli.available()}


@app.get('/api/claude-code')
def cli_status():
    return claude_cli.status()


@app.put('/api/claude-code')
def save_cli(body: dict):
    return claude_cli.save(body)


@app.post('/api/claude-code/check')
def check_cli():
    return claude_cli.inspect_cli()


@app.get('/api/settings')
def settings():
    defaults = store.settings() | {'model_' + stage: catalog.resolve(stage) for stage in catalog.STAGE_DEFAULTS}
    if text_cli.enabled():
        defaults.update({'model_' + stage: text_cli.resolve(stage) for stage in text_cli.STAGES})
    return {'defaults': defaults, 'usage': models.monthly_usage(), 'connection': config.public(),
            'models': text_cli.MODELS if text_cli.enabled() else catalog.compatible(config.provider()),
            'model_catalog': list(catalog.MODELS.values()) + text_cli.MODELS, 'text_service': text_cli.settings(),
            'capabilities': {'asset_ownership': True, 'task_snapshots': True, 'subtitles': False, 'subscription_text': True}}


@app.put('/api/settings/text-service')
def save_text_service(body: dict):
    return text_cli.save(body)


@app.put('/api/connection')
def save_connection(body: dict):
    return config.save(body)


@app.post('/api/connection/test')
def test_connection(body: dict):
    return models.test_connection(body.get('model') or catalog.resolve('script'))


PREFERENCE_FIELDS = {'audience', 'roles', 'style', 'platform', 'duration', 'aspect', 'exclude', 'days', 'search_limit'}
BUDGET_FIELDS = {'monthly_budget', 'max_tokens'}


def scoped_settings(body, allowed):
    if set(body) - allowed:
        raise ValueError('设置包含其他分组的字段，请分别保存')
    return save_settings(body)


@app.put('/api/settings/preferences')
def save_preferences(body: dict):
    return scoped_settings(body, PREFERENCE_FIELDS)


@app.put('/api/settings/budget')
def save_budget(body: dict):
    return scoped_settings(body, BUDGET_FIELDS)


@app.post('/api/connections')
def save_api_connection(body: dict):
    return config.save_profile(body)


@app.post('/api/connections/{ident}/activate')
def activate_api_connection(ident: str):
    return config.activate_profile(ident)


@app.delete('/api/connections/{ident}')
def delete_api_connection(ident: str):
    return config.delete_profile(ident)


@app.put('/api/settings')
def save_settings(body: dict):
    value = store.settings() | {k: v for k, v in body.items() if k in store.DEFAULTS}
    for key in ('monthly_budget', 'input_price', 'output_price', 'search_price'):
        value[key] = timeline.number(value[key])
        if value[key] < 0 or (key == 'monthly_budget' and value[key] == 0):
            raise ValueError('预算必须大于零，单价不能为负')
    for key, low, high in [('search_limit', 1, 10), ('max_tokens', 512, 16000), ('days', 1, 365), ('duration', 10, 3600)]:
        value[key] = int(value[key])
        if not low <= value[key] <= high:
            raise ValueError(f'{key} 应在 {low}–{high} 之间')
    if value['aspect'] not in ('9:16', '16:9', '1:1'):
        raise ValueError('画幅不支持')
    for stage in ('research', 'script', 'edit'):
        catalog.validate(value['model_' + stage])
    store.save_settings(value)
    return value


@app.get('/api/projects')
def projects():
    latest = {}
    for t in store.listing('task'):
        latest.setdefault(t.get('project_id'), {'kind': t['kind'], 'status': t['status'], 'error': t.get('error'),
                                                 'created_at': t['created_at']})
    return sorted([dict(p, last_task=latest.get(p['id'])) for p in store.listing('project')],
                  key=lambda p: p['updated_at'], reverse=True)


@app.delete('/api/projects/{project_id}')
def delete_project(project_id: str):
    import shutil
    with jobs.LOCK:
        store.get('project', project_id)
        if any(t['status'] in ('queued', 'running', 'cancelling') for t in store.listing('task', project_id)):
            raise ValueError('这个作品正在制作中，请先取消当前任务再删除。')
        # Assets moved to the public library or another project keep their original
        # storage folder; that folder must survive while anything still points at it.
        foreign = [a for a in store.listing('asset') if a.get('storage_project_id') == project_id and a.get('project_id') != project_id]
        task_ids = [t['id'] for t in store.listing('task', project_id)]
        with store.connection() as db:
            # Usage rows stay so the monthly budget remains accurate.
            db.execute("DELETE FROM objects WHERE project_id=? AND kind!='usage'", (project_id,))
            db.execute("DELETE FROM objects WHERE id=? AND kind='project'", (project_id,))
            db.executemany('DELETE FROM task_credentials WHERE id=?', [(i,) for i in task_ids])
        folder = store.DATA / 'projects' / project_id
        if folder.is_dir() and not foreign:
            shutil.rmtree(folder, ignore_errors=True)
        return {'ok': True, 'kept_files': bool(foreign)}


@app.post('/api/projects')
def create_project(body: ProjectInput):
    return store.put('project', {'name': body.name.strip(), 'requirements': body.requirements, 'defaults': store.settings(),
                                'status': '待调研', 'adopted': {}, 'stale_stages': [], 'stage_settings': {}, 'stage_prompts': {}})


@app.get('/api/projects/{project_id}')
def detail(project_id: str):
    project = store.get('project', project_id)
    return {'project': project, **{k + 's': store.listing(k, project_id) for k in
           ('version', 'asset', 'task', 'source', 'message', 'publication')},
           'usage': store.listing('usage', project_id), 'docs': docs.status(project)}


@app.patch('/api/projects/{project_id}')
def update_project(project_id: str, body: dict):
    with jobs.LOCK:
        p = store.get('project', project_id)
        if 'name' in body:
            name = str(body['name']).strip()
            if not name or len(name) > 120:
                raise ValueError('项目名称为空或过长')
            p['name'] = name
            p['name_custom'] = True
        if 'requirements' in body:
            p['requirements'] = body['requirements']
        return store.put('project', p)


@app.post('/api/projects/{project_id}/tasks')
def task(project_id: str, body: TaskInput):
    if body.model:
        if body.kind in ('research', 'script', 'angles') and text_cli.enabled():
            text_cli.validate(body.model)
        else:
            catalog.validate(body.model)
    if body.kind not in ('analyze', 'local_edit', 'research', 'angles', 'script', 'edit', 'preview', 'final', 'cli_video'):
        raise ValueError('任务类型不支持')
    if body.kind in ('preview', 'final') and not body.version_id:
        raise ValueError('请选择剪辑版本')
    if body.intent == 'generate' and any(text in body.prompt for text in ('有什么建议', '先给建议', '先说说建议')):
        body.intent = 'advice'
    if body.intent not in ('generate', 'advice'):
        raise ValueError('任务意图不支持')
    if body.transcribe:
        raise ValueError('当前版本暂停 ASR 和自动字幕')
    for key, value in body.settings.items():
        if key not in store.DEFAULTS:
            raise ValueError('设置项不支持：' + key)
        if key in ('duration', 'days', 'search_limit') and not 1 <= timeline.number(value) <= (10 if key == 'search_limit' else 3600):
            raise ValueError('时长、天数或搜索上限不合法')
        if key == 'aspect' and value not in ('9:16', '16:9', '1:1'):
            raise ValueError('画幅不支持')
    return jobs.submit(project_id, body.kind, body.model_dump())


@app.post('/api/tasks/{task_id}/cancel')
def cancel(task_id: str):
    return jobs.cancel(task_id)


@app.post('/api/tasks/{task_id}/retry')
def retry(task_id: str):
    old = store.get('task', task_id)
    if old['status'] not in ('failed', 'interrupted', 'cancelled'):
        raise ValueError('该任务不需要恢复')
    with jobs.LOCK:
        # A run sent with an approved text is retried with that same text.
        return jobs.submit(old['project_id'], old['kind'], old['payload'], frozen=old,
                           prompt_override=claude_cli.prompt_override(old))


RESENDABLE = ('research', 'script', 'angles', 'chat', 'polish', 'cli_video')


@app.get('/api/tasks/{task_id}/sent')
def sent(task_id: str):
    task = store.get('task', task_id)
    path = claude_cli.sent_path(task)
    if not path.is_file():
        raise ValueError('这次运行没有原文记录：它运行在加上这个功能之前，或者还没有开始发送。')
    record = json.loads(path.read_text(encoding='utf-8'))
    return record | {'task_id': task_id, 'kind': task['kind'], 'status': task['status'],
                     'model': task.get('model_config', {}).get('model'), 'edited': bool(task.get('edited_from')),
                     'resendable': task['kind'] in RESENDABLE and record['engine'] == 'claude_cli' and bool(task.get('project_id'))}


class ResendInput(BaseModel):
    prompt: str = Field(min_length=1, max_length=1_000_000)


@app.post('/api/tasks/{task_id}/resend')
def resend(task_id: str, body: ResendInput):
    """Run the same task again with the creator's edited copy of the exact text sent to Claude."""
    old = store.get('task', task_id)
    if old['kind'] not in RESENDABLE or not old.get('project_id'):
        raise ValueError('这类任务不能修改原文后重新发送')
    if (old.get('model_config') or {}).get('engine') != 'claude_cli':
        raise ValueError('只有通过本机 Claude Code 运行的任务可以修改原文后重新发送')
    if old['status'] in ('queued', 'running', 'cancelling'):
        raise ValueError('这次运行还没有结束，请等它完成或停止后再重新发送')
    if not body.prompt.strip():
        raise ValueError('原文不能为空')
    with jobs.LOCK:
        return jobs.submit(old['project_id'], old['kind'], old['payload'], frozen=old, prompt_override=body.prompt,
                           edited_from=old['id'])


def owned_version(project_id, version_id):
    value = store.get('version', version_id)
    if value['project_id'] != project_id:
        raise ValueError('版本不属于该项目')
    return value


@app.post('/api/projects/{project_id}/versions/{version_id}/adopt')
def adopt(project_id: str, version_id: str, body: dict):
    with jobs.LOCK:
        v = owned_version(project_id, version_id)
        p = store.get('project', project_id)
        stage = v['stage']
        if stage == 'script' and 'paragraphs' not in v['result']:
            raise ValueError('角度版本不作为确认文案，请先生成完整稿')
        if stage == 'research':
            topic = next((t for t in v['result'].get('topics', []) if t['id'] == body.get('topic_id')), None)
            if not topic:
                raise ValueError('请选择一个候选选题')
            p['selected_topic'] = topic
        previous = p['adopted'].get(stage)
        old_topic = p.get('selected_topic_id')
        p['adopted'][stage] = v['id']
        if stage == 'research':
            p['selected_topic_id'] = body['topic_id']
        downstream = {'research': ['script', 'edit'], 'script': ['edit'], 'edit': []}[stage]
        if previous != v['id'] or (stage == 'research' and old_topic != body['topic_id']):
            p['stale_stages'] = sorted(set(p.get('stale_stages', [])) | {s for s in downstream if p['adopted'].get(s)})
        if stage in p['stale_stages']:
            p['stale_stages'].remove(stage)
        if p.get('workspace'):
            p['workspace'][stage + '_version'] = v['id']
            if stage in ('research', 'script') and p['workspace'].get('scene_version'):
                p['stale_stages'] = sorted(set(p['stale_stages']) | {'scenes'})
        # Restoring an old result restores its requirements, but preserves unrelated upstream state.
        p['stage_settings'][stage] = {k: val for k, val in v.get('effective', {}).items() if k != 'prompt'}
        p['stage_prompts'][stage] = v.get('prompt', '')
        p.setdefault('stage_instruction_chain', {})[stage] = [v.get('prompt', '')]
        relevant = {'research': [], 'script': ['research'], 'edit': ['research', 'script']}[stage]
        if any(v.get('upstream', {}).get(s) != p['adopted'].get(s) for s in relevant):
            p['stale_stages'] = sorted(set(p['stale_stages']) | {stage})
        if stage != 'research' and v.get('selected_topic_id') != p.get('selected_topic_id'):
            p['stale_stages'] = sorted(set(p['stale_stages']) | {stage})
        if stage == 'edit' and set(v.get('asset_ids', [])) != {a['id'] for a in store.listing('asset', project_id)}:
            p['stale_stages'] = sorted(set(p['stale_stages']) | {stage})
        p['status'] = {'research': '待文案确认', 'script': '待录制', 'edit': '已导出' if v.get('final') else '待审片'}[stage]
        return store.put('project', p)


@app.post('/api/projects/{project_id}/versions')
def manual_version(project_id: str, body: dict):
    stage = body.get('stage')
    if stage not in ('script', 'edit'):
        raise ValueError('仅支持保存文案或时间线')
    with jobs.LOCK:
        p = store.get('project', project_id)
        parent_id = body.get('parent_id')
        parent = owned_version(project_id, parent_id) if parent_id else None
        if parent and parent['stage'] != stage:
            raise ValueError('父版本属于其他阶段')
        result = body.get('result', {})
        if stage == 'script':
            # Manual editing is an explicit user action: locking state can be changed here.
            from .workspace import ground_script
            result = jobs.protect_script(ground_script(result, store.listing('source', project_id), parent['result'] if parent else None))
        else:
            result['captions'] = []
            result = timeline.validate(result, store.listing('asset', project_id))
        return store.put('version', {'stage': stage, 'parent_id': parent_id, 'result': result, 'prompt': '用户直接编辑',
                         'effective': store.effective(p, stage, p['stage_prompts'].get(stage, '')),
                         'upstream': dict(p['adopted']), 'model_config': {'model': 'manual'},
                         'selected_topic_id': p.get('selected_topic_id'),
                         'asset_ids': [a['id'] for a in store.listing('asset', project_id)],
                         'stale': False, 'subtitles_enabled': False, 'summary': '用户直接编辑并保存'}, project_id)


@app.get('/api/projects/{project_id}/versions/{version_id}/map')
def mapping(project_id: str, version_id: str, second: float):
    v = owned_version(project_id, version_id)
    return timeline.source_at(v['result'], second)


@app.post('/api/projects/{project_id}/assets')
async def upload(project_id: str, file: UploadFile = File(...), kind: str | None = None,
                 media_type: str | None = Query(default=None, alias='type')):
    return await upload_asset(file, project_id, media_type, kind)


@app.post('/api/library/assets')
async def upload_public(file: UploadFile = File(...), media_type: str | None = Query(default=None, alias='type')):
    return await upload_asset(file, None, media_type)


def length(path):
    """Seconds of an uploaded clip for 素材.md; unknown when FFprobe is missing or the file is odd."""
    try:
        return round(media.probe(path, threading.Event())['duration'], 2)
    except Exception:
        return None


async def upload_asset(file, project_id=None, media_type=None, kind=None):
    if project_id:
        store.get('project', project_id)
    if kind not in (None, 'speech', 'music', 'image'):
        raise ValueError('素材类型不支持')
    suffix = Path(file.filename or '').suffix.lower()
    if suffix not in ('.mp4', '.mov', '.mkv', '.webm', '.avi', '.mp3', '.wav', '.m4a', '.flac', '.aac', '.ogg', '.jpg', '.jpeg', '.png', '.webp'):
        raise ValueError('请上传常见视频、音频或图片文件')
    media_type = asset_store.validate_type(media_type or ('Music' if kind == 'music' else 'Image' if kind == 'image'
                                          else asset_store.asset_type({'path': file.filename})), suffix)
    kind = {'Music': 'music', 'Image': 'image'}.get(media_type, 'speech')
    asset_id = store.uid()
    relative = f'assets/{asset_id}{suffix}'
    path = asset_store.file({'project_id': project_id, 'path': relative})
    path.parent.mkdir(parents=True, exist_ok=True)
    digest, total = hashlib.sha256(), 0
    try:
        with path.open('wb') as target:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > 20 * 1024**3:
                    raise ValueError('单个素材最大 20GB')
                target.write(chunk)
                digest.update(chunk)
        if not total:
            raise ValueError('上传文件为空')
        with jobs.LOCK:
            for existing in store.listing('asset'):
                if existing.get('project_id') == project_id and existing.get('sha256') == digest.hexdigest() and asset_store.asset_type(existing) == media_type:
                    path.unlink()
                    return asset_store.describe(existing)
            item = store.put('asset', {'id': asset_id, 'name': Path(file.filename).name, 'path': relative, 'kind': kind,
                                      'type': media_type, 'scope': 'project' if project_id else 'public',
                                      'project_id': project_id, 'storage_project_id': project_id,
                                      'sha256': digest.hexdigest(), 'size': total, 'protected': [], 'provenance': '',
                                      'duration': length(path) if media_type != 'Image' else None}, project_id)
            if project_id:
                p = store.get('project', project_id)
                p['status'] = '待制作' if p.get('workspace', {}).get('intent') in ('assets', 'video') else '素材已保存'
                if p['adopted'].get('edit'):
                    p['stale_stages'] = sorted(set(p['stale_stages']) | {'edit'})
                docs.refresh_materials(p)
                store.put('project', p)
            return item
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


@app.patch('/api/projects/{project_id}/assets/{asset_id}')
def update_asset(project_id: str, asset_id: str, body: dict):
    if store.get('asset', asset_id).get('project_id') != project_id:
        raise ValueError('素材不属于该项目')
    return asset_store.update(asset_id, body)


@app.patch('/api/assets/{asset_id}')
def manage_asset(asset_id: str, body: dict):
    return asset_store.update(asset_id, body)


@app.get('/api/assets/{asset_id}/file')
def asset_file(asset_id: str):
    return FileResponse(asset_store.file(store.get('asset', asset_id)))


@app.get('/api/assets/{asset_id}/frames/{index}')
def asset_frame(asset_id: str, index: int):
    asset = store.get('asset', asset_id)
    frames = asset.get('analysis', {}).get('frames', [])
    if not 0 <= index < len(frames):
        raise ValueError('关键帧不存在')
    return FileResponse(asset_store.file(asset, frames[index]['path']))


def add_source(project_id, row):
    url = str(row.get('url', '')).strip()
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('来源链接必须是无凭据的 HTTP/HTTPS URL')
    # Imports are user-provided evidence, never a claim of automatic verification.
    known = next((s for s in store.listing('source', project_id) if s['url'] == url), None)
    return store.put('source', {'id': known['id'] if known else store.uid(), 'url': url, 'title': str(row.get('title', '')),
                     'platform': str(row.get('platform', '')), 'author': row.get('author') or None,
                     'published_at': row.get('published_at') or None, 'metrics': row.get('metrics', {}),
                     'evidence_note': row.get('evidence_note', ''), 'verification': 'user_supplied', 'collected_at': store.now()}, project_id)


@app.post('/api/projects/{project_id}/sources')
def source(project_id: str, body: dict):
    store.get('project', project_id)
    return add_source(project_id, body)


@app.post('/api/projects/{project_id}/sources/csv')
async def import_sources(project_id: str, file: UploadFile = File(...)):
    store.get('project', project_id)
    data = await file.read(5 * 1024 * 1024 + 1)
    if len(data) > 5 * 1024 * 1024:
        raise ValueError('CSV 不超过 5MB')
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = data.decode('gb18030')
    rows = list(csv.DictReader(io.StringIO(text)))
    if len(rows) > 1000:
        raise ValueError('每次导入不超过 1000 行')
    imported, errors = [], []
    for index, row in enumerate(rows, 2):
        try:
            row['metrics'] = {k: row[k] for k in ('views', 'likes', 'comments', 'followers') if row.get(k)}
            imported.append(add_source(project_id, row))
        except ValueError as exc:
            errors.append({'row': index, 'error': str(exc)})
    return {'imported': len(imported), 'errors': errors}


@app.get('/api/templates')
def templates():
    return store.listing('template')


@app.post('/api/templates')
def save_template(body: dict):
    if body.get('stage') not in ('research', 'script', 'edit') or not str(body.get('name', '')).strip():
        raise ValueError('模板需要名称与正确阶段')
    model = ((text_cli.validate(body['model']) if text_cli.enabled() and body['stage'] in text_cli.STAGES else
              catalog.validate(body['model'])) if body.get('model') else None)
    return store.put('template', {'name': str(body['name'])[:100], 'stage': body['stage'], 'model': model,
                                 'prompt': str(body.get('prompt', ''))[:20000], 'settings': body.get('settings', {})})


@app.post('/api/projects/{project_id}/publications')
def publication(project_id: str, body: dict):
    p = store.get('project', project_id)
    version_id = body.get('version_id') or p['adopted'].get('edit')
    v = owned_version(project_id, version_id)
    if not v.get('final'):
        raise ValueError('请先导出选定成片版本')
    url = str(body.get('url', ''))
    if url and urlparse(url).scheme not in ('https', 'http'):
        raise ValueError('发布链接不合法')
    metrics = body.get('metrics', {})
    for key, value in metrics.items():
        if value is not None and timeline.number(value) < 0:
            raise ValueError('指标不能为负')
    ident = body.get('id')
    if ident:
        record = store.get('publication', ident)
        if record['project_id'] != project_id:
            raise ValueError('记录不属于该项目')
        record['snapshots'].append({'at': store.now(), 'metrics': metrics})
    else:
        record = {'platform': str(body.get('platform', ''))[:50], 'version_id': version_id, 'url': url,
                  'published_at': body.get('published_at'), 'snapshots': [{'at': store.now(), 'metrics': metrics}]}
    record = store.put('publication', record, project_id)
    p['status'] = '已发布'
    store.put('project', p)
    return record


@app.get('/api/projects/{project_id}/export/records')
def export_records(project_id: str):
    store.get('project', project_id)
    return Response(json.dumps(store.listing('publication', project_id), ensure_ascii=False, indent=2), media_type='application/json',
                    headers={'Content-Disposition': 'attachment; filename="publications.json"'})


@app.get('/api/projects/{project_id}/versions/{version_id}/shooting-pack')
def shooting_pack(project_id: str, version_id: str):
    v = owned_version(project_id, version_id)
    if v['stage'] != 'script' or 'paragraphs' not in v['result']:
        raise ValueError('请选择完整文案版本')
    r = v['result']
    text = '# 拍摄包\n\n版本：' + v['id'] + '\n\n## 完整文案\n\n'
    for p in r['paragraphs']:
        text += f"### {p.get('speaker', 'A')} · 约 {p.get('estimated_sec', 0)} 秒\n\n{p['text']}\n\n画面/音乐：{p.get('cue', '')}\n\n"
    for speaker in dict.fromkeys(p.get('speaker', '旁白') for p in r['paragraphs']):
        text += f'## {speaker} 台词\n\n' + '\n\n'.join(p['text'] for p in r['paragraphs'] if p.get('speaker') == speaker) + '\n\n'
    for key, title in [('shot_list', '镜头与录制清单'), ('music_needs', '音乐示例需求（未提供文件不视为已插入）'),
                       ('facts', '事实与来源'), ('publishing', '发布材料')]:
        text += f'## {title}\n\n' + json.dumps(r.get(key, []), ensure_ascii=False, indent=2) + '\n\n'
    return Response(text, media_type='text/markdown', headers={'Content-Disposition': 'attachment; filename="shooting-pack.md"'})


@app.get('/api/projects/{project_id}/files/{relative:path}')
def project_file(project_id: str, relative: str):
    path = store.project_file(project_id, relative)
    if not path.is_file():
        raise HTTPException(404, '产物文件不存在')
    if path.suffix.lower() not in ('.mp4', '.mov', '.mkv', '.webm', '.avi', '.mp3', '.wav', '.m4a', '.flac', '.aac', '.ogg', '.jpg', '.jpeg', '.png', '.webp', '.srt', '.md', '.json', '.zip'):
        raise HTTPException(403, '该文件类型不允许访问')
    return FileResponse(path)


from .workspace import router as workspace_router
app.include_router(workspace_router)
from .inspiration import router as inspiration_router
app.include_router(inspiration_router)
app.mount('/', StaticFiles(directory=store.ROOT / 'web', html=True), name='web')
