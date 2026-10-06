"""Claude Code PreToolUse policy: allow by default, block only destructive or credential-exposing actions.

This is a guard against mistakes, not an OS sandbox. Code the agent writes to a script
and then runs is not inspected.
"""
import argparse
import json
import re
import shlex
import sys
import tempfile
from pathlib import Path

# Commands that change the machine rather than the video project.
SYSTEM = {'format', 'diskpart', 'mkfs', 'fdisk', 'shutdown', 'reboot', 'poweroff', 'halt', 'logoff',
          'bcdedit', 'bootrec', 'vssadmin', 'wbadmin', 'cipher', 'takeown', 'icacls', 'cacls', 'reg',
          'regedit', 'schtasks', 'sc', 'net', 'netsh', 'wmic', 'setx', 'runas', 'sudo', 'chown',
          'stop-computer', 'restart-computer', 'format-volume', 'clear-disk', 'initialize-disk',
          'remove-partition', 'set-executionpolicy', 'set-itemproperty', 'new-itemproperty',
          'remove-itemproperty', 'killall', 'pkill', 'crontab', 'gh'}
DELETE = {'rm', 'rmdir', 'del', 'erase', 'rd', 'unlink', 'shred', 'remove-item', 'ri', 'mv', 'move',
          'move-item', 'mi', 'ren', 'rename', 'rename-item', 'rni', 'clear-content', 'clc', 'robocopy'}
WRAPPERS = {'env', 'exec', 'time', 'nohup', 'xargs', 'call', 'start', 'command', 'nice', 'timeout', '&', '.'}
# Credential stores and workbench state. Media and project files remain readable.
SENSITIVE = [re.compile(p, re.I) for p in (
    r'(^|[\\/])\.ssh([\\/]|$)', r'(^|[\\/])\.aws([\\/]|$)', r'(^|[\\/])\.gnupg([\\/]|$)', r'(^|[\\/])\.azure([\\/]|$)',
    r'\.credentials\.json', r'(^|[\\/])\.netrc$', r'(^|[\\/])\.git-credentials$', r'(^|[\\/])\.npmrc$', r'(^|[\\/])\.pypirc$',
    r'machine-config\.json', r'workbench\.sqlite3', r'[\\/]Login Data', r'[\\/]Cookies', r'[\\/]Local State$',
    r'[\\/]Microsoft[\\/](Credentials|Protect|Vault)')]


def within(path, root):
    return path.resolve().is_relative_to(root.resolve())


def to_path(value, cwd, temp):
    """Resolve Windows, Git Bash (/c/...) and relative spellings to an absolute path."""
    value = value.strip().strip('"\'')
    if match := re.match(r'^/([a-zA-Z])(/|$)', value):
        value = match[1] + ':/' + value[3:]
    elif value == '/tmp' or value.startswith('/tmp/'):
        value = str(temp) + value[4:]
    if value.startswith('~'):
        value = str(Path.home()) + value[1:]
    return (Path(cwd) / value).resolve()


def sensitive(text, project_env):
    normalized = text.replace('/', '\\')
    return any(p.search(text) or p.search(normalized) for p in SENSITIVE) or \
        (project_env and project_env.casefold() in normalized.casefold())


def split_outside_quotes(command):
    parts, current, quote, i = [], '', None, 0
    while i < len(command):
        char = command[i]
        if quote:
            current += char
            if char == quote:
                quote = None
            elif char == '$' and command[i+1:i+2] == '(' and quote == '"':
                # Command substitution still runs inside double quotes.
                parts.append(current)
                current, i = '', i + 1
        elif char in '"\'':
            quote, current = char, current + char
        elif char in ';|&\n\r`(){}':
            parts.append(current)
            current = ''
        else:
            current += char
        i += 1
    return parts + [current]


def segments(command):
    """Split a shell line into simple commands; good enough to find each command word."""
    for part in split_outside_quotes(command):
        # Windows paths use backslashes; POSIX escaping would otherwise turn C:\x\y into C:xy.
        part = part.strip().replace('\\', '/')
        if not part:
            continue
        try:
            lexer = shlex.shlex(part, posix=True, punctuation_chars='<>')
            lexer.whitespace_split = True
            tokens = list(lexer)
        except ValueError:
            tokens = part.split()
        if tokens:
            yield tokens


def command_word(tokens):
    tokens = list(tokens)
    while tokens:
        word = Path(tokens[0].strip('"\'')).name.casefold()
        word = re.sub(r'\.(exe|cmd|bat|com|ps1)$', '', word)
        if word in WRAPPERS or re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*=.*', tokens[0]):
            tokens = tokens[1:]
            continue
        if word in ('cmd', 'powershell', 'pwsh', 'bash', 'sh') and len(tokens) > 2 and tokens[1].casefold() in ('/c', '-c', '-command', '/k'):
            # Inline scripts are inspected as their own command lines.
            return 'shell', tokens[2:]
        return word, tokens[1:]
    return '', []


def check_command(command, cwd, roots, temp, protect_pid, venv_python, project_env):
    if sensitive(command, project_env):
        return False, '该命令涉及凭据或工作台私有数据，已拦截'
    if re.search(r'git\s+push|npm\s+publish|twine\s+upload|gh\s+(release|pr|issue|repo)', command, re.I):
        return False, '该命令会向外部发布内容，已拦截'
    for tokens in segments(command):
        word, args = command_word(tokens)
        if word == 'shell':
            ok, reason = check_command(' '.join(args), cwd, roots, temp, protect_pid, venv_python, project_env)
            if not ok:
                return ok, reason
            continue
        lowered = [a.casefold() for a in args]
        if word in ('cd', 'set-location', 'sl', 'pushd', 'chdir') and args:
            cwd = to_path(args[-1], cwd, temp)
            continue
        if word in SYSTEM or word.startswith('mkfs'):
            return False, f'「{word}」会修改系统设置，已拦截'
        if word == 'dd' and any(a.startswith('of=') for a in args):
            return False, 'dd 写入设备，已拦截'
        if word in ('taskkill', 'stop-process', 'kill', 'spps'):
            if any(a in ('/im', '-name', '-processname') for a in lowered) or str(protect_pid) in args:
                return False, '只能按 PID 结束本次任务自己启动的进程'
            continue
        if word in ('npm', 'pnpm', 'yarn') and any(a in ('-g', '--global', '--location=global') for a in lowered):
            return False, '请在 toolbox 或任务目录中本地安装依赖，不要全局安装'
        if word in ('pip', 'pip3') and '--user' in lowered:
            return False, '请使用 toolbox 的 Python 安装依赖'
        if venv_python and tokens and to_path(tokens[0], cwd, temp) == Path(venv_python).resolve() and 'pip' in lowered:
            return False, '工作台自身的 Python 环境不能修改，请使用 toolbox 的 Python'
        if word == 'git' and lowered[:1] and lowered[0] in ('clean', 'reset', 'checkout', 'restore'):
            if not within(cwd, roots[0]):
                return False, 'git 清理或还原只能在本次任务目录内执行'
        deleting = word in DELETE or (word == 'find' and any(a in ('-delete', '-exec', '-execdir') and
                                                             (a == '-delete' or 'rm' in lowered) for a in lowered))
        if deleting:
            paths = [a for a in args if not a.startswith('-') and not re.fullmatch(r'/[A-Za-z?]+(:\S*)?', a)
                     and a not in ('{}', '/;', ';', '+')]
            # Moves are checked like deletions: they remove the source and may overwrite the target.
            if not paths:
                paths = ['.']
            for value in paths:
                if any(c in value for c in '$%'):
                    return False, '删除或移动命令请使用明确的路径，不使用变量'
                if not any(within(to_path(value, cwd, temp), root) for root in roots):
                    return False, '只能删除或移动本次任务目录、toolbox 或临时目录中的文件'
        for i, token in enumerate(tokens[:-1]):
            if token in ('>', '>>') and not any(within(to_path(tokens[i+1], cwd, temp), root) for root in roots) \
                    and tokens[i+1].casefold() not in ('/dev/null', 'nul', '$null'):
                return False, '重定向输出只能写入本次任务目录、toolbox 或临时目录'
    return True, '允许'


def decide(event, root, run, executables, toolbox=None, temp=None, protect_pid=None, project_env=None):
    name, args = event.get('tool_name'), event.get('tool_input') or {}
    root, run = Path(root).resolve(), Path(run).resolve()
    # Only directories passed explicitly are writable; /tmp spellings still resolve to the system temp.
    roots = [run] + [Path(p).resolve() for p in (toolbox, temp) if p]
    temp = Path(temp or tempfile.gettempdir())
    cwd = Path(event.get('cwd') or root).resolve()
    # Copies of the user's uploads; the workbench refreshes them before each run.
    protected = [run / '素材']
    if name in ('Read', 'Write', 'Edit', 'MultiEdit', 'NotebookEdit', 'Glob', 'Grep', 'LS'):
        value = args.get('file_path') or args.get('notebook_path') or args.get('path') or str(cwd)
        target = to_path(str(value), cwd, temp)
        if sensitive(str(target), project_env):
            return False, '该文件包含凭据或工作台私有数据，不能访问'
        if name in ('Write', 'Edit', 'MultiEdit', 'NotebookEdit'):
            if any(target == p or within(target, p) for p in protected):
                return False, '素材文件夹是用户素材的副本，保持只读，请把产物写到其他位置'
            if not any(within(target, r) for r in roots):
                return False, '只能写入本次任务目录、toolbox 或临时目录'
        return True, '文件操作'
    if name in ('Bash', 'PowerShell'):
        command = args.get('command', '')
        if not isinstance(command, str):
            return False, '命令格式不正确'
        return check_command(command, cwd, roots, temp, protect_pid, executables.get('workbench_python'), project_env)
    # Web, agents, todos, skills and other tools are unrestricted.
    return True, '允许'


def main():
    sys.stdin.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    for key in ('root', 'run'):
        parser.add_argument('--' + key, required=True)
    for key in ('toolbox', 'temp', 'protect-pid', 'workbench-python', 'project-env'):
        parser.add_argument('--' + key)
    args = parser.parse_args()
    try:
        event = json.load(sys.stdin)
        ok, reason = decide(event, args.root, args.run, {'workbench_python': args.workbench_python},
                            args.toolbox, args.temp, args.protect_pid, args.project_env)
    except Exception:
        ok, reason = False, '无法验证工具输入，已停止此次工具操作'
    print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse',
          'permissionDecision': 'allow' if ok else 'deny', 'permissionDecisionReason': reason}}))


if __name__ == '__main__':
    main()
