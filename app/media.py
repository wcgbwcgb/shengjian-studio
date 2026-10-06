"""Local FFmpeg helpers the workbench itself needs: finding the tools, probing, and making videos playable."""
import json
import os
import shutil
import subprocess
import tempfile
import threading
from functools import lru_cache
from pathlib import Path

from . import config, store


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
    return {'service': 'music-studio', 'ffmpeg': bool(executable('ffmpeg')), 'ffprobe': bool(executable('ffprobe'))}


def run(args, cancel, cwd=None):
    if cancel.is_set():
        raise Cancelled('任务已取消')
    # Use a temporary file rather than a pipe: large FFmpeg stderr must never deadlock.
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
                raise Cancelled('任务已取消；已生成的文件保留')
        log.seek(0)
        output = log.read().decode('utf-8', errors='replace')
    if proc.returncode:
        raise ValueError('本地处理失败：' + output[-2500:])
    return output


@lru_cache(maxsize=8)
def available_video_encoder(ffmpeg):
    encoders = run([ffmpeg, '-hide_banner', '-encoders'], threading.Event())
    for name in ('libx264', 'libopenh264', 'libo264rt'):
        if name in encoders:
            return name
    raise ValueError('本地视频工具缺少 H.264 编码器，请在设置中选择完整版本的 FFmpeg。')


def video_encoding(final=False):
    encoder = available_video_encoder(executable('ffmpeg'))
    if encoder == 'libx264':
        return ['-c:v', encoder, '-preset', 'veryfast', '-crf', '20' if final else '28']
    return ['-c:v', encoder, '-b:v', '6000k' if final else '1600k']


def probe(path, cancel):
    ffprobe = executable('ffprobe')
    if not ffprobe:
        raise ValueError('未找到 FFprobe，请安装 FFmpeg 并配置 FFPROBE_PATH')
    result = json.loads(run([ffprobe, '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)], cancel))
    streams = result.get('streams', [])
    if not any(s['codec_type'] in ('audio', 'video') for s in streams):
        raise ValueError('文件没有可读取的音视频流')
    duration = float(result.get('format', {}).get('duration', 0))
    if duration <= 0:
        raise ValueError('无法确定视频时长')
    return {'duration': duration, 'streams': streams, 'has_video': any(s['codec_type'] == 'video' for s in streams),
            'has_audio': any(s['codec_type'] == 'audio' for s in streams)}
