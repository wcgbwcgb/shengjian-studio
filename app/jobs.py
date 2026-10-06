"""Queue of Claude Code runs. Each run sends one prompt; a project has at most one active run."""
import copy
import queue
import threading
import time

from . import assets as asset_store, claude_cli, media, store

QUEUE = queue.Queue()
EVENTS = {}
LOCK = threading.RLock()
STOP = threading.Event()
THREAD = None
ACTIVE = ('queued', 'running', 'cancelling')


def start():
    global THREAD
    STOP.clear()
    THREAD = threading.Thread(target=worker, daemon=True, name='agent-worker')
    THREAD.start()


def close():
    STOP.set()
    with LOCK:
        for event in list(EVENTS.values()):
            event.set()
    if THREAD:
        THREAD.join(timeout=6)


def last_session(project_id):
    """Runs continue the project's conversation, like follow-up messages in one Claude Code session."""
    for task in store.listing('task', project_id):
        if task.get('cli_session_id') and task.get('cli_workdir') == claude_cli.WORKDIR:
            return task['cli_session_id']
    return None


def submit(project_id, prompt, fresh=False, frozen=None):
    with LOCK:
        project = store.get('project', project_id)
        if any(t['status'] in ACTIVE for t in store.listing('task', project_id)):
            raise ValueError('这个作品正在制作中，请等它完成或先停止')
        prompt = str(prompt or '')
        if not prompt.strip():
            raise ValueError('请写下要交给 Claude 的内容')
        if len(prompt) > 20000:
            raise ValueError('内容太长了，请精简到两万字以内')
        cli_config = copy.deepcopy(frozen['cli_config']) if frozen and frozen.get('cli_config') else claude_cli.options()
        if not claude_cli.cli_command(cli_config):
            raise ValueError('未找到 Claude Code。请在设置中配置并检测本机 CLI。')
        # A retry continues the stopped run's own conversation, so finished steps are not redone.
        resume = (frozen.get('cli_session_id') or frozen.get('resume_session')) if frozen else None if fresh else last_session(project_id)
        task = store.put('task', {
            'kind': 'agent', 'status': 'queued', 'phase': '等待执行', 'payload': {'prompt': prompt, 'fresh': bool(fresh)},
            'cli_config': cli_config, 'model_config': {'engine': 'claude_cli', 'model': cli_config['model']},
            'resume_session': resume, 'logs': [], 'error': None,
            'asset_snapshot': [asset_store.describe(a) for a in reversed(store.listing('asset', project_id))],
            **({'retry_of': frozen['id']} if frozen else {})}, project_id)
        store.put('project', project)  # Most recently worked-on projects come first.
        EVENTS[task['id']] = threading.Event()
        QUEUE.put(task['id'])
        return task


def cancel(task_id):
    with LOCK:
        task = store.get('task', task_id)
        if task['status'] not in ACTIVE:
            return task
        EVENTS.setdefault(task_id, threading.Event()).set()
        task['status'] = 'cancelling'
        return store.put('task', task, task['project_id'])


def phase(task_id, label):
    with LOCK:
        task = store.get('task', task_id)
        task['phase'] = label
        task['logs'] = (task['logs'] + [{'at': store.now(), 'text': label}])[-300:]
        store.put('task', task, task['project_id'])


def worker():
    while not STOP.is_set():
        try:
            task_id = QUEUE.get(timeout=.5)
        except queue.Empty:
            continue
        started = time.monotonic()
        try:
            with LOCK:
                task = store.get('task', task_id)
                event = EVENTS[task_id]
                if event.is_set():
                    raise media.Cancelled()
                task['status'] = 'running'
                task['started_at'] = store.now()
                store.put('task', task, task['project_id'])
            output = claude_cli.execute(task, event, lambda text: phase(task_id, text))
            if event.is_set():
                raise media.Cancelled()
            with LOCK:
                task = store.get('task', task_id)
                task.update(status='completed', phase='已完成', output=output)
        except media.Cancelled:
            task = store.get('task', task_id)
            task.update(status='interrupted' if STOP.is_set() else 'cancelled', phase='已中断' if STOP.is_set() else '已取消')
        except Exception as exc:
            task = store.get('task', task_id)
            task.update(status='failed', phase='失败，可重试', error=claude_cli.clean(exc)[:3000])
        finally:
            with LOCK:
                task['elapsed_sec'] = round(time.monotonic() - started, 2)
                task['finished_at'] = store.now()
                store.put('task', task, task['project_id'])
            QUEUE.task_done()
