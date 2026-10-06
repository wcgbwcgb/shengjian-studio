import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.getenv('MEDIA_DATA_DIR', str(ROOT / 'data'))).resolve()


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid.uuid4().hex


@contextmanager
def connection():
    DATA.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATA / 'workbench.sqlite3', timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init():
    with connection() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS objects (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, project_id TEXT,
            body TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS objects_lookup ON objects(kind,project_id);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS task_credentials (id TEXT PRIMARY KEY, body TEXT NOT NULL);
        ''')
    for task in listing('task'):
        if task['status'] in ('running', 'queued', 'cancelling'):
            task.update(status='interrupted', phase='程序关闭，等待恢复', error='上次任务未完成；点击恢复重新执行，已保存的分析缓存保留。')
            put('task', task, task['project_id'])


def put(kind, body, project_id=None):
    item = dict(body)
    project_id = project_id if project_id is not None else item.get('project_id')
    item.setdefault('id', uid())
    item.setdefault('created_at', now())
    item['updated_at'] = now()
    if project_id:
        item['project_id'] = project_id
    with connection() as db:
        db.execute('INSERT INTO objects VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body,project_id=excluded.project_id,updated_at=excluded.updated_at',
                   (item['id'], kind, project_id, json.dumps(item, ensure_ascii=False), item['created_at'], item['updated_at']))
    return item


def delete(kind, ident):
    with connection() as db:
        db.execute('DELETE FROM objects WHERE id=? AND kind=?', (ident, kind))


def preference(key, value=None):
    """Small JSON values kept beside the account defaults, e.g. inspiration feedback."""
    with connection() as db:
        if value is not None:
            db.execute('INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET body=excluded.body',
                       (key, json.dumps(value, ensure_ascii=False)))
            return value
        row = db.execute('SELECT body FROM settings WHERE key=?', (key,)).fetchone()
    return json.loads(row['body']) if row else None


def save_task_credentials(ident, body):
    with connection() as db:
        db.execute('INSERT INTO task_credentials VALUES (?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body',
                   (ident, json.dumps(body)))


def task_credentials(ident):
    with connection() as db:
        row = db.execute('SELECT body FROM task_credentials WHERE id=?', (ident,)).fetchone()
    return json.loads(row['body']) if row else None


def get(kind, ident):
    with connection() as db:
        row = db.execute('SELECT body FROM objects WHERE id=? AND kind=?', (ident, kind)).fetchone()
    if not row:
        raise ValueError('记录不存在')
    return json.loads(row['body'])


def listing(kind, project_id=None):
    with connection() as db:
        sql, args = 'SELECT body FROM objects WHERE kind=?', [kind]
        if project_id is not None:
            sql += ' AND project_id=?'
            args.append(project_id)
        rows = db.execute(sql + ' ORDER BY created_at DESC', args).fetchall()
    return [json.loads(row['body']) for row in rows]


DEFAULTS = {'audience': '对当前话题感兴趣的观众', 'roles': '', 'style': '自然、轻松，少用术语',
            'platform': '抖音 / B站', 'duration': 60, 'aspect': '9:16', 'exclude': '', 'days': 7,
            'search_limit': 10, 'monthly_budget': 40, 'input_price': 4, 'output_price': 20,
            'search_price': 0.01, 'max_tokens': 6000,
            'model_research': 'claude-sonnet-5-5', 'model_script': 'claude-sonnet-5-5', 'model_edit': 'claude-opus-5-5'}


def settings():
    with connection() as db:
        row = db.execute("SELECT body FROM settings WHERE key='defaults'").fetchone()
    return DEFAULTS | (json.loads(row['body']) if row else {})


def save_settings(value):
    with connection() as db:
        db.execute("INSERT INTO settings VALUES ('defaults',?) ON CONFLICT(key) DO UPDATE SET body=excluded.body",
                   (json.dumps(value, ensure_ascii=False),))


def project_dir(project_id):
    get('project', project_id)
    path = DATA / 'projects' / project_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def project_file(project_id, relative):
    root = project_dir(project_id).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError('文件路径超出项目目录')
    return path


def effective(project, stage, prompt, overrides=None):
    result = dict(project['defaults']) | project.get('requirements', {}) | project.get('stage_settings', {}).get(stage, {}) | (overrides or {})
    # These explicit common instructions update the visible structured settings too.
    if '横屏' in prompt or '16:9' in prompt:
        result['aspect'] = '16:9'
    elif '竖屏' in prompt or '9:16' in prompt:
        result['aspect'] = '9:16'
    elif '1:1' in prompt or '正方形' in prompt:
        result['aspect'] = '1:1'
    import re
    duration = re.search(r'(\d+)\s*秒', prompt)
    days = re.search(r'(?:最近|近)\s*(\d+)\s*天', prompt)
    if duration:
        result['duration'] = int(duration[1])
    if days:
        result['days'] = int(days[1])
    result['prompt'] = prompt
    return result
