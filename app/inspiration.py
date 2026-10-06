"""Inspiration batches: generated on request, replaced by "another batch", one-step undo, saved favourites."""
import copy
import threading
from datetime import date

from fastapi import APIRouter
from pydantic import BaseModel, Field

from . import catalog, claude_cli, config, media, models, store, text_cli
from .workspace import create_from_research, ResearchStart, create, Creation

router = APIRouter(prefix='/api/inspirations')
LOCK = threading.RLock()
EVENTS = {}
ACTIVE = ('queued', 'running', 'cancelling')
PREFS = 'inspiration_prefs'
BATCH_SIZE = 8


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


@router.get('')
def browse():
    saved = {f['origin_id']: f['id'] for f in favorites()}
    job = latest_job()
    return {'ideas': [card(i, saved) for i in ideas()],
            'favorites': [dict(f['item'], id=f['origin_id'], saved=True, favorite_id=f['id']) for f in favorites()],
            'previous': len(ideas('previous')), 'configured': text_cli.available(),
            'job': job and {k: job.get(k) for k in ('id', 'status', 'phase', 'error', 'created_at', 'started_at', 'payload')}}


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
        if not text_cli.available():
            raise ValueError('请先在设置中连接 Claude 订阅或 API 服务')
        task = {'kind': 'inspire', 'project_id': None, 'status': 'running', 'phase': '正在准备',
                'payload': body.model_dump(), 'logs': [], 'error': None, 'started_at': store.now()}
        credential = {}
        if text_cli.enabled():
            model = text_cli.resolve('research')
            task['model_config'] = {'engine': 'claude_cli', 'provider': 'claude', 'model': model, 'billing': 'subscription'}
            task['cli_config'] = dict(claude_cli.options(), model=model)
        else:
            task['model_config'], credential = config.freeze(catalog.resolve('research'))
        task = store.put('task', task)
        if credential:
            store.save_task_credentials(task['id'], credential)
        EVENTS[task['id']] = threading.Event()
        threading.Thread(target=run, args=(task,), daemon=True, name='inspiration').start()
        return task


def update(task, **values):
    with LOCK:
        current = store.get('task', task['id'])
        current.update(values)
        store.put('task', current)
        return current


def evidence_urls(raw):
    found = set()
    for block in raw:
        content = block.get('content')
        if block.get('type') == 'web_search_tool_result' and isinstance(content, list):
            found |= {item.get('url') for item in content if isinstance(item, dict) and item.get('url')}
        if block.get('type') == 'web_fetch_tool_result' and isinstance(content, dict) and content.get('url'):
            found.add(content['url'])
    return found


def run(task):
    event = EVENTS[task['id']]
    report = lambda text: update(task, phase=text, logs=(store.get('task', task['id']).get('logs', []) + [{'at': store.now(), 'text': text}])[-50:])
    payload, settings = task['payload'], store.settings()
    preference = prefs()
    try:
        context = {'web': payload['web'], 'direction': payload['direction'], 'feedback': payload['feedback'],
                   'audience': settings['audience'], 'style': settings['style'], 'platform': settings['platform'],
                   'avoid_titles': preference['dislikes'][-60:] + [i['title'] for i in ideas()] + [i['title'] for i in ideas('previous')],
                   'recent_feedback': preference['feedback'][-8:], 'liked': [f['item'].get('title') for f in favorites()][:15],
                   'count': BATCH_SIZE, 'today': date.today().isoformat(),
                   'effective': {'search_limit': settings['search_limit']}}
        result, raw, _ = models.call(task, 'inspire', context, event, report)
        if event.is_set():
            raise media.Cancelled()
        found = evidence_urls(raw)
        batch = []
        for item in result.get('ideas') or []:
            if not isinstance(item, dict) or not str(item.get('title') or '').strip():
                continue
            sources = []
            for source in (item.get('sources') or [])[:5] if payload['web'] else []:
                if isinstance(source, dict) and str(source.get('url', '')).startswith('http'):
                    sources.append({'url': source['url'], 'title': str(source.get('title') or source['url'])[:200],
                                    'verification': 'search_only' if source['url'] in found else 'unverified'})
            batch.append({'title': str(item['title']).strip()[:200], 'description': str(item.get('description') or '').strip()[:600],
                          'hook': str(item.get('hook') or '').strip()[:300], 'format': str(item.get('format') or '').strip()[:100],
                          'tags': [str(t)[:20] for t in (item.get('tags') or [])][:3], 'sources': sources,
                          'web': payload['web'], 'batch_id': task['id']})
        if not batch:
            raise ValueError('这次没有得到可用的灵感，请换个方向再试')
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
            update(task, status='completed', phase='已完成', finished_at=store.now())
    except media.Cancelled:
        update(task, status='cancelled', phase='已取消')
    except Exception as exc:
        update(task, status='failed', phase='未完成', error=str(exc)[:2000])
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
    item = lookup(body.get('id'))
    if not item:
        raise ValueError('灵感不存在')
    clarify = bool(body.get('clarify', True))
    if item.get('kind') == 'research':
        # Favourites saved before batches existed keep their original research.
        try:
            store.get('version', item['version_id'])
            return create_from_research(ResearchStart(version_id=item['version_id'], topic_id=item['topic']['id']))
        except ValueError:
            pass
    idea = item['title'] + ('：' + item['description'] if item.get('description') else '')
    return create(Creation(idea=idea, intent='research', clarify=clarify))
