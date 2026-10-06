"""Machine configuration; credentials never enter project/version payloads."""
import base64
import ctypes
import json
import os
import threading
from ctypes import wintypes
from pathlib import Path
from urllib.parse import urlparse

from . import store


LOCK = threading.RLock()
DEFAULT_BASE_URL = 'https://api.anthropic.com'
CONNECTION_FIELDS = ('base_url', 'protocol', 'credential_override', 'api_key_local', 'api_key_encrypted', 'last_test', 'model_defaults', 'pricing')


def _profile_view(profile, active_id):
    from . import catalog
    owner = 'openai' if profile.get('protocol') == 'openai' else 'claude'
    return {'id': profile['id'], 'name': profile['name'], 'base_url': profile.get('base_url', DEFAULT_BASE_URL),
            'provider': owner, 'models': catalog.compatible(owner), 'model_defaults': profile.get('model_defaults', {}),
            'pricing': profile.get('pricing', {}),
            'protocol': profile.get('protocol', 'anthropic'), 'active': profile['id'] == active_id,
            'api_configured': bool(profile.get('api_key_encrypted') or profile.get('api_key_local')),
            'last_test': profile.get('last_test')}


def _archive_connection(value):
    """Preserve the previous local connection before introducing another one."""
    profiles = value.setdefault('connections', [])
    if not value.get('active_connection_id') and (value.get('api_key_encrypted') or value.get('api_key_local')):
        profile = {key: value[key] for key in CONNECTION_FIELDS if key in value}
        profile.update(id=store.uid(), name='原有连接')
        profiles.append(profile)
        value['active_connection_id'] = profile['id']


def _sync_active(value):
    for profile in value.get('connections', []):
        if profile['id'] == value.get('active_connection_id'):
            for key in CONNECTION_FIELDS:
                profile.pop(key, None)
                if key in value:
                    profile[key] = value[key]


def save_profile(body):
    with LOCK:
        value = read()
        _archive_connection(value)
        profiles = value.setdefault('connections', [])
        ident = body.get('id') or store.uid()
        old = next((p for p in profiles if p['id'] == ident), None)
        if body.get('id') and old is None:
            raise ValueError('连接不存在')
        name = str(body.get('name', old.get('name', '') if old else '')).strip()
        if not name or len(name) > 80:
            raise ValueError('请输入 1–80 字的连接名称')
        protocol = body.get('protocol', old.get('protocol', 'anthropic') if old else 'anthropic')
        if protocol not in ('anthropic', 'openai'):
            raise ValueError('请选择支持的接口类型')
        profile = dict(old or {})
        profile.update(id=ident, name=name, protocol=protocol,
                       base_url=validate_base_url(body.get('base_url', profile.get('base_url', DEFAULT_BASE_URL))))
        from . import catalog
        owner = 'openai' if protocol == 'openai' else 'claude'
        defaults = body.get('model_defaults', profile.get('model_defaults', {}))
        if old and old.get('protocol') != protocol and 'model_defaults' not in body:
            defaults = {}
        profile['model_defaults'] = {stage: catalog.validate(defaults.get(stage) or (
            'gpt-6-luna' if owner == 'openai' else catalog.STAGE_DEFAULTS[stage]), owner) for stage in catalog.STAGE_DEFAULTS}
        prices = body.get('pricing', profile.get('pricing', {}))
        if old and old.get('protocol') != protocol and 'pricing' not in body:
            prices = {}
        profile['pricing'] = validate_pricing(prices, owner)
        supplied = str(body.get('api_key') or '').strip()
        if supplied:
            profile.pop('api_key_encrypted', None)
            profile.pop('api_key_local', None)
            profile.update(_encode_key(supplied))
        if not profile.get('api_key_encrypted') and not profile.get('api_key_local'):
            raise ValueError('新连接需要填写 API Key')
        if supplied or (old and any(old.get(k) != profile.get(k) for k in ('protocol', 'base_url'))):
            profile.pop('last_test', None)
        if old:
            profiles[profiles.index(old)] = profile
        else:
            profiles.append(profile)
        if not value.get('active_connection_id') or value.get('active_connection_id') == ident:
            _apply_profile(value, profile)
        write(value)
        return public()


def _apply_profile(value, profile):
    for key in CONNECTION_FIELDS:
        value.pop(key, None)
        if key in profile:
            value[key] = profile[key]
    value['credential_override'] = True
    value['active_connection_id'] = profile['id']


def activate_profile(ident):
    with LOCK:
        value = read()
        profile = next((p for p in value.get('connections', []) if p['id'] == ident), None)
        if profile is None:
            raise ValueError('连接不存在')
        _apply_profile(value, profile)
        write(value)
        return public()


def delete_profile(ident):
    with LOCK:
        value = read()
        profiles = value.get('connections', [])
        if not any(p['id'] == ident for p in profiles):
            raise ValueError('连接不存在')
        value['connections'] = [p for p in profiles if p['id'] != ident]
        if value.get('active_connection_id') == ident:
            for key in CONNECTION_FIELDS:
                value.pop(key, None)
            value.pop('active_connection_id', None)
            value['credential_override'] = True
        write(value)
        return public()


def _encode_key(supplied):
    if len(supplied) > 4096 or any(c.isspace() for c in supplied):
        raise ValueError('密钥格式不正确，请粘贴完整密钥，不要包含空白字符')
    if os.name == 'nt':
        try:
            return {'credential_override': True, 'api_key_encrypted': base64.b64encode(_dpapi(supplied.encode('utf-8'))).decode('ascii')}
        except ValueError:
            pass
    return {'credential_override': True, 'api_key_local': supplied}


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


class _Blob(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(data, decrypt=False):
    """Windows user-scoped encryption, without additional package dependencies."""
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, target = _Blob(len(data), buffer), _Blob()
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ValueError('本机密钥加密/读取失败，请在设置中重新输入密钥')
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        kernel.LocalFree(ctypes.cast(target.pbData, ctypes.c_void_p))


def api_key():
    value = read()
    if value.get('credential_override'):
        encrypted = value.get('api_key_encrypted')
        if encrypted:
            if os.name != 'nt':
                raise ValueError('此密钥由 Windows 账号保护，请在当前电脑设置中重新输入')
            return _dpapi(base64.b64decode(encrypted), decrypt=True).decode('utf-8')
        return value.get('api_key_local', '')
    return os.getenv('ANTHROPIC_API_KEY', '').strip()


def has_api_key():
    value = read()
    if value.get('credential_override'):
        return bool(value.get('api_key_encrypted') or value.get('api_key_local'))
    return bool(os.getenv('ANTHROPIC_API_KEY', '').strip())


def base_url():
    return read().get('base_url', DEFAULT_BASE_URL)


def public():
    value = read()
    return {'api_configured': has_api_key(), 'base_url': value.get('base_url', DEFAULT_BASE_URL),
            'protocol': value.get('protocol', 'anthropic'),
            'provider': provider(), 'model_defaults': value.get('model_defaults', {}), 'pricing': value.get('pricing', {}),
            'active_connection_id': value.get('active_connection_id'),
            'connections': [_profile_view(p, value.get('active_connection_id')) for p in value.get('connections', [])],
            'ffmpeg_path': value.get('ffmpeg_path', os.getenv('FFMPEG_PATH', '')),
            'ffprobe_path': value.get('ffprobe_path', os.getenv('FFPROBE_PATH', '')),
            'key_storage': 'windows_account' if value.get('api_key_encrypted') else 'local_file',
            'last_test': value.get('last_test')}


def validate_base_url(url):
    url = str(url).strip().rstrip('/') or DEFAULT_BASE_URL
    parsed = urlparse(url)
    if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
            or (parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ('localhost', '127.0.0.1', '::1')))):
        raise ValueError('API 地址应使用 HTTPS，本机代理可使用 http://127.0.0.1；地址不能含密钥、查询或片段')
    # The UI accepts either the service root or an Anthropic /v1 base URL.
    if parsed.path.endswith(('/messages', '/responses', '/chat/completions')):
        raise ValueError('请填写 API 基础地址，不要填写完整请求地址')
    return url


def save(body):
    with LOCK:
        value = read()
        changed = False
        if 'protocol' in body:
            if body['protocol'] not in ('anthropic', 'openai'):
                raise ValueError('请选择支持的接口类型')
            changed = changed or body['protocol'] != value.get('protocol', 'anthropic')
            value['protocol'] = body['protocol']
            if changed:
                value.pop('model_defaults', None)
                value.pop('pricing', None)
        if 'model_defaults' in body:
            from . import catalog
            owner = 'openai' if value.get('protocol') == 'openai' else 'claude'
            value['model_defaults'] = {stage: catalog.validate(model, owner) for stage, model in body['model_defaults'].items()
                                       if stage in catalog.STAGE_DEFAULTS}
        if 'pricing' in body:
            value['pricing'] = validate_pricing(body['pricing'], 'openai' if value.get('protocol') == 'openai' else 'claude')
        if 'base_url' in body:
            url = validate_base_url(body['base_url'])
            changed = changed or url != value.get('base_url', DEFAULT_BASE_URL)
            value['base_url'] = url
        supplied = str(body.get('api_key') or '').strip()
        if supplied:
            if len(supplied) > 4096 or any(c.isspace() for c in supplied):
                raise ValueError('密钥格式不正确，请粘贴完整密钥，不要包含空白字符')
            value['credential_override'] = True
            value.pop('api_key_local', None)
            value.pop('api_key_encrypted', None)
            if os.name == 'nt':
                try:
                    value['api_key_encrypted'] = base64.b64encode(_dpapi(supplied.encode('utf-8'))).decode('ascii')
                except ValueError:
                    # Portable/sandbox Windows accounts can lack a DPAPI profile.
                    # Match the existing local .env storage rather than disable setup.
                    value['api_key_local'] = supplied
            else:
                value['api_key_local'] = supplied
            changed = True
        elif body.get('clear_key'):
            value['credential_override'] = True
            value.pop('api_key_encrypted', None)
            value.pop('api_key_local', None)
            changed = True
        for name in ('ffmpeg_path', 'ffprobe_path'):
            if name in body:
                value[name] = str(body[name]).strip().strip('"')[:2000]
        if changed:
            value.pop('last_test', None)
        _sync_active(value)
        write(value)
        return public()


def record_test(model, tested_key=None, tested_url=None):
    with LOCK:
        if tested_key is not None and (api_key() != tested_key or messages_url() != tested_url):
            return False
        value = read()
        value['last_test'] = {'model': model, 'at': store.now(), 'status': 'success'}
        _sync_active(value)
        write(value)
        return True


def messages_url():
    root = base_url()
    if read().get('protocol') == 'openai':
        return root + ('/responses' if root.endswith('/v1') else '/v1/responses')
    return root + ('/messages' if root.endswith('/v1') else '/v1/messages')


def credentials():
    with LOCK:
        return api_key(), messages_url()


def provider():
    return 'openai' if read().get('protocol') == 'openai' else 'claude'


def validate_pricing(prices, owner):
    from . import catalog, timeline
    if not isinstance(prices, dict):
        raise ValueError('计费配置应为对象')
    result = {}
    for model, fields in prices.items():
        catalog.validate(model, owner)
        if not isinstance(fields, dict) or set(fields) - set(catalog.pricing(model)):
            raise ValueError('计费字段不支持')
        result[model] = {}
        for name, supplied in fields.items():
            value = timeline.number(supplied)
            if value < 0:
                raise ValueError('计费单价不能为负')
            result[model][name] = value
    return result


def freeze(model):
    from . import catalog
    with LOCK:
        value = read()
        owner = provider()
        catalog.validate(model, owner)
        key, url = credentials()
        settings = store.settings()
        snapshot = {'provider': owner, 'protocol': value.get('protocol', 'anthropic'),
                    'connection_id': value.get('active_connection_id'), 'base_url': base_url(), 'url': url,
                    'model': model, 'pricing': catalog.pricing(model) | value.get('pricing', {}).get(model, {}),
                    'max_tokens': int(settings['max_tokens']), 'monthly_budget': float(settings['monthly_budget'])}
        return snapshot, _encode_key(key) if key else {}


def frozen_credentials(task):
    value = store.task_credentials(task['id'])
    if value is None:
        # Legacy interrupted tasks predating configuration snapshots.
        return credentials()
    encrypted = value.get('api_key_encrypted')
    key = _dpapi(base64.b64decode(encrypted), decrypt=True).decode('utf-8') if encrypted else value.get('api_key_local', '')
    return key, task['model_config']['url']


def redact(text, key=None):
    if key is None:
        try:
            key = api_key()
        except Exception:
            return '本机密钥读取失败，请在设置中重新输入密钥'
    return str(text).replace(key, '[已隐藏]') if key else str(text)
