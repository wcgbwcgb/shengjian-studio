import copy
import queue
import threading
import time

from . import assets as asset_store, catalog, claude_cli, config, media, models, store, text_cli, timeline

QUEUE = queue.Queue()
EVENTS = {}
LOCK = threading.RLock()
STOP = threading.Event()
THREAD = None


def start():
    global THREAD
    STOP.clear()
    THREAD = threading.Thread(target=worker, daemon=True, name='media-worker')
    THREAD.start()


def close():
    STOP.set()
    with LOCK:
        for event in list(EVENTS.values()):
            event.set()
    if THREAD:
        THREAD.join(timeout=6)


def submit(project_id, kind, payload, frozen=None):
    with LOCK:
        project = store.get('project', project_id)
        if any(t['status'] in ('running', 'queued', 'cancelling') for t in store.listing('task', project_id)):
            raise ValueError('该项目已有任务执行中，请等待或取消后再修改')
        stage = 'edit' if kind in ('analyze', 'preview', 'final', 'local_edit', 'first_cut', 'cli_video') else ('script' if kind in ('script', 'angles') else kind)
        payload = dict(payload)
        ai_task = kind in ('research', 'script', 'angles', 'edit', 'chat')
        # Conversation turns use the writing model.
        model_stage = 'script' if kind == 'chat' else stage
        using_cli = kind in ('research', 'script', 'angles', 'chat') and (frozen['model_config'].get('engine') == 'claude_cli' if frozen else text_cli.enabled())
        selected_model = (frozen['model_config']['model'] if frozen else
                          text_cli.resolve(model_stage, payload.get('model')) if using_cli else catalog.resolve(model_stage, payload.get('model'))) if ai_task else None
        cli_config = None
        if using_cli:
            cli_config = copy.deepcopy(frozen['cli_config']) if frozen else dict(claude_cli.options(), model=selected_model)
            if not claude_cli.cli_command(cli_config):
                raise ValueError('未找到 Claude Code，请在设置中配置并检测订阅登录')
            model_config, credential = ({'engine': 'claude_cli', 'provider': 'claude', 'model': selected_model, 'billing': 'subscription'}, {})
            if frozen:
                model_config = copy.deepcopy(frozen['model_config'])
        else:
            model_config, credential = (config.freeze(selected_model) if ai_task and not frozen else
                                        (copy.deepcopy(frozen['model_config']), store.task_credentials(frozen['id']) or {}) if frozen else ({'model': 'local'}, {}))
        if ai_task and frozen and not using_cli and not model_config.get('url'):
            # Older tasks did not persist connection credentials. Freeze the
            # compatible connection on their first retry, then retain it.
            captured, credential = config.freeze(selected_model)
            captured.update(model_config)
            model_config = captured
        payload['model'] = selected_model
        prompt = str(payload.get('prompt', ''))[:20000]
        if kind == 'cli_video':
            if not prompt.strip():
                raise ValueError('请填写视频制作要求')
            if not claude_cli.cli_command(frozen.get('cli_config') if frozen else None):
                raise ValueError('未找到 Claude Code。请在设置中配置并检测本机 CLI。')
            base_id = payload.get('base_version_id')
            if base_id:
                base = store.get('version', base_id)
                if base['project_id'] != project_id or base['stage'] != 'edit':
                    raise ValueError('引用的视频版本不属于当前项目')
        effective = store.effective(project, stage, prompt, payload.get('settings', {}))
        snapshot_assets = [asset_store.rules(a) for a in reversed(store.listing('asset', project_id))]
        if kind in ('preview', 'final') and payload.get('version_id'):
            v = store.get('version', payload['version_id'])
            if v['project_id'] != project_id or v['stage'] != 'edit':
                raise ValueError('成片版本不属于该项目')
            snapshot_assets = v.get('asset_snapshot') or [asset_store.rules(store.get('asset', ident)) for ident in v.get('asset_ids', [])] or snapshot_assets
        task = store.put('task', {'kind': kind, 'status': 'queued', 'phase': '等待执行', 'payload': payload,
                                 'snapshot': project, 'effective': effective, 'upstream': dict(project.get('adopted', {})),
                                 'asset_snapshot': snapshot_assets,
                                 'source_snapshot': store.listing('source', project_id),
                                 'model_config': model_config,
                                 'subtitles_enabled': False,
                                 'logs': [], 'error': None}, project_id)
        if ai_task and not using_cli:
            store.save_task_credentials(task['id'], credential)
        if using_cli:
            task['cli_config'] = cli_config
            task = store.put('task', task, project_id)
        if kind == 'cli_video':
            task['cli_config'] = copy.deepcopy(frozen['cli_config']) if frozen else claude_cli.options()
            task['model_config'] = {'engine': 'claude_cli', 'provider': 'claude', 'model': task['cli_config']['model']}
            task = store.put('task', task, project_id)
        if frozen:
            for field in ('snapshot', 'upstream', 'effective', 'asset_snapshot', 'source_snapshot'):
                task[field] = copy.deepcopy(frozen[field])
            task['retry_of'] = frozen['id']
            task = store.put('task', task, project_id)
        EVENTS[task['id']] = threading.Event()
        if prompt:
            store.put('message', {'stage': stage, 'role': 'user', 'text': prompt, 'task_id': task['id'], 'effective': effective}, project_id)
            project.setdefault('stage_settings', {})[stage] = {k: v for k, v in effective.items() if k != 'prompt'}
            project.setdefault('stage_prompts', {})[stage] = prompt
            project.setdefault('stage_instruction_chain', {}).setdefault(stage, []).append(prompt)
            store.put('project', project)
        QUEUE.put(task['id'])
        return task


def cancel(task_id):
    with LOCK:
        task = store.get('task', task_id)
        if task['status'] not in ('queued', 'running', 'cancelling'):
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


def version(task, stage, result, parent=None, config=None, raw=None):
    with LOCK:
        current = store.get('project', task['project_id'])
        stale = current.get('adopted', {}) != task['upstream'] or current.get('selected_topic_id') != task['snapshot'].get('selected_topic_id')
        if stage == 'edit':
            stale = stale or {a['id'] for a in store.listing('asset', task['project_id'])} != {a['id'] for a in task['asset_snapshot']}
        return store.put('version', {'stage': stage, 'parent_id': parent, 'result': result,
                         'prompt': task['payload'].get('prompt', ''), 'effective': task['effective'],
                         'upstream': task['upstream'], 'model_config': config or {'model': 'local'},
                         'source_ids': [s['id'] for s in result.get('sources', [])] if stage == 'research' else [s['id'] for s in task['source_snapshot']],
                         'asset_ids': [a['id'] for a in task['asset_snapshot']],
                         'asset_snapshot': task['asset_snapshot'], 'subtitles_enabled': False,
                         'selected_topic_id': task['snapshot'].get('selected_topic_id'),
                         'task_id': task['id'], 'stale': stale, 'raw_tool_results': raw or []}, task['project_id'])


def execute(task, event):
    project_id, kind, payload = task['project_id'], task['kind'], task['payload']
    report = lambda text: phase(task['id'], text)
    assets = task['asset_snapshot']
    if kind == 'cli_video':
        return claude_cli.execute(task, event, report)
    if kind == 'first_cut':
        from .workspace import execute_first_cut
        return execute_first_cut(task, event, report)
    if kind == 'chat':
        from .workspace import execute_chat
        return execute_chat(task, event, report)
    if kind == 'angles' and payload.get('workspace'):
        from .workspace import execute_angles
        return execute_angles(task, event, report)
    if kind == 'analyze':
        if not assets:
            raise ValueError('请先上传原始素材')
        for asset in assets:
            media.analyze(asset, event, report, False)
        return {'asset_count': len(assets)}
    if kind == 'local_edit':
        if not assets or any(not a.get('analysis') for a in assets):
            raise ValueError('请先分析所有素材')
        result = timeline.full_timeline(assets, task['effective']['aspect'])
        result['captions'] = []
        v = version(task, 'edit', result, task['upstream'].get('edit'))
        artifacts = media.render(project_id, v, assets, event, report)
        v['preview'] = artifacts
        store.put('version', v, project_id)
        return {'version_id': v['id'], 'artifacts': artifacts}
    if kind in ('preview', 'final'):
        v = store.get('version', payload['version_id'])
        if v['project_id'] != project_id or v['stage'] != 'edit':
            raise ValueError('成片版本不属于该项目')
        if v['result'].get('engine') == 'claude_cli':
            artifacts = v.get('final') or v.get('preview')
            if not artifacts or not store.project_file(project_id, artifacts['video']).is_file():
                raise ValueError('视频文件不存在，请用制作要求重新生成')
            return {'version_id': v['id'], 'artifacts': artifacts}
        v['subtitles_enabled'] = False
        v['result']['captions'] = []
        version_asset_ids = set(v.get('asset_ids') or [s['source_asset_id'] for s in v['result']['segments']])
        assets = [a for a in assets if a['id'] in version_asset_ids]
        artifacts = media.render(project_id, v, assets, event, report, kind == 'final')
        v['final' if kind == 'final' else 'preview'] = artifacts
        store.put('version', v, project_id)
        with LOCK:
            project = store.get('project', project_id)
            if kind == 'final' and project.get('adopted', {}).get('edit') == v['id']:
                project['status'] = '已导出'
                store.put('project', project)
        return {'version_id': v['id'], 'artifacts': artifacts}
    stage = 'script' if kind in ('script', 'angles') else kind
    context = {'intent': payload.get('intent', 'generate'), 'effective': task['effective'], 'project': task['snapshot'],
               'sources': task['source_snapshot'], 'assets': assets,
               'persistent_stage_instructions': task['snapshot'].get('stage_instruction_chain', {}).get(stage, []),
               'history': list(reversed(store.listing('message', project_id))) [-12:], 'target_ids': payload.get('target_ids', []),
               'selected_angle': payload.get('angle'),
               'requirements_card': task['snapshot'].get('workspace', {}).get('card', {})}
    for upstream_stage, ident in task['upstream'].items():
        context[upstream_stage] = store.get('version', ident)['result']
    if kind == 'edit':
        if not assets or any(not a.get('analysis') for a in assets):
            raise ValueError('请先完成素材分析')
        base_id = payload.get('base_version_id') or task['upstream'].get('edit')
        if base_id:
            base = store.get('version', base_id)
            if base['project_id'] != project_id or base['stage'] != 'edit':
                raise ValueError('预览版本不属于当前项目')
            context['referenced_preview'] = {'id': base_id, 'timeline': base['result']}
    else:
        base_id = None if payload.get('fresh_script') else (payload.get('base_version_id') or task['upstream'].get(stage))
        if base_id:
            base = store.get('version', base_id)
            if base['project_id'] != project_id or base['stage'] != stage:
                raise ValueError('引用版本不属于当前阶段')
            context['current'] = base['result']
    result, raw, config = models.call(task, kind, context, event, report)
    if kind != 'edit':
        result = normalize_result(result, kind)
    if result.get('needs_clarification') or result.get('advice'):
        store.put('message', {'stage': stage, 'role': 'assistant', 'text': result.get('needs_clarification') or result['advice'], 'task_id': task['id']}, project_id)
        return {'conversation': result}
    if kind == 'research':
        result = models.ground_research(result, raw, context['sources'])
        for source in result['sources']:
            store.put('source', source, project_id)
    if kind == 'script':
        result = protect_script(result, context.get('current'), payload.get('target_ids', []))
        from .workspace import ground_script
        result = ground_script(result, context['sources'])
    if kind == 'edit':
        result['captions'] = []
        result = timeline.validate(result, assets)
    v = version(task, stage, result, base_id, config, raw)
    if kind == 'research' and payload.get('workspace'):
        with LOCK:
            project = store.get('project', project_id)
            if project.get('status') in ('构思中', '待调研'):
                project['status'] = '待选择角度'
                store.put('project', project)
    if kind == 'script' and payload.get('workspace'):
        from .workspace import completed_script
        completed_script(task, v)
    if kind == 'edit':
        v['preview'] = media.render(project_id, v, assets, event, report)
        store.put('version', v, project_id)
    store.put('message', {'stage': stage, 'role': 'assistant', 'text': '已保存新版本，等待查看并采用。' + result.get('summary', ''),
                         'version_id': v['id'], 'task_id': task['id']}, project_id)
    return {'version_id': v['id']}


RECORD_KEYS = {'research': 'topics', 'script': 'paragraphs', 'angles': 'angles'}


def normalize_result(result, mode):
    """The output schema offers optional message fields next to the records. Models
    often fill them with empty strings; valid records always win over a message."""
    result = dict(result)
    for field in ('needs_clarification', 'advice'):
        if field in result and (not isinstance(result[field], str) or not result[field].strip()):
            result.pop(field)
    records = result.get(RECORD_KEYS.get(mode, ''))
    if isinstance(records, list) and records and all(isinstance(r, dict) for r in records):
        result.pop('needs_clarification', None)
        result.pop('advice', None)
    return result


def protect_script(result, current=None, targets=None):
    paragraphs = result.get('paragraphs')
    if not isinstance(paragraphs, list) or not paragraphs:
        raise ValueError('文案结果缺少段落')
    ids = set()
    for p in paragraphs:
        p.setdefault('id', store.uid())
        if p['id'] in ids:
            raise ValueError('文案段落标识重复')
        ids.add(p['id'])
        p.setdefault('locked', False)
        p.setdefault('cue', '')
        p['text'] = str(p['text'])
    if current and current.get('paragraphs'):
        originals = current['paragraphs']
        generated = {p['id']: p for p in paragraphs}
        if targets:
            if not set(targets).issubset({p['id'] for p in originals}):
                raise ValueError('局部重写段落不存在')
            # Retain the complete original structure. Only named unlocked paragraphs change.
            result['paragraphs'] = [generated.get(p['id'], p) if p['id'] in targets and not p.get('locked') else p for p in originals]
            for key in ('facts', 'shot_list', 'music_needs', 'subtitle_notes', 'publishing', 'angle'):
                if key in current:
                    result[key] = current[key]
        else:
            for p in originals:
                if p.get('locked'):
                    if p['id'] not in generated:
                        raise ValueError('模型移除了锁定段落，请采用局部重写')
                    generated[p['id']].update(p)
    for p in result['paragraphs']:
        p['estimated_sec'] = round(len(p['text']) / 4.2, 1)
    result['estimated_sec'] = round(sum(p['estimated_sec'] for p in result['paragraphs']), 1)
    return result


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
            output = execute(task, event)
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
            error = config.redact(exc)
            task.update(status='failed', phase='失败，可重试', error=error[:3000])
        finally:
            with LOCK:
                task['elapsed_sec'] = round(time.monotonic() - started, 2)
                task['finished_at'] = store.now()
                store.put('task', task, task['project_id'])
            QUEUE.task_done()
