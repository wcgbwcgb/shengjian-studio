import hashlib
import json
import os
import shutil
import subprocess
import time
from functools import lru_cache
from pathlib import Path

from . import config, store
from .timeline import srt, validate


class Cancelled(Exception):
    pass


def executable(name):
    configured = config.read().get(name + '_path') or os.getenv(name.upper() + '_PATH', name)
    found = shutil.which(configured) or (configured if Path(configured).is_file() else None)
    if found:
        return found
    suffix = '.exe' if os.name == 'nt' else ''
    for folder in (store.ROOT / '.runtime' / 'ffmpeg' / 'bin', store.ROOT / '.runtime' / 'bin'):
        candidate = folder / (name + suffix)
        if candidate.is_file():
            return str(candidate)
    return None


def environment():
    return {'service': 'music-studio', 'ffmpeg': bool(executable('ffmpeg')), 'ffprobe': bool(executable('ffprobe')),
            'transcription': False,
            'api_configured': config.has_api_key(),
            'font': font_path() is not None}


def font_path():
    candidates = [os.getenv('MEDIA_FONT_PATH', ''), 'C:/Windows/Fonts/msyh.ttc',
                  'C:/Windows/Fonts/simhei.ttf', '/System/Library/Fonts/PingFang.ttc',
                  '/System/Library/Fonts/Hiragino Sans GB.ttc', '/System/Library/Fonts/STHeiti Medium.ttc',
                  '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc']
    return next((p for p in candidates if p and Path(p).is_file()), None)


def run(args, cancel, cwd=None):
    if cancel.is_set():
        raise Cancelled('任务已取消')
    # Use a temporary file rather than a pipe: large FFmpeg stderr must never deadlock.
    import tempfile
    with tempfile.TemporaryFile() as log:
        proc = subprocess.Popen(args, cwd=cwd, stdout=log, stderr=log,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        while proc.poll() is None:
            if cancel.wait(0.2):
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                raise Cancelled('任务已取消；已生成文件及 API 用量保留')
        log.seek(0)
        output = log.read().decode('utf-8', errors='replace')
    if proc.returncode:
        raise ValueError('本地处理失败：' + output[-2500:])
    return output


@lru_cache(maxsize=8)
def available_video_encoder(ffmpeg):
    encoders = run([ffmpeg, '-hide_banner', '-encoders'], __import__('threading').Event())
    for name in ('libx264', 'libopenh264', 'libo264rt'):
        if name in encoders:
            return name
    raise ValueError('本地视频工具缺少 H.264 编码器，请在高级设置中选择完整版本。')


def video_encoding(final=False):
    encoder = available_video_encoder(executable('ffmpeg'))
    if encoder == 'libx264':
        return ['-c:v', encoder, '-preset', 'veryfast', '-crf', '20' if final else '28']
    return ['-c:v', encoder, '-b:v', '6000k' if final else '1600k']


@lru_cache(maxsize=8)
def has_ass_filter(ffmpeg):
    return 'Unknown filter' not in run([ffmpeg, '-hide_banner', '-h', 'filter=ass'], __import__('threading').Event())


@lru_cache(maxsize=8)
def has_png(ffmpeg):
    return all(run([ffmpeg, '-hide_banner', '-h', f'{kind}=png'], __import__('threading').Event()).lstrip().startswith(label)
               for kind, label in [('decoder', 'Decoder png '), ('encoder', 'Encoder png ')])


def convert_image(source, target, cancel):
    if os.name != 'nt':
        raise ValueError('视频工具缺少图片支持，请选择完整版本')
    run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(store.ROOT / 'scripts' / 'convert-image.ps1'),
         '-Source', str(source), '-Target', str(target)], cancel)


def text_overlays(folder, captions, width, height, cancel, title=False, card=False):
    """Windows fallback for minimal FFmpeg builds without libass; no extra package."""
    if os.name != 'nt':
        raise ValueError('当前视频工具不支持字幕，请安装带 libass 的 FFmpeg 完整版本')
    items = []
    for i, caption in enumerate(captions):
        items.append({'text': caption['text'], 'path': str(folder / f'text-{i}.bgra'),
                      'font_size': round(width / (15 if title or card else 24)),
                      'y': round(height * (.28 if card else .14 if title else .74)),
                      'height': round(height * (.42 if card else .17))})
    manifest = folder / 'text-manifest.json'
    manifest.write_text(json.dumps({'width': width, 'height': height, 'items': items}, ensure_ascii=False), encoding='utf-8')
    run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(store.ROOT / 'scripts' / 'render-text.ps1'),
         '-Manifest', str(manifest)], cancel)
    inputs, filters = [], []
    for i, (item, caption) in enumerate(zip(items, captions)):
        inputs += ['-f', 'rawvideo', '-pixel_format', 'bgra', '-video_size', f'{width}x{height}', '-i', item['path']]
        before = '[0:v]' if i == 0 else f'[text{i}]'
        filters.append(f"{before}[{i+1}:v]overlay=0:0:enable='gte(t,{caption['start']})*lt(t,{caption['end']})'[text{i+1}]")
    return inputs, ';'.join(filters), f'[text{len(items)}]'


def probe(path, cancel):
    ffprobe = executable('ffprobe')
    if not ffprobe:
        raise ValueError('未找到 FFprobe，请安装 FFmpeg 并配置 FFPROBE_PATH')
    result = json.loads(run([ffprobe, '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)], cancel))
    streams = result.get('streams', [])
    if not any(s['codec_type'] in ('audio', 'video') for s in streams):
        raise ValueError('素材没有可读取的音视频流')
    duration = float(result.get('format', {}).get('duration', 0))
    if duration <= 0:
        raise ValueError('无法确定素材时长')
    return {'duration': duration, 'streams': streams, 'has_video': any(s['codec_type'] == 'video' for s in streams),
            'has_audio': any(s['codec_type'] == 'audio' for s in streams)}


def analyze(asset, cancel, phase, transcribe=False):
    from . import assets as asset_store
    asset = asset_store.rules(asset)
    if transcribe:
        raise ValueError('当前版本暂停 ASR 和自动字幕')
    started = time.monotonic()
    project_id = asset['project_id']
    path = asset_store.file(asset)
    storage_root = path.parent.parent
    cached = asset.get('analysis')
    if cached:
        phase('复用素材内容指纹缓存')
        return asset
    phase('正在检查音视频流')
    if asset.get('kind') == 'image':
        # Decode the image to validate it; stills have a flexible scene duration.
        ffmpeg = executable('ffmpeg')
        if not ffmpeg:
            raise ValueError('视频处理组件尚未就绪')
        image_path = asset['path']
        if not has_png(ffmpeg):
            folder = storage_root / 'analysis' / asset['id']
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / 'image.bmp'
            convert_image(path, target, cancel)
            image_path = target.relative_to(storage_root).as_posix()
        run([ffmpeg, '-v', 'error', '-i', str(asset_store.file(asset, image_path)), '-frames:v', '1', '-f', 'null', '-'], cancel)
        asset['analysis'] = {'duration': 120, 'has_video': True, 'has_audio': False,
                             'image_path': image_path, 'transcript': [], 'frames': [{'second': 0, 'path': asset['path']}]}
        return store.put('asset', asset, project_id)
    analysis = cached or probe(path, cancel)
    ffmpeg = executable('ffmpeg')
    if not ffmpeg:
        raise ValueError('未找到 FFmpeg，请安装并配置 FFMPEG_PATH')
    folder = storage_root / 'analysis' / asset['id']
    folder.mkdir(parents=True, exist_ok=True)
    if analysis['has_audio']:
        phase('正在提取分析音频')
        run([ffmpeg, '-y', '-v', 'error', '-i', str(path), '-vn', '-ar', '16000', '-ac', '1', str(folder / 'audio.wav')], cancel)
    if analysis['has_video'] and not cached:
        phase('正在提取关键帧')
        frames = []
        for i, second in enumerate([0, analysis['duration'] / 2, max(0, analysis['duration'] - 0.2)]):
            target = folder / f'frame-{i}.jpg'
            run([ffmpeg, '-y', '-v', 'error', '-ss', str(second), '-i', str(path), '-frames:v', '1', '-vf', 'scale=480:-2', str(target)], cancel)
            if target.exists():
                frames.append({'second': second, 'path': target.relative_to(storage_root).as_posix()})
        analysis['frames'] = frames
    analysis.setdefault('transcript', [])
    # Persist completed local analysis for subsequent editing tasks.
    asset['analysis'] = analysis
    if asset['type'] == 'Music':
        asset['protected'] = [{'start': 0, 'end': analysis['duration']}]
    store.put('asset', asset, project_id)
    analysis['elapsed_sec'] = round(time.monotonic() - started, 2)
    asset['analysis'] = analysis
    if asset['type'] == 'Music':
        asset['protected'] = [{'start': 0, 'end': analysis['duration']}]
    return store.put('asset', asset, project_id)


def escaped_filter_path(path):
    return str(path).replace('\\', '/').replace(':', '\\:').replace("'", "\\'")


def ass_text(text):
    # Strip ASS control syntax from user/model text, leaving literal readable text.
    return str(text).replace('\\', '／').replace('{', '（').replace('}', '）').replace('\r', '').replace('\n', '\\N')


def ass_time(sec):
    cs = round(sec * 100)
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f'{h}:{m:02}:{s:02}.{cs:02}'


def write_ass(path, captions, width, height, title=None, title_duration=10, title_alignment=8):
    size = max(18, round(width / 24))
    header = f'''[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Microsoft YaHei,{size},&H00FFFFFF,&H0000FFFF,&H00202020,&H80000000,0,0,0,0,100,100,0,0,1,2,0,2,30,30,{round(height * .13)},1
Style: Title,Microsoft YaHei,{round(size * 1.6)},&H00FFFFFF,&H0000FFFF,&H00202020,&H80000000,-1,0,0,0,100,100,0,0,1,3,0,{title_alignment},45,45,{round(height * .12)},1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    lines = [f"Dialogue: 0,{ass_time(c['start'])},{ass_time(c['end'])},Default,,0,0,0,,{ass_text(c['text'])}" for c in captions]
    if title:
        lines.append(f'Dialogue: 1,0:00:00.00,{ass_time(title_duration)},Title,,0,0,0,,{ass_text(title)}')
    path.write_text(header + '\n'.join(lines), encoding='utf-8-sig')


def render(project_id, version, assets, cancel, phase, final=False):
    from . import assets as asset_store
    ffmpeg = executable('ffmpeg')
    if not ffmpeg:
        raise ValueError('FFmpeg 未配置，无法生成视频')
    timeline = validate(version['result'], assets)
    subtitles_enabled = version.get('subtitles_enabled', True)
    if not subtitles_enabled:
        timeline['captions'] = []
    base = store.project_dir(project_id)
    folder = base / 'outputs' / version['id'] / ('final' if final else 'preview')
    folder.mkdir(parents=True, exist_ok=True)
    size = {'9:16': (1080, 1920), '16:9': (1920, 1080), '1:1': (1080, 1080)}[timeline.get('aspect', '9:16')]
    width, height = size if final else tuple(int(v / 2) for v in size)
    by_id, chunks = {a['id']: a for a in assets}, []
    for index, segment in enumerate(timeline['segments']):
        phase(f"正在渲染片段 {index + 1}/{len(timeline['segments'])}")
        asset = by_id[segment['source_asset_id']]
        duration = segment['source_end_sec'] - segment['source_start_sec']
        chunk = folder / f'chunk-{index:03}.mp4'
        # Normalize all segments before concat. Contain preserves both speakers.
        args = [ffmpeg, '-y', '-v', 'error']
        if asset.get('kind') == 'image':
            args += ['-loop', '1', '-framerate', '30']
        else:
            args += ['-ss', str(segment['source_start_sec'])]
        source_path = asset['analysis'].get('image_path', asset['path']) if asset.get('kind') == 'image' else asset['path']
        args += ['-t', str(duration), '-i', str(asset_store.file(asset, source_path))]
        audio_index = 0
        if not asset['analysis']['has_audio']:
            args += ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo']
            audio_index = 1
        video_index = 0
        if not asset['analysis']['has_video']:
            video_index = 1 if audio_index == 0 else 2
            args += ['-f', 'lavfi', '-i', f'color=c=0x11141c:s={width}x{height}:r=30']
        args += ['-map', f'{video_index}:v:0', '-map', f'{audio_index}:a:0', '-t', str(duration),
                 '-vf', f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x11141c,setsar=1,fps=30',
                 '-af', f"volume={segment.get('volume', 1)},aresample=48000,apad",
                 *video_encoding(final), '-pix_fmt', 'yuv420p',
                 '-c:a', 'aac', '-b:a', '192k', '-ac', '2', '-movflags', '+faststart', str(chunk)]
        run(args, cancel)
        chunks.append(chunk)
    (folder / 'concat.txt').write_text('\n'.join(f"file '{p.name}'" for p in chunks), encoding='utf-8')
    phase('正在合成视频')
    if subtitles_enabled:
        (folder / 'subtitles.srt').write_text(srt(timeline['captions']), encoding='utf-8-sig')
        write_ass(folder / 'subtitles.ass', timeline['captions'], width, height)
    target = folder / 'video.mp4'
    args = [ffmpeg, '-y', '-v', 'error', '-f', 'concat', '-safe', '1', '-i', 'concat.txt']
    if timeline['captions']:
        if not font_path():
            raise ValueError('没有可用中文字体，请设置 MEDIA_FONT_PATH 后重试')
        if has_ass_filter(ffmpeg):
            font_dir = escaped_filter_path(Path(font_path()).parent)
            args += ['-vf', f"ass=subtitles.ass:fontsdir='{font_dir}'", *video_encoding(final)]
        else:
            inputs, filters, output = text_overlays(folder, timeline['captions'], width, height, cancel)
            args += [*inputs, '-filter_complex', filters, '-map', output, '-map', '0:a:0', *video_encoding(final), '-pix_fmt', 'yuv420p']
    else:
        args += ['-c:v', 'copy']
    args += ['-c:a', 'copy', '-movflags', '+faststart', 'video.mp4']
    run(args, cancel, folder)
    phase('正在检查成片可解码与时长')
    checked = probe(target, cancel)
    if not checked['has_audio'] or not checked['has_video'] or abs(checked['duration'] - timeline['duration']) > max(.3, len(chunks) / 30 + .1):
        raise ValueError('成片音视频流或时长检查未通过')
    run([ffmpeg, '-v', 'error', '-i', str(target), '-f', 'null', '-'], cancel)
    artifacts = {'video': target.relative_to(base).as_posix()}
    if subtitles_enabled:
        artifacts['subtitles'] = (folder / 'subtitles.srt').relative_to(base).as_posix()
    if final:
        if not font_path():
            raise ValueError('封面需要中文字体，请配置 MEDIA_FONT_PATH')
        project = store.get('project', project_id)
        write_ass(folder / 'cover.ass', [], width, height, project['name'])
        phase('正在生成封面与发布材料')
        cover_args = [ffmpeg, '-y', '-v', 'error', '-ss', str(min(.5, timeline['duration'] / 2)), '-i', 'video.mp4']
        if has_ass_filter(ffmpeg):
            cover_args += ['-vf', f"ass=cover.ass:fontsdir='{escaped_filter_path(Path(font_path()).parent)}'"]
        else:
            inputs, filters, output = text_overlays(folder, [{'text': project['name'][:50], 'start': 0, 'end': 10}], width, height, cancel, title=True)
            cover_args += [*inputs, '-filter_complex', filters, '-map', output]
        cover_name = 'cover.png' if has_png(ffmpeg) else 'cover.bmp'
        run([*cover_args, '-frames:v', '1', '-c:v', 'png' if cover_name.endswith('.png') else 'bmp', cover_name], cancel, folder)
        if cover_name != 'cover.png':
            convert_image(folder / cover_name, folder / 'cover.png', cancel)
        script_id = version.get('upstream', {}).get('script')
        materials = store.get('version', script_id)['result'].get('publishing', {}) if script_id else {}
        (folder / 'publishing.md').write_text(f"# {project['name']}\n\n版本：{version['id']}\n\n" + json.dumps(materials, ensure_ascii=False, indent=2), encoding='utf-8')
        (folder / 'version.json').write_text(json.dumps({'version': version, 'timeline': timeline, 'checks': checked}, ensure_ascii=False, indent=2), encoding='utf-8')
        for key, filename in [('cover', 'cover.png'), ('publishing', 'publishing.md'), ('manifest', 'version.json')]:
            artifacts[key] = (folder / filename).relative_to(base).as_posix()
        import zipfile
        phase('正在打包发布文件')
        with zipfile.ZipFile(folder / 'publishing-pack.zip', 'w', compression=zipfile.ZIP_STORED) as archive:
            for filename in ['video.mp4', 'cover.png', 'publishing.md', 'version.json'] + (['subtitles.srt'] if subtitles_enabled else []):
                if cancel.is_set():
                    raise Cancelled()
                archive.write(folder / filename, arcname=filename)
        artifacts['bundle'] = (folder / 'publishing-pack.zip').relative_to(base).as_posix()
    artifacts['checks'] = {'duration': checked['duration'], 'decodable': True, 'manual_review_required': True}
    return artifacts
