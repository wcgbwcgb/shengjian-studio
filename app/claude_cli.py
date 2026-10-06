"""Run the user's Claude Code CLI with a prompt in a working folder; every feature is a different prompt."""
import json
import math
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from . import assets as asset_store, config, media, store

DEFAULTS = {'path': '', 'model': 'opus', 'timeout_sec': 7200, 'max_turns': 300}
LEGACY_LIMITS = {'timeout_sec': 1800, 'max_turns': 50}
MIN_VERSION = (2, 1, 248)
# Persistent, agent-writable environment for Python/Node packages, browsers and templates.
TOOLBOX = Path(os.getenv('MEDIA_AGENT_TOOLBOX', str(store.ROOT / '.runtime' / 'agent-toolbox'))).resolve()


def options():
    saved = config.read().get('claude_cli', {})
    if all(saved.get(k) == v for k, v in LEGACY_LIMITS.items()):
        # Earlier versions stored their defaults; full-video agents need the larger budget.
        saved = {k: v for k, v in saved.items() if k not in LEGACY_LIMITS}
    return DEFAULTS | saved


def save(body):
    value = options()
    if 'path' in body:
        value['path'] = str(body['path']).strip().strip('"')
        if len(value['path']) > 2000 or any(c in value['path'] for c in ('\n', '\r', '\x00')):
            raise ValueError('Claude Code 路径不合法')
    if 'model' in body:
        model = str(body['model']).strip()
        if not re.fullmatch(r'(opus|sonnet|haiku|claude-[a-z0-9.-]+)', model):
            raise ValueError('请选择 Claude 模型或填写完整 Claude 模型 ID')
        value['model'] = model
    for key, low, high in [('timeout_sec', 60, 21600), ('max_turns', 1, 2000)]:
        if key in body:
            try:
                number = int(body[key])
            except (ValueError, TypeError):
                raise ValueError(f'{key} 应为整数') from None
            if not low <= number <= high:
                raise ValueError(f'{key} 应在 {low}–{high} 之间')
            value[key] = number
    with config.LOCK:
        machine = config.read()
        if any(value[k] != options()[k] for k in DEFAULTS):
            machine.pop('claude_cli_check', None)
        machine['claude_cli'] = value
        config.write(machine)
    return status()


def cli_command(settings=None):
    value = settings or options()
    configured = value['path']
    candidates = ([configured] if configured else ['claude', str(Path.home() / '.local' / 'bin' / 'claude.exe'),
                  str(Path.home() / '.local' / 'bin' / 'claude'),
                  str(Path(os.getenv('APPDATA', '')) / 'npm' / 'claude.cmd')])
    for candidate in candidates:
        found = shutil.which(candidate) or (candidate if Path(candidate).is_file() else None)
        if not found:
            continue
        path = Path(found).resolve()
        if path.suffix.lower() in ('.cmd', '.bat', '.ps1'):
            # Never interpolate a user prompt into cmd.exe or a npm shell shim.
            script = path.parent / 'node_modules' / '@anthropic-ai' / 'claude-code' / 'cli.js'
            node = shutil.which('node')
            if node and script.is_file():
                return [node, str(script)]
            if configured:
                raise ValueError('请填写原生 claude.exe 路径，或安装可用的 Node.js 与 npm 版 Claude Code')
            continue
        return [str(path)]
    return None


def status():
    value = options()
    try:
        command = cli_command(value)
        error = None
    except ValueError as exc:
        command, error = None, str(exc)
    return {'installed': bool(command), 'executable': command[0] if command else None,
            'options': value, 'last_check': config.read().get('claude_cli_check'), 'error': error}


def environment():
    # Preserve CLI-managed login, while excluding the workbench API/proxy configuration.
    env = os.environ.copy()
    for key in list(env):
        if key.startswith('ANTHROPIC_') or key in ('CLAUDE_CODE_OAUTH_TOKEN', 'CLAUDECODE') or key.startswith('CLAUDE_CODE_USE_'):
            env.pop(key, None)
    venv = TOOLBOX / 'venv' / ('Scripts' if os.name == 'nt' else 'bin')
    folders = [str(venv)] if venv.is_dir() else []
    folders += [str(Path(sys.executable).parent)]
    folders += [str(Path(p).parent) for n in ('ffmpeg', 'ffprobe') if (p := media.executable(n))]
    env['PATH'] = os.pathsep.join(dict.fromkeys(folders)) + os.pathsep + env.get('PATH', '')
    env['PYTHONIOENCODING'] = 'utf-8'
    cache = TOOLBOX / 'cache'
    env.setdefault('PIP_CACHE_DIR', str(cache / 'pip'))
    env.setdefault('npm_config_cache', str(cache / 'npm'))
    env.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(cache / 'ms-playwright'))
    env.setdefault('PUPPETEER_CACHE_DIR', str(cache / 'puppeteer'))
    env['CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC'] = '1'
    return env


def clean(value):
    return re.sub(r'\b(?:sk-ant-|sk-proj-|sk-)[A-Za-z0-9_-]{12,}', '[已隐藏]', str(value))


def terminate(proc):
    job = getattr(proc, '_cli_job', None)
    if job is not None:
        import ctypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.TerminateJobObject(job, 1)
        kernel.CloseHandle(job)
        proc._cli_job = None
    if proc.poll() is not None:
        return
    if os.name == 'nt' and job is None:
        try:
            subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'], capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()
    elif os.name != 'nt':
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        if os.name != 'nt':
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        proc.kill()
        proc.wait(timeout=3)


def popen(args, **kwargs):
    if os.name == 'nt':
        kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs['start_new_session'] = True
    proc = subprocess.Popen(args, shell=False, **kwargs)
    if os.name == 'nt':
        # Job objects terminate descendants even if a child keeps stdout open or
        # the workbench closes unexpectedly. taskkill is only a fallback.
        import ctypes
        from ctypes import wintypes
        class Basic(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                        ('flags', wintypes.DWORD), ('min_working', ctypes.c_size_t),
                        ('max_working', ctypes.c_size_t), ('active', wintypes.DWORD),
                        ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD), ('scheduling', wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
        class Extended(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', IO), ('process_memory', ctypes.c_size_t),
                        ('job_memory', ctypes.c_size_t), ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = ctypes.c_void_p
        kernel.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        job = kernel.CreateJobObjectW(None, None)
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if job and kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)) and kernel.AssignProcessToJobObject(job, int(proc._handle)):
            proc._cli_job = job
        else:
            if job:
                kernel.CloseHandle(job)
            proc.kill()
            proc.wait()
            raise ValueError('Windows 无法建立视频任务进程组，请重启工作台后重试')
    return proc


def inspect_cli():
    command = cli_command()
    if not command:
        raise ValueError('未找到 Claude Code。请先安装并在终端登录，或在设置中填写 claude.exe 路径。')
    outputs = []
    for args in (['--version'], ['auth', 'status']):
        proc = popen(command + args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                     env=environment(), cwd=str(store.ROOT))
        try:
            stdout, stderr = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            terminate(proc)
            raise ValueError('Claude Code 环境检查超时，请先在终端运行 claude 检查安装与登录') from None
        finally:
            terminate(proc)
        outputs.append((proc.returncode, stdout.decode('utf-8', errors='replace'), stderr.decode('utf-8', errors='replace')))
    version = re.search(r'\b(\d+)\.(\d+)\.(\d+)\b', outputs[0][1])
    supported = bool(version and tuple(map(int, version.groups())) >= MIN_VERSION and outputs[0][0] == 0)
    try:
        auth = json.loads(outputs[1][1])
    except (json.JSONDecodeError, TypeError):
        auth = {}
    logged_in = outputs[1][0] == 0 and auth.get('loggedIn') is True
    result = {'at': store.now(), 'version': version[0] if version else None, 'supported': supported,
              'logged_in': logged_in, 'auth_method': auth.get('authMethod'),
              'message': '本机 Claude Code 已就绪' if supported and logged_in else
              '请将 Claude Code 更新到 2.1.248 或以上' if not supported else '请在终端运行 claude auth login 登录'}
    with config.LOCK:
        machine = config.read()
        machine['claude_cli_check'] = result
        config.write(machine)
    return status()


# Each project has one working folder, like a project opened in Claude Code. Uploaded
# materials are copied into its 素材 subfolder.
WORKDIR = 'claude'
MATERIALS = '素材'
VIDEO_SUFFIXES = {'.mp4', '.mov', '.m4v', '.mkv', '.webm', '.avi'}
SKIP_DIRS = {MATERIALS, '.claude', 'node_modules', '.git', '__pycache__', '.venv', 'venv'}


def material_name(asset, taken):
    """The uploaded file name, made safe and unique inside the materials folder."""
    source = asset_store.file(asset)
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', Path(str(asset.get('name') or '')).name).strip(' .') or asset['id']
    if Path(name).suffix.lower() != source.suffix.lower():
        name += source.suffix.lower()
    stem, suffix, n = Path(name).stem, Path(name).suffix, 2
    while name.casefold() in taken:
        name, n = f'{stem} ({n}){suffix}', n + 1
    taken.add(name.casefold())
    return name


def toolbox_python(event, report):
    """A persistent virtual environment the agent may install packages into."""
    venv = TOOLBOX / 'venv'
    python = venv / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.is_file():
        report('首次使用：正在准备制作工具环境')
        TOOLBOX.mkdir(parents=True, exist_ok=True)
        media.run([sys.executable, '-m', 'venv', str(venv)], event)
    for folder in ('cache', 'templates'):
        (TOOLBOX / folder).mkdir(parents=True, exist_ok=True)
    return python


def task_update(task, **values):
    from . import jobs
    with jobs.LOCK:
        current = store.get('task', task['id'])
        current.update(values)
        store.put('task', current, task['project_id'])


def copy_input(source, target, event):
    """Copy large media without blocking task cancellation for the entire file."""
    with source.open('rb') as incoming, target.open('wb') as outgoing:
        while True:
            if event.is_set():
                raise media.Cancelled()
            chunk = incoming.read(4 * 1024 * 1024)
            if not chunk:
                break
            outgoing.write(chunk)


def sync_materials(work, assets, event, report):
    own = [a for a in assets if not a.get('generated')]
    folder, taken = work / MATERIALS, set()
    if own:
        report('正在放入项目素材')
        folder.mkdir(exist_ok=True)
    for asset in own:
        if event.is_set():
            raise media.Cancelled()
        source, target = asset_store.file(asset), folder / material_name(asset, taken)
        if not (target.is_file() and target.stat().st_size == source.stat().st_size):
            copy_input(source, target, event)
    if folder.is_dir():
        # Keep the folder in step with the project: removed materials disappear here too.
        for stale in folder.iterdir():
            if stale.is_file() and stale.name.casefold() not in taken:
                stale.unlink()


def prepare(task, work, event, report, assets=None):
    work.mkdir(parents=True, exist_ok=True)
    if assets is not None:
        sync_materials(work, assets, event, report)
    toolbox_python(event, report)
    hook_args = [sys.executable, str(Path(__file__).with_name('cli_guard.py')), '--root', str(work), '--run', str(work),
                 '--toolbox', str(TOOLBOX), '--temp', tempfile.gettempdir(), '--protect-pid', str(os.getpid()),
                 '--workbench-python', sys.executable, '--project-env', str(store.ROOT / '.env')]
    runtime_settings = {'hooks': {'PreToolUse': [{'matcher': '*',
                         'hooks': [{'type': 'command', 'command': hook_args[0],
                                    'args': hook_args[1:], 'timeout': 10}]}]}}
    # Keep policy files outside the agent's writable directories.
    owner = store.project_dir(task['project_id']) if task.get('project_id') else store.DATA / 'inspiration'
    control = owner / 'agent-control' / task['id']
    control.mkdir(parents=True, exist_ok=True)
    (control / 'settings.json').write_text(json.dumps(runtime_settings), encoding='utf-8')
    (control / 'mcp.json').write_text('{"mcpServers":{}}', encoding='utf-8')
    task_update(task, cli_run_dir=WORKDIR, cli_workdir=WORKDIR)
    return control


class Observer:
    """What Claude did, shown next to its reply: the web pages it read."""

    def __init__(self):
        self.urls, self.tools = [], {}

    def collect(self, item):
        if item.get('type') not in ('assistant', 'user'):
            return
        for block in item.get('message', {}).get('content', []) or []:
            if not isinstance(block, dict):
                continue
            if block.get('type') == 'tool_use' and block.get('name') in ('WebSearch', 'WebFetch'):
                self.tools[block.get('id')] = (block['name'], block.get('input') or {})
            elif block.get('type') == 'tool_result' and not block.get('is_error'):
                name, args = self.tools.get(block.get('tool_use_id'), ('', {}))
                content = block.get('content', '')
                text = content if isinstance(content, str) else '\n'.join(b.get('text', '') for b in content if isinstance(b, dict))
                if not name or not text.strip():
                    continue
                found = [args['url']] if name == 'WebFetch' and args.get('url') else \
                    re.findall(r'https?://[^\s<>"\]。，]+', text) if name == 'WebSearch' else []
                for url in (str(u).rstrip('.,;:)') for u in found):
                    if url not in self.urls:
                        self.urls.append(url)


def execute_process(task, command, root, prompt, event, report, on_event=None):
    timeout = task['cli_config']['timeout_sec']
    proc = popen(command, cwd=str(root), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                 env=environment())
    messages = queue.Queue(maxsize=200)
    finished = threading.Event()
    def read(stream, label):
        try:
            while not finished.is_set():
                line = stream.readline(1024 * 1024 + 1)
                if not line:
                    break
                if len(line) > 1024 * 1024:
                    line = b'{"type":"oversized_event"}\n'
                    while True:
                        chunk = stream.readline(1024 * 1024 + 1)
                        if not chunk or chunk.endswith(b'\n') or finished.is_set():
                            break
                while not finished.is_set():
                    try:
                        messages.put((label, line.decode('utf-8', errors='replace')), timeout=.2)
                        break
                    except queue.Full:
                        pass
        finally:
            while not finished.is_set():
                try:
                    messages.put((label, None), timeout=.2)
                    break
                except queue.Full:
                    pass
    readers = [threading.Thread(target=read, args=(stream, label), daemon=True)
               for stream, label in ((proc.stdout, 'stdout'), (proc.stderr, 'stderr'))]
    for thread in readers:
        thread.start()
    def write_prompt():
        try:
            proc.stdin.write(prompt.encode('utf-8'))
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
    writer = threading.Thread(target=write_prompt, daemon=True)
    writer.start()
    started, closed, result, tail = time.monotonic(), set(), None, ''
    last_phase = None
    try:
        while len(closed) < 2:
            if event.is_set():
                raise media.Cancelled('Claude Code 任务已取消')
            if time.monotonic() - started > timeout:
                raise ValueError('Claude Code 任务超时；已保存任务上下文，可调整要求后重试')
            try:
                label, line = messages.get(timeout=.2)
            except queue.Empty:
                continue
            if line is None:
                closed.add(label)
                continue
            if label == 'stderr':
                tail = (tail + clean(line))[-6000:]
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                tail = (tail + clean(line))[-6000:]
                continue
            if not isinstance(item, dict):
                continue
            if on_event:
                on_event(item)
            session = item.get('session_id')
            if session:
                try:
                    task_update(task, cli_session_id=str(uuid.UUID(session)))
                except (ValueError, TypeError):
                    pass
            phase = None
            if item.get('type') == 'assistant':
                for block in item.get('message', {}).get('content', []):
                    if block.get('type') == 'tool_use':
                        name = block.get('name', '')
                        if name in ('WebSearch', 'WebFetch'):
                            phase = '正在搜索参考资料' if name == 'WebSearch' else '正在读取来源原文'
                        phase = {'Read': '正在查看文件与素材', 'Write': '正在写入文件', 'Edit': '正在修改文件',
                                 'Glob': '正在查找项目文件', 'Grep': '正在读取项目内容', 'Bash': '正在执行本地制作工具',
                                 'PowerShell': '正在执行本地制作工具', 'Task': '正在分派并行制作任务',
                                 'Agent': '正在分派并行制作任务', 'TodoWrite': '正在规划制作步骤'}.get(name, phase)
                        arg = block.get('input', {}).get('command', '')
                        if name in ('Bash', 'PowerShell'):
                            phase = '正在安装制作依赖' if re.search(r'(pip|npm|pnpm|yarn).*(install|i|add)', arg) else \
                                    '正在渲染动画' if re.search(r'remotion|playwright|puppeteer|node', arg) else \
                                    '正在检查视频信息' if 'ffprobe' in arg or ' probe ' in arg else \
                                    '正在抽取检查画面' if 'frames' in arg else \
                                    '正在渲染或检查视频' if 'ffmpeg' in arg else phase
            elif item.get('type') == 'system' and item.get('subtype') == 'init':
                phase = 'Claude Code 已启动，正在理解要求'
            elif item.get('type') == 'system' and item.get('subtype') == 'api_retry':
                phase = 'Claude 服务暂未响应，正在重试连接'
            elif item.get('type') == 'result':
                result = item
            if phase and phase != last_phase:
                report(phase)
                last_phase = phase
        # A detached child must not keep the task marked completed while CLI is alive.
        while proc.poll() is None:
            if event.wait(.1):
                raise media.Cancelled()
            if time.monotonic() - started > timeout:
                raise ValueError('Claude Code 退出超时')
        if result:
            record_result(task, result)
        if proc.returncode or not result or result.get('is_error') or result.get('subtype') != 'success':
            detail = result.get('result') or '; '.join(map(str, result.get('errors', []))) if result else tail
            raise ValueError('Claude Code 没有完成：' + clean(detail or '未收到成功的执行结果')[:2000])
        return result
    finally:
        finished.set()
        terminate(proc)
        for thread in readers:
            thread.join(timeout=1)
        writer.join(timeout=1)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if not stream.closed:
                stream.close()


def record_result(task, result):
    # CLI costs are provider estimates and may describe a resumed conversation total.
    # Keep them separate from the workbench's API-key budget and token prices.
    values = {'usage': result.get('usage', {}), 'model_usage': result.get('modelUsage', {}),
              'reported_cost_usd': result.get('total_cost_usd'), 'num_turns': result.get('num_turns'),
              'duration_ms': result.get('duration_ms'), 'permission_denials': len(result.get('permission_denials', []))}
    task_update(task, cli_result=values)


def changed_files(work, since):
    """Files Claude wrote or rewrote during this run, newest first."""
    found = []
    for folder, dirs, files in os.walk(work):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            path = Path(folder) / name
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if mtime >= since - 2:
                found.append((mtime, path.relative_to(work).as_posix()))
    return [p for _, p in sorted(found, reverse=True)]


def run(task, work, prompt, event, report, resume=None, assets=None):
    """Send one prompt to Claude Code in `work`. Nothing is added to the prompt."""
    settings = task['cli_config']
    command = cli_command(settings)
    if not command:
        raise ValueError('未找到 Claude Code，请在设置中检测本机 CLI')
    control = prepare(task, work, event, report, assets)
    # dontAsk denies anything not approved; the PreToolUse hook approves all but destructive actions.
    args = command + ['-p', '--output-format', 'stream-json', '--verbose', '--model', settings['model'],
                      '--max-turns', str(settings['max_turns']), '--permission-mode', 'dontAsk',
                      '--tools', 'default', '--settings', str(control / 'settings.json'),
                      '--strict-mcp-config', '--mcp-config', str(control / 'mcp.json')]
    if resume:
        args += ['--resume', resume, '--fork-session']
    report('正在启动本机 Claude Code')
    observer, started, failure, outcome = Observer(), time.time(), None, None
    try:
        outcome = execute_process(task, args, work, prompt, event, report, on_event=observer.collect)
    except ValueError as exc:
        # A run that stops early may still have produced files worth keeping.
        failure = exc
    if event.is_set():
        raise media.Cancelled()
    return {'reply': clean((outcome or {}).get('result') or '').strip(), 'failure': failure,
            'files': changed_files(work, started), 'web': observer.urls}


def save_video(task, source, work, summary, event, report):
    """Copy Claude's video into the version store, converting it only if browsers cannot play it."""
    report('正在保存视频')
    ffmpeg, project_root = media.executable('ffmpeg'), store.project_dir(task['project_id']).resolve()
    probe = media.probe(source, event)
    if not probe['has_video'] or not math.isfinite(probe['duration']):
        raise ValueError('Claude Code 生成的视频文件无法读取：' + source.name)
    visual = next(s for s in probe['streams'] if s['codec_type'] == 'video')
    playable = (source.suffix.lower() in ('.mp4', '.m4v', '.mov') and visual.get('codec_name') == 'h264'
                and visual.get('pix_fmt') == 'yuv420p'
                and all(s.get('codec_name') == 'aac' for s in probe['streams'] if s['codec_type'] == 'audio'))
    target = store.project_file(task['project_id'], f'videos/{task["id"]}/video.mp4')
    target.parent.mkdir(parents=True, exist_ok=True)
    streams = ['-map', '0:v:0', '-map', '0:a?']
    if playable:
        media.run([ffmpeg, '-y', '-v', 'error', '-i', str(source), *streams, '-c', 'copy', '-movflags', '+faststart', str(target)], event)
    else:
        report('正在转换为网页可播放的格式')
        media.run([ffmpeg, '-y', '-v', 'error', '-i', str(source), *streams, '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2',
                   *media.video_encoding(final=True), '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k',
                   '-movflags', '+faststart', str(target)], event)
    media.run([ffmpeg, '-v', 'error', '-xerror', '-i', str(target), '-f', 'null', '-'], event)
    probe = media.probe(target, event)
    visual = next(s for s in probe['streams'] if s['codec_type'] == 'video')
    ratio = visual.get('width', 0) / max(1, visual.get('height', 0))
    shapes = {'9:16': 9/16, '16:9': 16/9, '1:1': 1}
    notes = ['Claude 生成的文件：' + WORKDIR + '/' + source.relative_to(work).as_posix()]
    if not playable:
        notes.append('原文件不是网页可播放的 H.264 MP4，工作台已转换一份；原文件仍在 Claude 的工作文件夹里。')
    result = {'engine': 'claude_cli', 'summary': (clean(summary).strip() or 'Claude Code 已完成视频制作')[:3000],
              'aspect': min(shapes, key=lambda k: abs(shapes[k] - ratio)), 'duration': probe['duration'],
              'segments': [], 'captions': [], 'has_recorded_audio': probe['has_audio'], 'notes': notes}
    artifacts = {'video': target.relative_to(project_root).as_posix(),
                 'checks': {'decodable': True, 'duration': probe['duration'], 'codec': 'h264', 'has_audio': probe['has_audio']}}
    return result, artifacts


def keep_video(task, source, work, summary, notes, event, report):
    result, artifacts = save_video(task, source, work, summary, event, report)
    result['notes'] = notes + result['notes']
    from . import jobs
    current = store.get('task', task['id'])
    with jobs.LOCK:
        if event.is_set():
            raise media.Cancelled()
        v = store.put('version', {'stage': 'edit', 'result': result, 'prompt': task['payload']['prompt'],
                      'task_id': task['id'], 'preview': artifacts, 'final': artifacts,
                      'cli_session_id': current.get('cli_session_id'), 'cli_result': current.get('cli_result'),
                      'cli_run_dir': WORKDIR, 'cli_workdir': WORKDIR,
                      'model_config': {'engine': 'claude_cli', 'model': task['cli_config']['model']}}, task['project_id'])
        p = store.get('project', task['project_id'])
        p.setdefault('adopted', {})['edit'] = v['id']
        store.put('project', p)
    return v


def execute(task, event, report):
    """A project run: the prompt goes to Claude; a new video also becomes a playable version."""
    work = store.project_file(task['project_id'], WORKDIR)
    result = run(task, work, task['payload']['prompt'], event, report, task.get('resume_session'), task['asset_snapshot'])
    failure, warnings = result.pop('failure'), []
    if failure and not result['files']:
        raise failure
    if failure:
        warnings.append('Claude Code 没有正常结束（' + clean(str(failure))[:300] + '），这些是它停止前留下的内容，请检查是否完整。')
    videos = [p for p in result['files'] if Path(p).suffix.lower() in VIDEO_SUFFIXES]
    version = None
    if videos and media.executable('ffmpeg') and media.executable('ffprobe'):
        try:
            version = keep_video(task, work / videos[0], work, result['reply'], list(warnings), event, report)
        except ValueError as exc:
            warnings.append(f'生成的视频 {videos[0]} 无法保存为版本：' + clean(str(exc))[:300])
    elif videos:
        warnings.append('还没有配置 FFmpeg，视频留在工作文件夹里，没有保存为可播放的版本。')
    return {**result, 'files': result['files'][:200], 'web': result['web'][:50], 'warnings': warnings,
            'version_id': version['id'] if version else None}
