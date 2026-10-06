import hashlib
import json
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / '.env')

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import assets as asset_store, claude_cli, config, jobs, media, projects, store


@asynccontextmanager
async def lifespan(app):
    store.init()
    asset_store.migrate()
    projects.migrate()
    jobs.start()
    yield
    jobs.close()


app = FastAPI(title='声间 AI 视频创作空间', lifespan=lifespan)
FRONTEND = ('/', '/index.html', '/app.js', '/workspace.js', '/assets.js', '/settings.js', '/discovery.js',
            '/styles.css', '/v2.css', '/polish.css', '/agent.css')


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
    if request.url.path in FRONTEND:
        response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@app.exception_handler(ValueError)
async def value_error(request, exc):
    return JSONResponse({'detail': str(exc)}, status_code=400)


@app.get('/api/environment')
def env():
    return media.environment() | {'claude_cli': claude_cli.status()}


@app.get('/api/settings')
def settings():
    # start.ps1 reads the capabilities to detect an older server still running.
    return {'tools': config.tools(), 'claude_cli': claude_cli.status(),
            'capabilities': {'prompt_library': True, 'task_snapshots': True}}


@app.put('/api/tools')
def save_tools(body: dict):
    return config.save_tools(body)


@app.get('/api/claude-code')
def cli_status():
    return claude_cli.status()


@app.put('/api/claude-code')
def save_cli(body: dict):
    return claude_cli.save(body)


@app.post('/api/claude-code/check')
def check_cli():
    return claude_cli.inspect_cli()


@app.post('/api/tasks/{task_id}/cancel')
def cancel(task_id: str):
    return jobs.cancel(task_id)


@app.post('/api/tasks/{task_id}/retry')
def retry(task_id: str):
    old = store.get('task', task_id)
    if old['status'] not in ('failed', 'interrupted', 'cancelled'):
        raise ValueError('该任务不需要恢复')
    if not old.get('project_id') or not old.get('payload', {}).get('prompt'):
        raise ValueError('这是旧版工作流的任务，请在对话里重新发送要求')
    return jobs.submit(old['project_id'], old['payload']['prompt'], frozen=old)


@app.post('/api/projects/{project_id}/assets')
async def upload(project_id: str, file: UploadFile = File(...), media_type: str | None = Query(default=None, alias='type')):
    return await upload_asset(file, project_id, media_type)


@app.post('/api/library/assets')
async def upload_public(file: UploadFile = File(...), media_type: str | None = Query(default=None, alias='type')):
    return await upload_asset(file, None, media_type)


async def upload_asset(file, project_id=None, media_type=None):
    if project_id:
        store.get('project', project_id)
    suffix = Path(file.filename or '').suffix.lower()
    if suffix not in asset_store.IMAGE | asset_store.AUDIO | asset_store.VIDEO:
        raise ValueError('请上传常见视频、音频或图片文件')
    media_type = asset_store.validate_type(media_type or asset_store.asset_type({'path': file.filename}), suffix)
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
            return store.put('asset', {'id': asset_id, 'name': Path(file.filename).name, 'path': relative,
                                       'kind': {'Music': 'music', 'Image': 'image'}.get(media_type, 'speech'),
                                       'type': media_type, 'scope': 'project' if project_id else 'public',
                                       'project_id': project_id, 'storage_project_id': project_id,
                                       'sha256': digest.hexdigest(), 'size': total}, project_id)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


@app.patch('/api/assets/{asset_id}')
def manage_asset(asset_id: str, body: dict):
    return asset_store.update(asset_id, body)


@app.get('/api/assets/{asset_id}/file')
def asset_file(asset_id: str):
    return FileResponse(asset_store.file(store.get('asset', asset_id)))


@app.post('/api/projects/{project_id}/import-asset')
def import_asset(project_id: str, body: dict):
    """Copy a public library asset into a project; the library copy stays."""
    import shutil
    with jobs.LOCK:
        store.get('project', project_id)
        original = asset_store.describe(store.get('asset', str(body.get('asset_id'))))
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
        return store.put('asset', a, project_id)


@app.get('/api/library')
def library_overview():
    names = {p['id']: p['name'] for p in store.listing('project')}
    assets = [dict(asset_store.describe(a), project_name=names.get(a.get('project_id'), '公共素材库'))
              for a in store.listing('asset') if not a.get('generated')]
    films = [dict(v, project_name=names.get(v['project_id'], '')) for v in store.listing('version')
             if v['stage'] == 'edit' and (v.get('preview') or v.get('final')) and v['project_id'] in names]
    return {'assets': assets, 'films': films}


def owned_version(project_id, version_id):
    value = store.get('version', version_id)
    if value['project_id'] != project_id:
        raise ValueError('版本不属于该项目')
    return value


@app.post('/api/projects/{project_id}/publications')
def publication(project_id: str, body: dict):
    p = store.get('project', project_id)
    version_id = body.get('version_id') or p.get('adopted', {}).get('edit')
    v = owned_version(project_id, version_id)
    if not v.get('final'):
        raise ValueError('请选择一个已经完成的视频版本')
    url = str(body.get('url', ''))
    if url and urlparse(url).scheme not in ('https', 'http'):
        raise ValueError('发布链接不合法')
    metrics = body.get('metrics', {})
    for value in metrics.values():
        if value is not None and float(value) < 0:
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
    return store.put('publication', record, project_id)


@app.get('/api/projects/{project_id}/export/records')
def export_records(project_id: str):
    store.get('project', project_id)
    return Response(json.dumps(store.listing('publication', project_id), ensure_ascii=False, indent=2), media_type='application/json',
                    headers={'Content-Disposition': 'attachment; filename="publications.json"'})


@app.get('/api/projects/{project_id}/files/{relative:path}')
def project_file(project_id: str, relative: str):
    """Saved video versions; files in the working folder are served by /work/."""
    path = store.project_file(project_id, relative)
    if not path.is_file():
        raise HTTPException(404, '文件不存在')
    if path.suffix.lower() not in asset_store.VIDEO | asset_store.AUDIO | asset_store.IMAGE:
        raise HTTPException(403, '该文件类型不允许访问')
    return FileResponse(path)


from .library import router as library_router
app.include_router(library_router)
app.include_router(projects.router)
from .inspiration import router as inspiration_router
app.include_router(inspiration_router)
app.mount('/', StaticFiles(directory=store.ROOT / 'web', html=True), name='web')
