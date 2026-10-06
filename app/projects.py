"""A project is a working folder plus the conversation of Claude Code runs in it."""
import json
import mimetypes
import shutil
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import claude_cli, jobs, store

router = APIRouter(prefix='/api')
TEXT = {'.md', '.txt', '.json', '.csv', '.srt', '.vtt', '.yaml', '.yml', '.html', '.htm', '.css', '.js', '.jsx', '.ts',
        '.tsx', '.py', '.xml', '.svg', '.log'}
EDITABLE = {'.md', '.txt', '.json', '.csv', '.srt', '.vtt', '.yaml', '.yml'}
KINDS = {'video': claude_cli.VIDEO_SUFFIXES, 'audio': {'.mp3', '.wav', '.m4a', '.flac', '.aac', '.ogg'},
         'image': {'.png', '.jpg', '.jpeg', '.webp', '.gif'}}
# Pages Claude writes may contain scripts; they run in an isolated origin, never the workbench's.
SANDBOXED = {'.html', '.htm', '.svg', '.xml', '.js'}


def work(project_id):
    return store.project_file(project_id, claude_cli.WORKDIR)


def kind_of(path):
    suffix = Path(path).suffix.lower()
    return next((k for k, s in KINDS.items() if suffix in s), 'text' if suffix in TEXT else 'other')


def new_project(name, idea=''):
    return store.put('project', {'name': (name.strip() or '新作品')[:80], 'idea': idea.strip()[:12000],
                                 'adopted': {}, 'migration': MIGRATION})


def files(project_id, limit=500):
    root = work(project_id)
    found = []
    if root.is_dir():
        for path in root.rglob('*'):
            relative = path.relative_to(root)
            if path.is_file() and not set(relative.parts[:-1]) & claude_cli.SKIP_DIRS:
                stat = path.stat()
                found.append({'path': relative.as_posix(), 'size': stat.st_size, 'mtime': stat.st_mtime,
                              'kind': kind_of(path)})
    return sorted(found, key=lambda f: f['mtime'], reverse=True)[:limit]


# The UI understands three files; the prompts that ask for them spell out their format.

def read_json(project_id, name):
    try:
        return json.loads((work(project_id) / name).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return None


def text(value, limit=2000):
    return str(value or '').strip()[:limit] if isinstance(value, (str, int, float)) else ''


def texts(values, limit=40):
    return [t for t in (text(v) for v in values) if t][:limit] if isinstance(values, list) else []


def directions(project_id):
    """directions.json: the direction cards on the 调研 tab."""
    data = read_json(project_id, 'directions.json')
    items = data.get('directions') if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    fields = ('title', 'reason', 'hook', 'audience')
    return [{k: text(item.get(k), 1000) for k in fields}
            for item in items if isinstance(item, dict) and text(item.get('title'))][:30]


def research(project_id):
    """research.json: the 调研 tab. Anything missing or malformed is simply not shown."""
    data = read_json(project_id, 'research.json')
    if not isinstance(data, dict):
        return None
    result = {k: text(data.get(k)) for k in ('title', 'summary', 'what_happened', 'why_now', 'content_gap')}
    result.update({k: texts(data.get(k)) for k in ('viewpoints', 'audience_reactions', 'to_verify')})
    result['facts'] = [{'claim': text(f.get('claim')), 'sources': [u for u in texts(f.get('sources'), 10) if u.startswith('http')]}
                       for f in data.get('facts') or [] if isinstance(f, dict) and text(f.get('claim'))][:40]
    result['sources'] = [{'url': text(s.get('url')), 'title': text(s.get('title'), 300)}
                         for s in data.get('sources') or [] if isinstance(s, dict) and text(s.get('url')).startswith('http')][:60]
    return result


def script(project_id):
    """脚本.md: the 文案 tab."""
    path = work(project_id) / '脚本.md'
    return path.read_text(encoding='utf-8', errors='replace')[:200_000] if path.is_file() else None


def visible_task(task):
    # The material list is internal; the conversation only needs what was asked and answered.
    return {k: v for k, v in task.items() if k not in ('asset_snapshot', 'snapshot', 'source_snapshot', 'effective')}


@router.get('/projects')
def projects():
    latest = {}
    for t in store.listing('task'):
        if t.get('project_id'):
            latest.setdefault(t['project_id'], {k: t.get(k) for k in ('kind', 'status', 'error', 'created_at', 'phase')})
    return sorted([dict(p, last_task=latest.get(p['id'])) for p in store.listing('project')],
                  key=lambda p: p['updated_at'], reverse=True)


class ProjectInput(BaseModel):
    name: str = Field(default='', max_length=120)
    prompt: str = Field(default='', max_length=20000)


@router.post('/projects')
def create(body: ProjectInput):
    """Create a project and, when a prompt is given, send it to Claude straight away."""
    prompt = body.prompt.strip()
    name = body.name.strip() or next((line.strip() for line in prompt.splitlines() if line.strip()), '')
    with jobs.LOCK:
        project = new_project(name, prompt)
        if not prompt:
            return {'project': project, 'task': None, 'needs_cli': False}
        try:
            return {'project': project, 'task': jobs.submit(project['id'], prompt), 'needs_cli': False}
        except ValueError as exc:
            if 'Claude Code' not in str(exc):
                raise
            # The idea is kept; the creator connects Claude Code and sends it again.
            return {'project': project, 'task': None, 'needs_cli': True}


@router.get('/projects/{project_id}')
def detail(project_id: str):
    store.get('project', project_id)
    return {'project': store.get('project', project_id),
            'tasks': [visible_task(t) for t in store.listing('task', project_id)],
            'versions': [v for v in store.listing('version', project_id) if v['stage'] == 'edit'],
            'assets': store.listing('asset', project_id), 'publications': store.listing('publication', project_id),
            'files': files(project_id), 'directions': directions(project_id), 'research': research(project_id),
            'script': script(project_id), 'work_path': str(work(project_id))}


@router.patch('/projects/{project_id}')
def rename(project_id: str, body: dict):
    with jobs.LOCK:
        p = store.get('project', project_id)
        name = str(body.get('name', '')).strip()
        if not name or len(name) > 120:
            raise ValueError('作品名称为空或过长')
        p.update(name=name, name_custom=True)
        return store.put('project', p)


@router.delete('/projects/{project_id}')
def delete(project_id: str):
    with jobs.LOCK:
        store.get('project', project_id)
        if any(t['status'] in jobs.ACTIVE for t in store.listing('task', project_id)):
            raise ValueError('这个作品正在制作中，请先停止当前任务再删除。')
        # Assets moved to the public library or another project keep their original
        # storage folder; that folder must survive while anything still points at it.
        foreign = [a for a in store.listing('asset') if a.get('storage_project_id') == project_id and a.get('project_id') != project_id]
        with store.connection() as db:
            db.execute("DELETE FROM objects WHERE project_id=? AND kind!='usage'", (project_id,))
            db.execute("DELETE FROM objects WHERE id=? AND kind='project'", (project_id,))
        folder = store.DATA / 'projects' / project_id
        if folder.is_dir() and not foreign:
            shutil.rmtree(folder, ignore_errors=True)
        return {'ok': True, 'kept_files': bool(foreign)}


class RunInput(BaseModel):
    prompt: str = Field(max_length=20000)
    fresh: bool = False


@router.post('/projects/{project_id}/run')
def run(project_id: str, body: RunInput):
    return jobs.submit(project_id, body.prompt, body.fresh)


def work_file(project_id, relative):
    root = work(project_id).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError('文件路径超出工作文件夹')
    return path


@router.get('/projects/{project_id}/work/{relative:path}')
def read_file(project_id: str, relative: str):
    path = work_file(project_id, relative)
    if not path.is_file():
        raise ValueError('文件不存在：' + relative)
    suffix = path.suffix.lower()
    media_type = 'text/plain; charset=utf-8' if suffix in TEXT and suffix not in ('.html', '.htm', '.svg') else \
        mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
    headers = {'Content-Security-Policy': 'sandbox allow-scripts'} if suffix in SANDBOXED else {}
    return FileResponse(path, media_type=media_type, headers=headers)


class FileText(BaseModel):
    text: str = Field(max_length=2_000_000)


@router.put('/projects/{project_id}/work/{relative:path}')
def write_file(project_id: str, relative: str, body: FileText):
    """The creator can edit what Claude wrote (需求.md, 脚本.md…); the next prompt reads the edited file."""
    with jobs.LOCK:
        if any(t['status'] in jobs.ACTIVE for t in store.listing('task', project_id)):
            raise ValueError('Claude 正在工作，完成后再修改文件')
        path = work_file(project_id, relative)
        if path.suffix.lower() not in EDITABLE:
            raise ValueError('这种文件不能在这里编辑')
        if set(path.relative_to(work(project_id).resolve()).parts[:-1]) & claude_cli.SKIP_DIRS:
            raise ValueError('这个位置的文件不能编辑')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body.text, encoding='utf-8', newline='')
        return {'ok': True, 'path': relative}


# Projects from the earlier stage-by-stage workflow -----------------------------

def legacy_research(result, topic_id=None):
    topics = result.get('topics') or [{}]
    topic = next((t for t in topics if t.get('id') == topic_id), topics[0])
    facts = [{'claim': f.get('claim', ''), 'sources': f.get('source_urls', [])} if isinstance(f, dict) else {'claim': str(f), 'sources': []}
             for f in topic.get('key_facts') or []]
    return {'title': topic.get('title', ''), 'summary': result.get('summary', ''),
            **{k: topic.get(k, '') for k in ('what_happened', 'why_now', 'content_gap')},
            'facts': facts, 'viewpoints': topic.get('narratives', []), 'audience_reactions': topic.get('audience_reactions', []),
            'to_verify': topic.get('verify', []),
            'sources': [{'url': s.get('url', ''), 'title': s.get('title', '')} for s in result.get('sources') or []]}


def legacy_script(result):
    lines = ['# 脚本', '']
    if result.get('angle'):
        lines += ['方向：' + str(result['angle']), '']
    for i, p in enumerate(result.get('paragraphs', []), 1):
        lines += [f"## {i:02d} · {p.get('speaker') or '旁白'}", '', str(p.get('text', ''))]
        if p.get('cue'):
            lines += ['', '画面：' + str(p['cue'])]
        lines.append('')
    return '\n'.join(lines)


def write_new(path, content):
    if not path.exists():
        path.write_text(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, indent=2), encoding='utf-8')


MIGRATION = 2


def migrate():
    """Turn the research, directions, script and requirements card of old projects into the files the tabs show."""
    for p in store.listing('project'):
        if p.get('migration', 1 if p.get('v3') else 0) >= MIGRATION:
            continue
        try:
            folder = work(p['id'])
            folder.mkdir(parents=True, exist_ok=True)
            versions = store.listing('version', p['id'])
            w = p.get('workspace') or {}
            old_research = next((v for v in versions if v['stage'] == 'research'), None)
            old_script = next((v for v in versions if v['stage'] == 'script' and v['result'].get('paragraphs')), None)
            if old_research:
                write_new(folder / 'research.json', legacy_research(old_research['result'], p.get('selected_topic_id')))
                angles = []
                for topic in old_research['result'].get('topics', []):
                    angles += [a for a in topic.get('angles', []) if isinstance(a, dict)]
                    angles += w.get('extra_angles', {}).get(topic.get('id'), [])
                if angles:
                    write_new(folder / 'directions.json', {'directions': [
                        {k: a.get(k, '') for k in ('title', 'reason', 'hook', 'audience')} for a in angles]})
            if old_script:
                write_new(folder / '脚本.md', legacy_script(old_script['result']))
            card = {k: v for k, v in (w.get('card') or {}).items() if v}
            if card:
                labels = {'audience': '给谁看', 'goal': '想让观众得到什么', 'core_message': '核心观点', 'tone': '语气风格',
                          'format': '形式与时长', 'must_include': '必须包含', 'avoid': '要避免', 'notes': '其他'}
                write_new(folder / '需求.md', '# 需求\n\n' + '\n'.join(f'- {labels.get(k, k)}：{v}' for k, v in card.items()) + '\n')
            p['idea'] = p.get('idea') or w.get('idea') or p['name']
            p['migration'] = MIGRATION
            store.put('project', p, touch=False)
        except Exception:
            continue  # One damaged legacy project must not stop the workbench from starting.
