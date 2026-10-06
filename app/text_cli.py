"""Research and writing through the installed, subscription-authenticated CLI."""
import copy
import json
import re
import subprocess
from pathlib import Path

from . import claude_cli, config, media, store

STAGES = ('research', 'script')
MODELS = [{'id': name, 'name': label, 'provider': 'claude', 'engine': 'claude_cli', 'tag': '订阅额度'}
          for name, label in [('sonnet', 'Claude Sonnet'), ('opus', 'Claude Opus'), ('haiku', 'Claude Haiku')]]


def settings():
    return {'engine': 'api', 'models': {'research': 'sonnet', 'script': 'sonnet'}} | config.read().get('text_service', {})


def enabled():
    return settings()['engine'] == 'claude_cli'


def available():
    return bool(claude_cli.cli_command()) if enabled() else config.has_api_key()


def validate(model):
    if model not in [m['id'] for m in MODELS] and not re.fullmatch(r'claude-[a-z0-9.-]+', model or ''):
        raise ValueError('订阅模式请选择 Claude Sonnet、Opus 或 Haiku')
    return model


def resolve(stage, supplied=None):
    return validate(supplied or settings()['models'][stage])


def save(body):
    with config.LOCK:
        value = settings()
        if body.get('engine', value['engine']) not in ('api', 'claude_cli'):
            raise ValueError('请选择 API 或 Claude 订阅')
        value['engine'] = body.get('engine', value['engine'])
        models = body.get('models', {})
        if not isinstance(models, dict) or set(models) - set(STAGES):
            raise ValueError('仅支持调研和文案模型配置')
        value['models'] = value['models'] | {stage: validate(model) for stage, model in models.items()}
        machine = config.read()
        machine['text_service'] = value
        config.write(machine)
        return value


def require_subscription(command, root, event):
    """Verify the CLI's login without obtaining or storing OAuth credentials."""
    if event.is_set():
        raise media.Cancelled()
    proc = claude_cli.popen(command + ['auth', 'status'], cwd=str(root), stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=claude_cli.environment())
    try:
        output, _ = proc.communicate(timeout=15)
        try:
            auth = json.loads(output)
        except (ValueError, TypeError):
            auth = {}
        if proc.returncode or auth.get('loggedIn') is not True or auth.get('authMethod') != 'claude.ai':
            raise ValueError('请在 Claude Code 中使用 Claude 订阅账号登录（claude auth login），当前未检测到订阅登录')
    except subprocess.TimeoutExpired:
        raise ValueError('Claude Code 登录检查超时，请在终端检查登录') from None
    finally:
        claude_cli.terminate(proc)
    if event.is_set():
        raise media.Cancelled()


class Evidence:
    def __init__(self):
        self.tools, self.raw = {}, []

    def collect(self, item):
        if item.get('type') not in ('assistant', 'user'):
            return
        for block in item.get('message', {}).get('content', []):
            if block.get('type') == 'tool_use' and block.get('name') in ('WebSearch', 'WebFetch'):
                self.tools[block.get('id')] = (block['name'], block.get('input', {}))
            elif block.get('type') == 'tool_result' and not block.get('is_error'):
                name, args = self.tools.get(block.get('tool_use_id'), ('', {}))
                content = block.get('content', '')
                text = content if isinstance(content, str) else '\n'.join(b.get('text', '') for b in content if isinstance(b, dict))
                if not text.strip():
                    continue
                if name == 'WebFetch' and args.get('url'):
                    self.raw.append({'type': 'web_fetch_tool_result', 'content': {'type': 'web_fetch_result', 'url': args['url']}})
                elif name == 'WebSearch':
                    urls = list(dict.fromkeys(u.rstrip('.,;:)') for u in re.findall(r'https?://[^\s<>"\]\u3002\uff0c]+', text)))
                    self.raw.append({'type': 'web_search_tool_result', 'content': [{'type': 'web_search_result', 'url': u} for u in urls]})


RECORDS = {'research': 'topics', 'script': 'paragraphs', 'angles': 'angles', 'inspire': 'ideas'}
CHAT_ACTIONS = ['none', 'research', 'angles', 'custom_angle', 'revise_script']


def schema(mode):
    if mode == 'chat':
        text = {'type': 'string'}
        return {'type': 'object', 'required': ['reply'], 'additionalProperties': True, 'properties': {
            'reply': text, 'question': text, 'options': {'type': 'array', 'items': text}, 'card': {'type': 'object'},
            'action': {'type': 'string', 'enum': CHAT_ACTIONS}, 'action_input': text, 'action_label': text}}
    key = RECORDS[mode]
    # Claude tool schemas require an object root and reject root combinators.
    # The application validates which optional result/message was returned.
    return {'type': 'object', 'properties': {
        key: {'type': 'array', 'minItems': 1, 'items': {'type': 'object'}},
        'needs_clarification': {'type': 'string'}, 'advice': {'type': 'string'}},
        'additionalProperties': True}


def validate_result(result, mode):
    if not isinstance(result, dict):
        raise ValueError('Claude 返回的调研或文案结构无效')
    if mode == 'chat':
        if not isinstance(result.get('reply'), str) or not result['reply'].strip():
            raise ValueError('Claude 没有给出有效回复，请重试')
        return result
    from .jobs import normalize_result
    result = normalize_result(result, mode)
    key = RECORDS[mode]
    records = result.get(key)
    if isinstance(records, list) and records and all(isinstance(record, dict) for record in records):
        return result
    if 'needs_clarification' in result or 'advice' in result:
        return result
    raise ValueError('Claude 返回的结构化结果缺少有效的 ' + key + '，请重试')


def call(task, mode, context, event, report):
    from . import models
    if mode not in RECORDS and mode != 'chat':
        raise ValueError('订阅文字服务仅支持调研、灵感、对话和文案')
    settings = task['cli_config']
    command = claude_cli.cli_command(settings)
    if not command:
        raise ValueError('未找到 Claude Code，请在设置中检测本机 CLI')
    # Inspiration batches belong to no project.
    owner = store.project_dir(task['project_id']) if task.get('project_id') else store.DATA / 'inspiration'
    root = owner / 'text-tasks' / task['id']
    root.mkdir(parents=True, exist_ok=True)
    report('正在检查 Claude 订阅登录')
    require_subscription(command, root, event)
    (root / 'mcp.json').write_text('{"mcpServers":{}}', encoding='utf-8')
    web = mode == 'research' or (mode == 'inspire' and context.get('web'))
    tools = 'WebSearch,WebFetch' if web else ''
    args = command + ['-p', '--output-format', 'stream-json', '--verbose', '--model', settings['model'],
                      '--max-turns', str(settings['max_turns']), '--permission-mode', 'dontAsk', '--restricted',
                      '--tools', tools, '--strict-mcp-config', '--mcp-config', str(root / 'mcp.json'),
                      '--json-schema', json.dumps(schema(mode))]
    if tools:
        args += ['--allowedTools', tools]
    prompt = (models.SYSTEM + models.SCHEMAS[mode] + '\n仅完成本次调研或文案，不制作视频，不生成字幕。'
              + ('\n必须使用实际联网工具，无法访问时如实说明；禁止编造来源、日期和互动数据。' if web else
                 '\n本次不联网；禁止编造来源、日期和互动数据。')
              + '\n任务上下文：\n' + json.dumps(context, ensure_ascii=False, default=str))
    # Keep stage inputs for diagnostics without copying login credentials.
    (root / 'context.json').write_text(json.dumps(context, ensure_ascii=False, default=str), encoding='utf-8')
    evidence = Evidence()
    report({'research': '正在使用 Claude 订阅调研', 'inspire': '正在构思灵感', 'chat': '正在思考',
            'angles': '正在构思新方向'}.get(mode, '正在使用 Claude 订阅写稿'))
    response = claude_cli.execute_process(task, args, root, prompt, event, report, on_event=evidence.collect)
    if event.is_set():
        raise media.Cancelled()
    result = response.get('structured_output')
    if not isinstance(result, dict):
        text = str(response.get('result') or '').strip()
        if text.startswith('```'):
            text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text)
        try:
            result = json.loads(text)
        except ValueError:
            (root / 'result.txt').write_text(text, encoding='utf-8')
            raise ValueError('Claude 未返回有效的结构化调研或文案，请重试') from None
    # Retained for diagnosis when a result is rejected below.
    (root / 'result.json').write_text(json.dumps(result, ensure_ascii=False, default=str), encoding='utf-8')
    result = validate_result(result, mode)
    metadata = copy.deepcopy(task['model_config'])
    metadata['cli_result'] = store.get('task', task['id']).get('cli_result', {})
    return result, evidence.raw, metadata
