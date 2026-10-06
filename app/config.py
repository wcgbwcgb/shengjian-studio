"""Machine configuration: local tool paths and Claude Code settings."""
import json
import os
import threading

from . import store

LOCK = threading.RLock()


def _path():
    return store.DATA / 'machine-config.json'


def read():
    with LOCK:
        path = _path()
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def write(value):
    with LOCK:
        store.DATA.mkdir(parents=True, exist_ok=True)
        path = _path()
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        if os.name != 'nt':
            temporary.chmod(0o600)
        temporary.replace(path)


def tools():
    value = read()
    return {'ffmpeg_path': value.get('ffmpeg_path', os.getenv('FFMPEG_PATH', '')),
            'ffprobe_path': value.get('ffprobe_path', os.getenv('FFPROBE_PATH', ''))}


def save_tools(body):
    with LOCK:
        value = read()
        for name in ('ffmpeg_path', 'ffprobe_path'):
            if name in body:
                value[name] = str(body[name]).strip().strip('"')[:2000]
        write(value)
        return tools()
