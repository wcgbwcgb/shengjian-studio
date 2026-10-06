import tempfile
import os
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app import media, store, timeline

FFMPEG, FFPROBE = media.executable('ffmpeg'), media.executable('ffprobe')


@unittest.skipUnless(media.executable('ffmpeg') and media.executable('ffprobe'), '需要本地 FFmpeg / FFprobe')
class RealRendererTests(unittest.TestCase):
    """Synthetic codec fixtures only; not a substitute for human footage acceptance."""
    def test_real_render_reordered_clips_and_music(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(store, 'DATA', Path(directory)), \
             patch.dict(os.environ, {'FFMPEG_PATH': FFMPEG, 'FFPROBE_PATH': FFPROBE}):
            store.init()
            p = store.put('project', {'name': '渲染测试素材 · 非真实录制'})
            folder = store.project_dir(p['id'])
            (folder / 'assets').mkdir()
            event = threading.Event()
            media.run([media.executable('ffmpeg'), '-y', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x240:rate=30',
                       '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000', '-t', '2',
                       *media.video_encoding(), '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(folder / 'assets' / 'speech.mp4')], event)
            media.run([media.executable('ffmpeg'), '-y', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=660:sample_rate=48000',
                       '-t', '0.5', str(folder / 'assets' / 'music.wav')], event)
            assets = []
            for name, kind in [('speech.mp4', 'speech'), ('music.wav', 'music')]:
                a = store.put('asset', {'name': name, 'kind': kind, 'path': 'assets/' + name, 'protected': []}, p['id'])
                assets.append(media.analyze(a, event, lambda _: None))
            result = timeline.validate({'aspect': '9:16', 'segments': [
                {'source_asset_id': assets[0]['id'], 'source_start_sec': 1, 'source_end_sec': 2},
                {'source_asset_id': assets[1]['id'], 'source_start_sec': 0, 'source_end_sec': .5},
                {'source_asset_id': assets[0]['id'], 'source_start_sec': 0, 'source_end_sec': .5}],
                'captions': [{'start': .1, 'end': .8, 'text': '字幕测试'}] if media.font_path() else []}, assets)
            version = {'id': store.uid(), 'result': result, 'upstream': {}}
            preview = media.render(p['id'], version, assets, event, lambda _: None)
            self.assertTrue(store.project_file(p['id'], preview['video']).exists())
            self.assertTrue(preview['checks']['decodable'])
            self.assertAlmostEqual(preview['checks']['duration'], 2, delta=.3)
            if media.font_path():
                final = media.render(p['id'], version, assets, event, lambda _: None, final=True)
                self.assertTrue(store.project_file(p['id'], final['bundle']).is_file())
                self.assertTrue(store.project_file(p['id'], final['cover']).is_file())
                import zipfile
                disabled = dict(version, id=store.uid(), subtitles_enabled=False)
                clean = media.render(p['id'], disabled, assets, event, lambda _: None, final=True)
                self.assertNotIn('subtitles', clean)
                output = store.project_file(p['id'], clean['video']).parent
                self.assertFalse((output / 'subtitles.srt').exists())
                self.assertFalse((output / 'subtitles.ass').exists())
                with zipfile.ZipFile(store.project_file(p['id'], clean['bundle'])) as archive:
                    self.assertNotIn('subtitles.srt', archive.namelist())


if __name__ == '__main__':
    unittest.main()
