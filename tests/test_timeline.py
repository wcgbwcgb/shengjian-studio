import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import store, timeline


def asset(ident='speech', duration=20, kind='speech', protected=None, transcript=None):
    return {'id': ident, 'name': ident, 'kind': kind, 'protected': protected or [],
            'analysis': {'duration': duration, 'transcript': transcript or []}}


def segment(ident, start, end, **extra):
    return dict(source_asset_id=ident, source_start_sec=start, source_end_sec=end, **extra)


class TimelineTests(unittest.TestCase):
    def test_cut_recalculates_source_mapping_and_subtitles(self):
        a = asset(transcript=[{'start': 10, 'end': 14, 'text': '和弦'}])
        result = timeline.validate({'segments': [segment('speech', 8, 15)]}, [a])
        self.assertEqual(timeline.source_at(result, 3)[0]['source_sec'], 11)
        self.assertEqual(timeline.map_captions(result['segments'], [a]), [{'start': 2, 'end': 6, 'text': '和弦'}])

    def test_repeated_source_occurrences_have_distinct_ids_and_captions(self):
        a = asset(transcript=[{'start': 2, 'end': 4, 'text': '试听'}])
        result = timeline.validate({'segments': [segment('speech', 0, 5), segment('speech', 0, 5)]}, [a])
        self.assertNotEqual(result['segments'][0]['id'], result['segments'][1]['id'])
        self.assertEqual([c['start'] for c in timeline.map_captions(result['segments'], [a])], [2, 7])

    def test_reordering_preserves_source_times(self):
        result = timeline.validate({'segments': [segment('speech', 12, 18), segment('speech', 0, 5)]}, [asset()])
        self.assertEqual(timeline.source_at(result, 7)[0]['source_sec'], 1)
        self.assertEqual(result['duration'], 11)

    def test_music_cannot_be_silently_omitted_or_truncated(self):
        assets = [asset(), asset('music', 8, 'music', [{'start': 0, 'end': 8}])]
        for segments in [[segment('speech', 0, 10)], [segment('speech', 0, 10), segment('music', 0, 7)]]:
            with self.assertRaisesRegex(ValueError, '音乐'):
                timeline.validate({'segments': segments}, assets)

    def test_split_music_coverage_can_be_complete(self):
        a = asset('music', 8, 'music', [{'start': 0, 'end': 8}])
        result = timeline.validate({'segments': [segment('music', 0, 4), segment('music', 4, 8)]}, [a])
        self.assertEqual(result['duration'], 8)

    def test_nonfinite_out_of_bounds_and_duplicate_ids_are_rejected(self):
        for segments in [[segment('speech', 0, float('nan'))], [segment('speech', -1, 3)],
                         [segment('speech', 0, 21)], [segment('missing', 0, 1)],
                         [segment('speech', 0, 2, id='same'), segment('speech', 2, 3, id='same')]]:
            with self.assertRaises(ValueError):
                timeline.validate({'segments': segments}, [asset()])

    def test_subtitle_bounds_checked_and_srt_rounding(self):
        with self.assertRaisesRegex(ValueError, '字幕'):
            timeline.validate({'segments': [segment('speech', 0, 3)], 'captions': [{'start': 2, 'end': 4, 'text': '错'}]}, [asset()])
        self.assertIn('00:01:00,000', timeline.srt([{'start': 59.9999, 'end': 61, 'text': '对'}]))

    def test_ab_music_gain_must_stay_consistent(self):
        a = asset('music', 8, 'music', [{'start': 0, 'end': 8}])
        with self.assertRaisesRegex(ValueError, '原始增益'):
            timeline.validate({'segments': [segment('music', 0, 8, volume=1.3)]}, [a])


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patcher = patch.object(store, 'DATA', Path(self.temp.name))
        self.patcher.start()
        store.init()

    def tearDown(self):
        self.patcher.stop()
        self.temp.cleanup()

    def test_restart_preserves_versions_and_marks_unfinished_tasks(self):
        p = store.put('project', {'name': 'test', 'adopted': {}})
        v = store.put('version', {'stage': 'script', 'result': {'paragraphs': []}}, p['id'])
        t = store.put('task', {'status': 'running'}, p['id'])
        store.init()
        self.assertEqual(store.get('task', t['id'])['status'], 'interrupted')
        self.assertEqual(store.get('version', v['id'])['stage'], 'script')

    def test_path_escape_is_rejected(self):
        p = store.put('project', {'name': 'test'})
        with self.assertRaises(ValueError):
            store.project_file(p['id'], '../../secret.txt')

    def test_requirements_precedence_and_explicit_aspect(self):
        p = {'defaults': store.DEFAULTS, 'requirements': {'duration': 90}, 'stage_settings': {'edit': {'duration': 80}}}
        r = store.effective(p, 'edit', '改成横屏，75 秒', {'duration': 70})
        self.assertEqual(r['duration'], 75)
        self.assertEqual(r['aspect'], '16:9')


if __name__ == '__main__':
    unittest.main()
