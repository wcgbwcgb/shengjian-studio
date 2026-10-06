"""Inspiration batches: a library prompt runs in a shared folder and Claude writes ideas.json.

The creator's own signals (dismissed, saved, feedback, already shown) are written as files
in 偏好/ before each run; the prompt tells Claude to read them.
"""
import copy
import json
import threading

from fastapi import APIRouter
from pydantic import BaseModel, Field

from . import claude_cli, library, media, store

router = APIRouter(prefix='/api/inspirations')
LOCK = threading.RLock()
EVENTS = {}
ACTIVE = ('queued', 'running', 'cancelling')
PREFS = 'inspiration_prefs'
PROMPTS = {False: 'inspire', True: 'inspire-web'}
BATCH_SIZE = 12


def folder():
    return store.DATA / 'inspiration' / claude_cli.WORKDIR


def prefs():
    return {'dislikes': [], 'feedback': []} | (store.preference(PREFS) or {})


def latest_job():
    jobs = [t for t in store.listing('task') if t.get('kind') == 'inspire']
    return jobs[0] if jobs else None


def favorites():
    return store.listing('inspiration')


def ideas(status='active'):
    return list(reversed([i for i in store.listing('idea') if i.get('status') == status]))


def card(idea, saved):
    return dict(idea, kind='idea', saved=idea['id'] in saved, favorite_id=saved.get(idea['id']))


def cli_ready():
    try:
        return bool(claude_cli.cli_command())
    except ValueError:
        return False


@router.get('')
def browse():
    saved = {f['origin_id']: f['id'] for f in favorites()}
    job = latest_job()
    return {'ideas': [card(i, saved) for i in ideas()],
            'favorites': [dict(f['item'], id=f['origin_id'], saved=True, favorite_id=f['id']) for f in favorites()],
            'previous': len(ideas('previous')), 'configured': cli_ready(),
            'job': job and {k: job.get(k) for k in ('id', 'status', 'phase', 'error', 'created_at', 'started_at', 'payload', 'output')}}


class Generate(BaseModel):
    web: bool = False
    direction: str = Field(default='', max_length=500)
    feedback: str = Field(default='', max_length=500)


@router.post('/generate')
def generate(body: Generate):
    with LOCK:
        job = latest_job()
        if job and job['status'] in ACTIVE:
            raise ValueError('上一批灵感还在生成，请稍候')
        if not cli_ready():
            raise ValueError('请先在设置中连接本机 Claude Code')
        prompt = library.prompt(PROMPTS[body.web])['body']
        if body.direction.strip():
            prompt += '\n\n想探索的方向：' + body.direction.strip()
        if body.feedback.strip():
            prompt += '\n\n我对上一批的意见：' + body.feedback.strip()
        settings = claude_cli.options()
        task = store.put('task', {'kind': 'inspire', 'project_id': None, 'status': 'running', 'phase': '正在准备',
                                  'payload': body.model_dump() | {'prompt': prompt}, 'cli_config': settings,
                                  'model_config': {'engine': 'claude_cli', 'model': settings['model']},
                                  'logs': [], 'error': None, 'started_at': store.now()})
        EVENTS[task['id']] = threading.Event()
        threading.Thread(target=run, args=(task,), daemon=True, name='inspiration').start()
        return task


def update(task, **values):
    with LOCK:
        current = store.get('task', task['id'])
        current.update(values)
        store.put('task', current)
        return current


def write_preferences(work, preference):
    lists = {'已出现.md': [i['title'] for i in ideas() + ideas('previous')],
             '不感兴趣.md': preference['dislikes'][-60:], '反馈.md': preference['feedback'][-8:],
             '收藏.md': [f['item'].get('title', '') for f in favorites()][:15]}
    target = work / '偏好'
    target.mkdir(parents=True, exist_ok=True)
    for name, items in lists.items():
        text = '\n'.join('- ' + str(item).replace('\n', ' ') for item in items if str(item).strip())
        (target / name).write_text(text + '\n' if text else '（暂无）\n', encoding='utf-8')


def read_ideas(path):
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return None
    items = data.get('ideas') if isinstance(data, dict) else data
    return items if isinstance(items, list) else None


def run(task):
    event = EVENTS[task['id']]
    report = lambda text: update(task, phase=text, logs=(store.get('task', task['id']).get('logs', []) + [{'at': store.now(), 'text': text}])[-50:])
    payload, preference, work = task['payload'], prefs(), folder()
    try:
        work.mkdir(parents=True, exist_ok=True)
        write_preferences(work, preference)
        output = work / 'ideas.json'
        output.unlink(missing_ok=True)
        result = claude_cli.run(task, work, payload['prompt'], event, report)
        items = read_ideas(output)
        if items is None:
            if result['failure']:
                raise result['failure']
            raise ValueError('Claude 没有写出 ideas.json' + ('。Claude 的回复：' + result['reply'][:800] if result['reply'] else ''))
        read = set(result['web'])
        batch = []
        for item in items:
            if not isinstance(item, dict) or not str(item.get('title') or '').strip():
                continue
            sources = []
            for source in (item.get('sources') or [])[:5]:
                if isinstance(source, dict) and str(source.get('url', '')).startswith('http'):
                    # Links Claude cites but never opened during this run are labelled as such.
                    sources.append({'url': source['url'], 'title': str(source.get('title') or source['url'])[:200],
                                    'verification': 'search_only' if source['url'] in read else 'unverified'})
            batch.append({'title': str(item['title']).strip()[:200], 'description': str(item.get('description') or '').strip()[:600],
                          'hook': str(item.get('hook') or '').strip()[:300], 'format': str(item.get('format') or '').strip()[:100],
                          'tags': [str(t)[:20] for t in (item.get('tags') or [])][:3], 'sources': sources,
                          'web': payload['web'], 'batch_id': task['id']})
        if not batch:
            raise ValueError('ideas.json 里没有可用的选题，请换个方向再试')
        with LOCK:
            # Unsaved cards from the batch before last are discarded; the current batch stays undoable once.
            for old in ideas('previous'):
                store.delete('idea', old['id'])
            for old in ideas():
                store.put('idea', dict(old, status='previous'))
            for item in batch[:BATCH_SIZE]:
                store.put('idea', dict(item, status='active'))
            if payload['feedback'].strip():
                preference['feedback'] = (preference['feedback'] + [payload['feedback'].strip()])[-20:]
                store.preference(PREFS, preference)
            update(task, status='completed', phase='已完成', finished_at=store.now(),
                   output={'reply': result['reply'][:3000]})
    except media.Cancelled:
        update(task, status='cancelled', phase='已取消')
    except Exception as exc:
        update(task, status='failed', phase='未完成', error=claude_cli.clean(exc)[:2000])
    finally:
        EVENTS.pop(task['id'], None)


@router.post('/cancel')
def cancel():
    job = latest_job()
    if job and job['status'] in ACTIVE:
        event = EVENTS.get(job['id'])
        if event:
            event.set()
            return update(job, status='cancelling', phase='正在停止')
        return update(job, status='cancelled', phase='已取消')
    return job


@router.post('/undo')
def undo():
    with LOCK:
        previous = ideas('previous')
        if not previous:
            raise ValueError('没有可以恢复的上一批灵感')
        # Swapping lets the creator switch back again if the older batch was not better.
        for item in ideas():
            store.put('idea', dict(item, status='previous'))
        for item in previous:
            store.put('idea', dict(item, status='active'))
        return browse()


@router.post('/{ident}/dismiss')
def dismiss(ident: str):
    with LOCK:
        item = store.get('idea', ident)
        preference = prefs()
        preference['dislikes'] = (preference['dislikes'] + [item['title']])[-200:]
        store.preference(PREFS, preference)
        store.delete('idea', ident)
        return {'ok': True}


def lookup(ident):
    item = next((dict(i, kind='idea') for i in ideas() + ideas('previous') if i['id'] == ident), None)
    saved = next((f for f in favorites() if f['origin_id'] == ident), None)
    return item or (copy.deepcopy(saved['item']) if saved else None)


@router.post('/favorites')
def favorite(body: dict):
    item = lookup(body.get('id'))
    if not item:
        raise ValueError('灵感不存在')
    existing = next((s for s in favorites() if s['origin_id'] == body['id']), None)
    return existing or store.put('inspiration', {'origin_id': body['id'], 'item': item})


@router.delete('/favorites/{ident}')
def unfavorite(ident: str):
    store.get('inspiration', ident)
    store.delete('inspiration', ident)
    return {'ok': True}


@router.post('/create')
def from_inspiration(body: dict):
    from .projects import new_project
    item = lookup(body.get('id'))
    if not item:
        raise ValueError('灵感不存在')
    # Favourites saved by earlier versions hold a research topic instead of a title.
    title = item.get('title') or item.get('topic', {}).get('title') or '新作品'
    idea = title + ('：' + item['description'] if item.get('description') else '') + \
        ('\n开场：' + item['hook'] if item.get('hook') else '')
    return {'project': new_project(title, idea)}
