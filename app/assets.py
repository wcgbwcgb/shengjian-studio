"""Asset identity, ownership and physical storage are independent of media type."""
from pathlib import Path
from . import store

TYPES = ('Video', 'Image', 'Audio', 'Music')
IMAGE = {'.jpg', '.jpeg', '.png', '.webp'}
AUDIO = {'.mp3', '.wav', '.m4a', '.flac', '.aac', '.ogg'}
VIDEO = {'.mp4', '.mov', '.mkv', '.webm', '.avi'}


def asset_type(asset):
    if asset.get('type') in TYPES:
        return asset['type']
    if asset.get('kind') == 'music':
        return 'Music'
    suffix = Path(asset.get('path', asset.get('name', ''))).suffix.lower()
    return 'Image' if suffix in IMAGE or asset.get('kind') == 'image' else 'Audio' if suffix in AUDIO else 'Video'


def describe(asset):
    result = dict(asset)
    result['type'] = asset_type(result)
    result['scope'] = result.get('scope', 'project' if result.get('project_id') else 'public')
    result.setdefault('storage_project_id', result.get('project_id'))
    result['kind'] = {'Music': 'music', 'Image': 'image'}.get(result['type'], 'speech')
    return result


def validate_type(value, suffix):
    if value not in TYPES:
        raise ValueError('素材类型应为 Video、Image、Audio 或 Music')
    allowed = IMAGE if value == 'Image' else VIDEO if value == 'Video' else AUDIO if value == 'Audio' else AUDIO | VIDEO
    if suffix.lower() not in allowed:
        raise ValueError('素材类型与文件格式不兼容')
    return value


def file(asset, relative=None):
    asset = describe(asset)
    owner = asset['storage_project_id']
    if owner:
        return store.project_file(owner, relative or asset['path'])
    root = (store.DATA / 'library').resolve()
    target = (root / (relative or asset['path'])).resolve()
    if not target.is_relative_to(root) or target == root:
        raise ValueError('素材路径超出存储目录')
    return target


def rules(asset):
    asset = describe(asset)
    if asset['type'] == 'Music' and asset.get('analysis', {}).get('duration'):
        asset['protected'] = [{'start': 0, 'end': asset['analysis']['duration']}]
    return asset


def migrate():
    for asset in store.listing('asset'):
        if 'type' not in asset or 'scope' not in asset or 'storage_project_id' not in asset:
            store.put('asset', rules(asset))


def update(ident, body):
    from . import jobs, timeline
    with jobs.LOCK:
        asset = describe(store.get('asset', ident))
        old_owner, old_type = asset.get('project_id'), asset['type']
        scope = body.get('scope', asset['scope'])
        if scope not in ('project', 'public'):
            raise ValueError('请选择项目素材或公共素材')
        owner = body.get('project_id', old_owner) if scope == 'project' else None
        if scope == 'project':
            if not owner:
                raise ValueError('项目素材需要选择项目')
            store.get('project', owner)
        for task in store.listing('task'):
            # Inspiration batches belong to no project and use no media.
            if task['status'] in ('queued', 'running', 'cancelling') and task.get('kind') != 'inspire' and (
                    task.get('project_id') in {old_owner, owner} or any(a['id'] == ident for a in task.get('asset_snapshot', []))):
                raise ValueError('相关素材任务执行中，请完成或取消后再修改素材')
        value = validate_type(body.get('type', old_type), Path(asset['path']).suffix)
        asset.update(scope=scope, project_id=owner, type=value)
        if old_type == 'Music' and value != 'Music':
            asset['protected'] = []
        if 'name' in body:
            name = str(body['name']).strip()
            if not name or len(name) > 200:
                raise ValueError('素材名称应为 1–200 字')
            asset['name'] = name
        if 'provenance' in body:
            asset['provenance'] = str(body['provenance'])[:2000]
        if 'protected' in body:
            if value == 'Music':
                raise ValueError('Music 类型默认完整保护')
            if not asset.get('analysis'):
                raise ValueError('请先分析素材')
            regions = []
            for region in body['protected']:
                start, end = timeline.number(region['start']), timeline.number(region['end'])
                if not 0 <= start < end <= asset['analysis']['duration']:
                    raise ValueError('音乐保护区时间越界')
                regions.append({'start': start, 'end': end})
            asset['protected'] = regions
        result = store.put('asset', rules(asset))
        for project_id in {old_owner, owner} - {None}:
            project = store.get('project', project_id)
            project['stale_stages'] = sorted(set(project.get('stale_stages', [])) | {'edit'})
            scene_id = project.get('workspace', {}).get('scene_version')
            if old_owner != owner and scene_id and any(s.get('asset_id') == ident for s in store.get('version', scene_id)['result']['scenes']):
                project['stale_stages'] = sorted(set(project['stale_stages']) | {'scenes'})
            store.put('project', project)
        return result
