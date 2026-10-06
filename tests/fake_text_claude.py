"""Subscription text CLI fixture; never imported by production."""
import json
import os
import sys
import time
import uuid
from pathlib import Path

if sys.argv[1:3] == ['auth', 'status']:
    print(json.dumps({'loggedIn': True, 'authMethod': os.getenv('TEXT_CLI_AUTH', 'claude.ai')}))
    sys.exit(0)
prompt = sys.stdin.read()
context = json.loads(prompt.split('任务上下文：\n', 1)[1])
output_schema = json.loads(sys.argv[sys.argv.index('--json-schema') + 1])
if output_schema.get('type') != 'object':
    print(json.dumps({'type': 'result', 'subtype': 'error', 'is_error': True,
                      'result': 'API Error: 400 tools.0.custom.input_schema.type: Field required'}), flush=True)
    sys.exit(1)
if any(key in output_schema for key in ('oneOf', 'allOf', 'anyOf')):
    print(json.dumps({'type': 'result', 'subtype': 'error', 'is_error': True,
                      'result': 'API Error: 400 tools.0.custom.input_schema: input_schema does not support oneOf, allOf, or anyOf at the top level'}), flush=True)
    sys.exit(1)
field = next(key for key in ('topics', 'paragraphs', 'angles', 'ideas', 'reply') if key in output_schema['properties'])
mode = {'topics': 'research', 'paragraphs': 'script', 'angles': 'angles', 'ideas': 'inspire', 'reply': 'chat'}[field]
Path('received.json').write_text(json.dumps({'argv': sys.argv, 'prompt': prompt, 'has_api_key': bool(os.getenv('ANTHROPIC_API_KEY')),
                                          'context': context}, ensure_ascii=False), encoding='utf-8')
session = str(uuid.uuid4())
print(json.dumps({'type': 'system', 'subtype': 'init', 'session_id': session}), flush=True)
test_mode = os.getenv('TEXT_CLI_MODE', 'success')
if test_mode == 'hang':
    time.sleep(120)
if mode == 'research':
    for name, ident, args, content in [
        ('WebSearch', 's', {'query': 'fixture'}, 'Links: [{"title":"Fixture","url":"https://example.org/read"},{"url":"https://example.org/search"}]'),
        ('WebFetch', 'f', {'url': 'https://example.org/read', 'prompt': 'summarize'}, 'Fixture source content')]:
        print(json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'name': name, 'id': ident, 'input': args}]}}), flush=True)
        print(json.dumps({'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': ident, 'content': content}]}}), flush=True)
    result = {'summary': '隔离订阅调研', 'topics': [{'id': 'topic', 'title': '订阅调研主题',
              'what_happened': 'Fixture', 'source_urls': ['https://example.org/read'],
              'angles': [{'id': str(i), 'title': f'角度 {i}'} for i in range(3)]}],
              'sources': [{'url': u, 'title': 'Fixture', 'verification': 'fetched'} for u in
                          ['https://example.org/read', 'https://example.org/search', 'https://example.org/invented']]}
elif mode == 'angles':
    result = {'angles': [{'id': str(i), 'title': f'新角度 {i}'} for i in range(3)]}
elif mode == 'inspire':
    result = {'ideas': [{'title': f'订阅灵感 {i}', 'description': '隔离测试', 'tags': ['测试']} for i in range(4)]}
elif mode == 'chat':
    asked = any(m['role'] == 'assistant' for m in context.get('conversation', []))
    result = ({'reply': '明白了。', 'question': '', 'options': [], 'card': {'tone': '轻松'}, 'action': 'research',
               'action_input': '订阅调研重点', 'action_label': '开始研究'} if asked else
              {'reply': '先确认一下。', 'question': '给谁看？', 'options': ['学生', '上班族'], 'card': {'audience': '学生'},
               'action': 'none', 'action_input': '', 'action_label': ''})
else:
    result = context.get('current') or {'angle': '订阅文案', 'paragraphs': [
        {'id': 'hook', 'speaker': '旁白', 'text': '订阅写作开场', 'cue': '近景', 'source_urls': ['https://example.org/read']},
        {'id': 'end', 'speaker': '旁白', 'text': '保留的结尾', 'cue': ''}], 'publishing': {'titles': ['测试标题']}}
    for p in result['paragraphs']:
        if p['id'] in context.get('target_ids', []):
            p['text'] = '订阅局部改写'
response = {'type': 'result', 'subtype': 'success', 'session_id': session, 'is_error': False,
            'structured_output': result, 'result': 'done', 'total_cost_usd': .05, 'num_turns': 3,
            'usage': {'input_tokens': 100, 'output_tokens': 50}, 'modelUsage': {'claude-sonnet-fixture': {}}}
if test_mode == 'invalid':
    response.pop('structured_output')
elif test_mode == 'empty':
    response['structured_output'] = {}
elif test_mode == 'advice':
    response['structured_output'] = {'advice': '先明确受众'}
elif test_mode == 'clarification':
    response['structured_output'] = {'needs_clarification': '请补充主题'}
print(json.dumps(response, ensure_ascii=False), flush=True)
