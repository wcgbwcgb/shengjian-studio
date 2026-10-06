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
        ''')
    for task in listing('task'):
        if task['status'] in ('running', 'queued', 'cancelling'):
            task.update(status='interrupted', phase='程序关闭，等待恢复', error='上次任务未完成；点击恢复重新执行，工作文件夹里的文件都还在。')
            put('task', task, task['project_id'])


def put(kind, body, project_id=None, touch=True):
    item = dict(body)
    project_id = project_id if project_id is not None else item.get('project_id')
    item.setdefault('id', uid())
    item.setdefault('created_at', now())
    if touch or 'updated_at' not in item:
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
    """Small JSON values, e.g. inspiration feedback or removed library defaults."""
    with connection() as db:
        if value is not None:
            db.execute('INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET body=excluded.body',
                       (key, json.dumps(value, ensure_ascii=False)))
            return value
        row = db.execute('SELECT body FROM settings WHERE key=?', (key,)).fetchone()
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
