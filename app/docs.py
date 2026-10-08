"""The project's hand-off files: 我的idea.md, 素材.md, 调研.md and 文案.md.

Each module keeps its result in one file so another module can reference it. They live
in the folder Claude Code makes videos in, so a video run can open them by name; research,
writing and polishing runs get the referenced text pasted into the prompt instead. The
creator can edit any file; the platform then stops overwriting it from new results.
素材.md is the exception: it is written from the project's material list, which is where
the creator says what each material is for.
"""
from . import models, store
from .claude_cli import WORKDIR

NAMES = {'idea': '我的idea.md', 'materials': '素材.md', 'research': '调研.md', 'script': '文案.md'}
MODULES = ('polish', 'research', 'script', 'video')
# What each module references unless the creator unticks it.
DEFAULT_REFS = {'polish': (), 'research': ('idea',), 'script': ('idea', 'research'), 'video': ('script', 'materials')}
# Polishing the idea may only look at the materials; the other files come after it.
ALLOWED_REFS = {'polish': ('materials',)}
PURPOSES = {'edit': '剪辑素材', 'reference': '参考', 'music': '配乐', 'other': '其他'}
TYPE_NAMES = {'Video': '视频', 'Image': '图片', 'Audio': '音频', 'Music': '音乐'}
# Guides a video run can be pointed to, one md file each. Claude may read them but not change them.
ABILITY_DIR = store.ROOT / 'agent-ability'


def path(project_id, key):
    folder = store.project_file(project_id, WORKDIR)
    folder.mkdir(parents=True, exist_ok=True)
    return folder / NAMES[key]


def read(project_id, key):
    file = path(project_id, key)
    return file.read_text(encoding='utf-8') if file.is_file() else ''


def meta(project):
    return project.setdefault('workspace', {}).setdefault('docs', {})


def write(project, key, text, origin):
    """origin: manual (the creator wrote it), brainstorm, polish, chat, or research:/script:<version id>.
    The caller holds jobs.LOCK and saves the project afterwards."""
    previous = read(project['id'], key)
    path(project['id'], key).write_text(text, encoding='utf-8')
    entry = meta(project).get(key, {})
    meta(project)[key] = {'origin': origin, 'updated_at': store.now(),
                          'previous': previous if previous != text else entry.get('previous', '')}


def undo(project, key):
    entry = meta(project).get(key, {})
    if not entry.get('previous') and not read(project['id'], key):
        raise ValueError('没有可以撤销的内容')
    current = read(project['id'], key)
    path(project['id'], key).write_text(entry.get('previous', ''), encoding='utf-8')
    meta(project)[key] = {'origin': 'manual', 'updated_at': store.now(), 'previous': current}


def sync(project, stage, version):
    """Keep 调研.md / 文案.md in step with the newest result, unless the creator edited the file."""
    entry = meta(project).get(stage, {})
    if entry.get('origin') == 'manual' and read(project['id'], stage).strip():
        entry['pending'] = version['id']
        meta(project)[stage] = entry
        return False
    render = render_research if stage == 'research' else render_script
    write(project, stage, render(version['result']), f"{stage}:{version['id']}")
    return True


def status(project):
    result = {}
    for key, name in NAMES.items():
        entry = meta(project).get(key, {})
        result[key] = {'name': name, 'text': read(project['id'], key), 'origin': entry.get('origin'),
                       'updated_at': entry.get('updated_at'), 'can_undo': bool(entry.get('previous')),
                       'pending': entry.get('pending'), 'generated': key == 'materials'}
    return result


def purpose(asset):
    from .assets import asset_type
    return asset.get('purpose') or ('music' if asset_type(asset) == 'Music' else 'edit')


def materials(project, assets=None):
    """The project's own materials in the creator's order; new ones go last."""
    own = [a for a in (store.listing('asset', project['id']) if assets is None else assets) if not a.get('generated')]
    own.sort(key=lambda a: a['created_at'])
    order = {ident: i for i, ident in enumerate(project.get('workspace', {}).get('material_order', []))}
    return sorted(own, key=lambda a: order.get(a['id'], len(order)))


def material_names(assets):
    """The file name each material gets in the 素材 folder, the same for 素材.md and the copies."""
    from .claude_cli import material_name
    taken = set()
    return {a['id']: material_name(a, taken) for a in assets}


def _length(seconds):
    seconds = int(seconds or 0)  # Whole seconds, as the workbench shows them.
    return f'{seconds // 60}:{seconds % 60:02d}'


def render_materials(project, assets=None):
    from .assets import asset_type
    own = materials(project, assets)
    if not own:
        return ''
    names = material_names(own)
    fixed = project.get('workspace', {}).get('material_sequence') == 'fixed'
    lines = ['# 素材', '', '制作视频时，这些文件在 Claude 工作文件夹的 素材/ 里。']
    for key, title in (('edit', '剪辑素材'), ('reference', '参考（不剪进成片）'), ('music', '配乐'), ('other', '其他')):
        group = [a for a in own if purpose(a) == key]
        if not group:
            continue
        lines += ['', f'## {title}']
        if key == 'edit':
            lines.append('剪辑顺序：' + ('按下面的顺序' if fixed else '由你决定'))
        for index, a in enumerate(group, 1):
            duration = (a.get('analysis') or {}).get('duration') or a.get('duration')
            kind = TYPE_NAMES.get(asset_type(a), '') + (f' · {_length(duration)}' if duration and asset_type(a) != 'Image' else '')
            label = f"用途：{a['purpose_label']}。" if key == 'other' and a.get('purpose_label') else ''
            note = (a.get('note') or '').strip()
            lead = f'{index}.' if key == 'edit' and fixed else '-'
            lines.append(f"{lead} {names[a['id']]}（{kind}）" + (f' {label}{note}' if label or note else ''))
    return '\n'.join(lines) + '\n'


def refresh_materials(project):
    """Rewrite 素材.md from the material list. The caller holds jobs.LOCK and saves the project."""
    text = render_materials(project)
    if text != read(project['id'], 'materials'):
        path(project['id'], 'materials').write_text(text, encoding='utf-8')
        meta(project)['materials'] = {'origin': 'materials', 'updated_at': store.now()}


def _list(lines, label, items):
    items = [str(i).strip() for i in items or [] if str(i).strip()]
    if items:
        lines += ['', f'**{label}**', *[f'- {i}' for i in items]]


def render_research(result):
    topics = result.get('topics') or []
    lines = ['# 调研']
    if result.get('summary'):
        lines += ['', str(result['summary'])]
    for index, topic in enumerate(topics, 1):
        lines += ['', f"## {f'选题 {index}：' if len(topics) > 1 else ''}{topic.get('title', '')}"]
        for label, key in (('问题', 'question'), ('发生了什么', 'what_happened'), ('为什么值得讲', 'why_now'),
                           ('内容空缺', 'content_gap')):
            if topic.get(key):
                lines += ['', f'**{label}**：{topic[key]}']
        facts = []
        for fact in topic.get('key_facts') or []:
            claim = fact.get('claim') if isinstance(fact, dict) else fact
            urls = fact.get('source_urls', []) if isinstance(fact, dict) else []
            facts.append(str(claim) + (f"（来源：{' '.join(urls)}）" if urls else ''))
        _list(lines, '关键事实', facts)
        _list(lines, '常见观点', topic.get('narratives'))
        _list(lines, '观众反应', topic.get('audience_reactions'))
        for number, angle in enumerate(topic.get('angles') or [], 1):
            if not isinstance(angle, dict):
                angle = {'title': str(angle)}
            lines += ['', f"### 角度 {number}：{angle.get('title', '')}"]
            for label, key in (('为什么有效', 'reason'), ('讲给谁', 'audience'), ('开场', 'hook'), ('不同之处', 'difference')):
                if angle.get(key):
                    lines.append(f'- {label}：{angle[key]}')
        _list(lines, '待核实', topic.get('verify'))
    sources = [s for s in result.get('sources') or [] if s.get('url')]
    if sources:
        lines += ['', '## 来源']
        for s in sources:
            note = f" — {s['evidence_note']}" if s.get('evidence_note') else ''
            lines.append(f"- [{s.get('title') or s['url']}]({s['url']})（{s.get('verification', 'unverified')}）{note}")
    return '\n'.join(lines) + '\n'


BEATS = {'hook': '开场钩子', 'context': '引入', 'evidence': '关键论据', 'turn': '转折', 'cta': '收尾'}


def render_script(result):
    paragraphs = result.get('paragraphs') or []
    lines = ['# 文案']
    if result.get('angle'):
        lines += ['', f"角度：{result['angle']}"]
    lines += ['', f"约 {round(result.get('estimated_sec') or 0)} 秒 · {len(paragraphs)} 段"]
    for index, p in enumerate(paragraphs, 1):
        beat = BEATS.get(p.get('beat'), '')
        lines += ['', f"## {index:02d}{' ' + beat if beat else ''} · {p.get('speaker') or '旁白'}", '', str(p.get('text', ''))]
        if p.get('cue'):
            lines.append(f"- 画面：{p['cue']}")
        if p.get('source_urls'):
            lines.append(f"- 来源：{' '.join(p['source_urls'])}")
    publishing = result.get('publishing') or {}
    if publishing.get('titles'):
        _list(lines, '标题备选', publishing['titles'])
    return '\n'.join(lines) + '\n'


def default_request(project, module):
    req = project['defaults'] | project.get('requirements', {})
    shape = {'9:16': '竖屏', '16:9': '横屏', '1:1': '方形'}.get(req.get('aspect'), '')
    if module == 'polish':
        return ('把下面这些零散的想法整理成一份清楚的创作想法。保留我的原意和具体细节，不要编造我没说过的经历或事实；'
                '理清想讲什么、给谁看、想让观众得到什么、大概的语气和形式；我还没想清楚的地方单独列在「待确定」里。')
    if module == 'research':
        lead = '' if read(project['id'], 'idea').strip() else f"我的想法：{project.get('workspace', {}).get('idea') or project['name']}\n"
        return lead + ('围绕我的想法，调研适合做成短视频的内容：提炼事实、不同观点、观众反应和内容空缺，'
                       '给出三个具体可拍的角度（讲给谁、怎么开场、和已有内容有什么不同）。只列实际取得的来源。')
    if module == 'script':
        return f"写一条约 {req.get('duration', 60)} 秒的{shape}短视频文案：开场抓人，推进清楚，结尾自然。重要事实标出调研里的来源。"
    return ''


def default_refs(project, module):
    return [key for key in DEFAULT_REFS[module] if read(project['id'], key).strip()]


def abilities():
    """The guides in agent-ability/, keyed by file name; each is named by its first heading."""
    found = {}
    for file in sorted(ABILITY_DIR.glob('*.md')):
        first = next((line for line in file.read_text(encoding='utf-8').splitlines() if line.strip()), '')
        found[file.stem] = first.lstrip('#').strip() if first.startswith('#') else file.stem
    return found


def compose(project, module, request, refs, brainstorm='', chosen=()):
    """The complete text a run sends to Claude. The creator sees and may edit it before sending."""
    if module not in MODULES:
        raise ValueError('不支持的模块')
    pid = project['id']
    attached = [key for key in NAMES if key in refs and read(pid, key).strip()]
    if module == 'video':
        # Claude Code runs in the folder that holds the files; naming them is enough.
        names = '、'.join(NAMES[k] for k in attached)
        text = request.strip() + (f'\n\n参考当前文件夹里的 {names}。' if names else '')
        # A guide removed from agent-ability/ is skipped. They live outside Claude's folder, so they are named by full path.
        known = abilities()
        guides = [f'{known[k]}：先读 {(ABILITY_DIR / (k + ".md")).as_posix()}，按里面的做法做。' for k in known if k in chosen]
        return text + ('\n\n' + '\n'.join(guides) if guides else '')
    mode = 'idea' if module == 'polish' else module
    parts = [models.SYSTEM + models.SCHEMAS[mode] + ('\n必须使用实际联网工具，无法访问时如实说明；禁止编造来源、日期和互动数据。'
                                                     if module == 'research' else '\n本次不联网；禁止编造来源、日期和互动数据。'),
             '【这次的要求】\n' + request.strip()]
    if module == 'polish':
        parts.append('【我的零散想法】\n' + (brainstorm.strip() or '（还没有写）'))
    parts += [f'【参考：{NAMES[k]}】\n{read(pid, k).strip()}' for k in attached]
    return '\n\n'.join(parts)
