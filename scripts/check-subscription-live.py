"""Manual subscription smoke test. Uses real Claude quota and isolated artifacts."""
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient
from app import claude_cli, config, store, text_cli
from app.main import app

sys.stdout.reconfigure(encoding='utf-8')
local_options = claude_cli.options()
directory = ROOT / 'artifacts' / ('subscription-live-' + str(int(time.time())))
directory.mkdir(parents=True)
checks = []
with patch.object(store, 'DATA', directory), TestClient(app) as client:
    claude_cli.save(dict(local_options, timeout_sec=240, max_turns=12))
    text_cli.save({'engine': 'claude_cli'})
    project = client.post('/api/projects', json={'name': '订阅连接隔离验收', 'requirements': {'duration': 30}}).json()
    url = '/api/projects/' + project['id']

    def finish(task):
        phase = ''
        for _ in range(2600):
            current = store.get('task', task['id'])
            if current['phase'] != phase:
                phase = current['phase'];print(phase, flush=True)
            if current['status'] not in ('running', 'queued', 'cancelling'):
                if current['status'] != 'completed':
                    failed = {'status': 'BLOCKED', 'checks': checks, 'stage': current['kind'],
                              'error': current.get('error') or current['status'], 'data_directory': str(directory)}
                    (ROOT / 'artifacts' / 'subscription-live-results.json').write_text(json.dumps(failed, ensure_ascii=False, indent=2), encoding='utf-8')
                    raise RuntimeError(current.get('error') or current['status'])
                return current
            time.sleep(.1)
        raise RuntimeError('Smoke test timed out')

    response = client.post(url + '/tasks', json={'kind': 'research', 'workspace': True,
        'prompt': '请做极简调研：Claude Code 如何使用 Claude 订阅登录？只检索 code.claude.com 官方认证文档，最多一次搜索和一次网页读取。一个主题、三个一句话的短视频角度、一个真实来源即可。不要输出长文。'})
    response.raise_for_status()
    researched = finish(response.json())
    research = store.get('version', researched['output']['version_id'])
    assert research['model_config']['billing'] == 'subscription'
    assert any(s['verification'] in ('fetched', 'search_only') for s in research['result']['sources']), 'No tool-grounded source'
    checks.append('Real subscription research with tool-grounded official sources')
    response = client.post(url + '/tasks', json={'kind': 'script', 'workspace': True,
        'prompt': '根据刚才的研究写一份极简中文口播：三个段落，每段一句话，不超过120字。保留真实来源链接，不生成字幕或视频。'})
    response.raise_for_status()
    written = finish(response.json())
    script = store.get('version', written['output']['version_id'])
    assert len(script['result']['paragraphs']) >= 1
    assert script['model_config']['billing'] == 'subscription'
    assert not store.listing('usage')
    checks.append('Real subscription writing without workbench API billing')
    report = {'status': 'PASS', 'checks': checks, 'data_directory': str(directory),
              'research_model': researched['model_config']['model'], 'script_model': written['model_config']['model'],
              'actual_model_usage': written.get('cli_result', {}).get('model_usage', {}),
              'sources': [{'url': s['url'], 'verification': s['verification']} for s in research['result']['sources']]}
    (ROOT / 'artifacts' / 'subscription-live-results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False), flush=True)
