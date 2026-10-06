"""Isolated browser fixture with the real worker and a text CLI subprocess."""
import os
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import claude_cli, config, store, text_cli
from app.main import app
import uvicorn
if not store.DATA.is_relative_to(ROOT / 'artifacts'):
    raise RuntimeError('Text CLI fixture requires isolated artifacts data')
claude_cli.cli_command = lambda settings=None: [sys.executable, str(ROOT / 'tests' / 'fake_text_claude.py')]
config.has_api_key = lambda: False
text_cli.save({'engine': 'claude_cli'})
uvicorn.run(app, host='127.0.0.1', port=int(os.getenv('TEXT_CLI_PORT', '8888')), log_level='warning')
