"""Prompts: each feature is a plain file the user edits.

Prompts fill the composer verbatim, so the user sees everything that is sent.
"""
import hashlib
import re
import shutil
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, Field

from . import store

router = APIRouter(prefix='/api')
DEFAULTS = Path(__file__).with_name('defaults') / 'prompts'
SCOPES = ('home', 'project', 'research', 'script', 'inspiration')
IDENT = re.compile(r'[a-z0-9][a-z0-9-]{0,63}')
REMOVED = 'removed_default_prompts'
SEEDED = 'shipped_prompt_versions'


def split(text):
    """A minimal frontmatter reader: `key: value` lines between --- fences."""
    text = text.replace('\r\n', '\n')
    meta, body = {}, text
    if text.startswith('---\n'):
        end = text.find('\n---', 3)
        if end != -1:
            for line in text[4:end].splitlines():
                key, sep, value = line.partition(':')
                if sep:
                    meta[key.strip()] = value.strip()
            body = text[end + 4:].lstrip('\n')
    return meta, body


def compose(meta, body):
    return '---\n' + ''.join(f'{k}: {v}\n' for k, v in meta.items()) + '---\n' + body.strip() + '\n'


def check(ident):
    if not IDENT.fullmatch(ident or ''):
        raise ValueError('名称只能使用小写字母、数字和连字符')
    return ident


def one_line(value, label, limit, required=True):
    value = str(value or '').strip()
    if '\n' in value or '\r' in value or len(value) > limit or (required and not value):
        raise ValueError(f'{label}应为 1–{limit} 字的一行文字' if required else f'{label}不能超过 {limit} 字，且只有一行')
    return value


def removed():
    """Shipped defaults the user deleted; they are not copied back."""
    return store.preference(REMOVED) or []


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def folder():
    """The user's prompts, topped up with shipped defaults the user has not deleted.

    A copy the user never edited follows newer shipped versions; an edited copy is kept.
    """
    target = store.DATA / 'prompts'
    target.mkdir(parents=True, exist_ok=True)
    skip, seeded = set(removed()), store.preference(SEEDED) or {}
    before = dict(seeded)
    for source in DEFAULTS.glob('*.md'):
        ident, destination = source.stem, target / source.name
        if ident in skip:
            continue
        shipped = digest(source)
        if not destination.exists() or (seeded.get(ident) == digest(destination) != shipped):
            shutil.copyfile(source, destination)
            seeded[ident] = shipped
        elif ident not in seeded and digest(destination) == shipped:
            seeded[ident] = shipped
    if seeded != before:
        store.preference(SEEDED, seeded)
    return target


def forget_removal(ident):
    value = removed()
    if ident in value:
        value.remove(ident)
        store.preference(REMOVED, value)


def prompt_view(path):
    meta, body = split(path.read_text(encoding='utf-8'))
    scope = [s.strip() for s in meta.get('scope', 'project').split(',') if s.strip() in SCOPES]
    return {'id': path.stem, 'label': meta.get('label') or path.stem, 'scope': scope,
            'description': meta.get('description', ''), 'body': body.strip(),
            'default': (DEFAULTS / path.name).is_file()}


def prompts():
    return sorted((prompt_view(p) for p in folder().glob('*.md')), key=lambda p: p['id'])


def prompt(ident):
    path = folder() / (check(ident) + '.md')
    if not path.is_file():
        raise ValueError('提示词不存在：' + ident)
    return prompt_view(path)


class PromptInput(BaseModel):
    label: str
    scope: list[str] = Field(default_factory=lambda: ['project'])
    description: str = ''
    body: str = Field(max_length=20000)


def write_prompt(ident, body: PromptInput):
    if not body.body.strip():
        raise ValueError('提示词内容不能为空')
    if not body.scope or set(body.scope) - set(SCOPES):
        raise ValueError('请选择提示词出现的位置')
    meta = {'label': one_line(body.label, '名称', 40), 'scope': ', '.join(s for s in SCOPES if s in body.scope),
            'description': one_line(body.description, '说明', 200, required=False)}
    (folder() / (check(ident) + '.md')).write_text(compose(meta, body.body), encoding='utf-8')
    forget_removal(ident)
    return prompt(ident)


@router.get('/prompts')
def list_prompts():
    return prompts()


@router.post('/prompts')
def create_prompt(body: PromptInput):
    return write_prompt('p-' + store.uid()[:8], body)


@router.put('/prompts/{ident}')
def save_prompt(ident: str, body: PromptInput):
    return write_prompt(ident, body)


@router.delete('/prompts/{ident}')
def delete_prompt(ident: str):
    path = folder() / (check(ident) + '.md')
    if not path.is_file():
        raise ValueError('提示词不存在：' + ident)
    path.unlink()
    if (DEFAULTS / path.name).is_file():
        store.preference(REMOVED, sorted(set(removed()) | {ident}))
    return {'ok': True}


@router.post('/prompts/{ident}/reset')
def reset_prompt(ident: str):
    source = DEFAULTS / (check(ident) + '.md')
    if not source.is_file():
        raise ValueError('这个提示词没有默认版本')
    forget_removal(ident)
    shutil.copyfile(source, folder() / source.name)
    store.preference(SEEDED, (store.preference(SEEDED) or {}) | {ident: digest(source)})
    return prompt(ident)
