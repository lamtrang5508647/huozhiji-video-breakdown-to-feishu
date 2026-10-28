import copy
import csv
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('editing_handoff', ROOT / 'skill/bilibili-editing-analysis/scripts/build_editing_handoff.py')
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def shot(sid, a, b, left, right):
    return {'shot_id': sid, 'source_shot_start_ms': a, 'source_shot_end_ms': b,
            'segment_intersection_start_ms': left, 'segment_intersection_end_ms': right}


def segment(sid, a, b, spans):
    return {'id': sid, 'name': '信息段', 'script_function': '解释', 'start_ms': a, 'end_ms': b,
            'source_shot_spans': spans, 'source_excerpt': {'items': [], 'type': 'subtitle_excerpts_only'}}


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.source = self.root / 'source.mp4'; self.source.write_bytes(b'source')
        self.timeline = self.root / 'timeline.json'; self.timeline.write_bytes(b'timeline')
        self.clip = self.root / '素材 clip.mp4'; self.clip.write_bytes(b'clip')
        self.sha = hashlib.sha256(b'source').hexdigest()
        self.manifest = {'video_id': 'example', 'duration_sec': 3, 'source': {'sha256': self.sha,
                         'timeline_manifest_sha256': hashlib.sha256(b'timeline').hexdigest()},
                         'segments': [segment('S1', 0, 1000, [shot('a', 0, 1500, 0, 1000)]),
                                      segment('S2', 1000, 3000, [shot('a', 0, 1500, 1000, 1500),
                                                                  shot('b', 1500, 3000, 1500, 3000)])]}
        self.assets = {'schema': 'editing-assets/v1', 'video_id': 'example', 'source_sha256': self.sha,
                       'assets': [{'shot_id': 'a', 'path': '素材 clip.mp4', 'sha256': hashlib.sha256(b'clip').hexdigest(),
                                   'source_start_ms': 0, 'source_end_ms': 1500}]}
        self.events = {'schema': 'editing-events/v1', 'video_id': 'example', 'source_sha256': self.sha,
                       'events': [{'id': 'E1', 'start_ms': 900, 'end_ms': 1100,
                           'visual_status': 'observed_frames', 'audio_status': 'not_reviewed',
                           'observation': '画面变化', 'recreation': '底轨叠字', 'evidence': '相邻帧'}]}

    def build(self, assets=None, events=None, manifest=None):
        return tool.build(self.root, manifest or self.manifest, self.source, self.timeline,
                          assets or self.assets, events or self.events)

    def test_cross_segment_event_links_and_missing(self):
        result = self.build()
        self.assertEqual(len(result['rows']), 3)
        self.assertEqual([len(row['events']) for row in result['rows']], [1, 1, 0])
        self.assertEqual(result['rows'][1]['source_start_ms'], 1000)
        self.assertEqual(result['rows'][1]['source_shot_start_ms'], 0)
        self.assertEqual(result['rows'][2]['asset_status'], 'missing')
        doc, table = tool.render(result)
        self.assertIn('%E7%B4%A0%E6%9D%90%20clip.mp4', doc)
        self.assertIn('素材缺失', doc)
        self.assertIn('not_reviewed', table)
        csv_rows = list(csv.DictReader(io.StringIO(table)))
        self.assertEqual(csv_rows[0]['源交集时长秒'], '1.0')
        self.assertEqual(csv_rows[0]['复刻步骤'], '底轨叠字')
        self.assertEqual(csv_rows[0]['音频核对状态'], 'not_reviewed')

    def test_wrong_source_timeline_asset_and_foreign_video(self):
        self.source.write_bytes(b'wrong')
        with self.assertRaisesRegex(ValueError, 'Source file'): self.build()
        self.source.write_bytes(b'source'); self.timeline.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'Timeline'): self.build()
        self.timeline.write_bytes(b'timeline'); self.clip.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'Asset hash'): self.build()
        self.clip.write_bytes(b'clip'); bad = copy.deepcopy(self.events); bad['video_id'] = 'other'
        with self.assertRaisesRegex(ValueError, 'identity'): self.build(events=bad)

    def test_reject_traversal_duplicates_and_forged_event(self):
        bad = copy.deepcopy(self.assets); bad['assets'][0]['path'] = '../clip.mp4'
        with self.assertRaises(ValueError): self.build(assets=bad)
        bad = copy.deepcopy(self.assets); bad['assets'].append(copy.deepcopy(bad['assets'][0]))
        with self.assertRaisesRegex(ValueError, 'Duplicate'): self.build(assets=bad)
        bad = copy.deepcopy(self.events); bad['events'][0]['end_ms'] = 3001
        with self.assertRaisesRegex(ValueError, 'outside'): self.build(events=bad)
        bad = copy.deepcopy(self.events); bad['events'].append(copy.deepcopy(bad['events'][0]))
        with self.assertRaisesRegex(ValueError, 'Duplicate'): self.build(events=bad)

    def test_csv_formula_prefix(self):
        self.events['events'][0]['observation'] = '=HYPERLINK("bad")'
        _, table = tool.render(self.build())
        self.assertNotIn('\n=HYPERLINK', table)
        self.assertEqual(tool.field('  =1+1'), "'  =1+1")

    def test_csv_preserves_source_newline(self):
        self.manifest['segments'][0]['source_excerpt']['items'] = [{'shot_id': 'a', 'text': '原文第一行\n第二行'}]
        _, table = tool.render(self.build())
        self.assertEqual(next(csv.DictReader(io.StringIO(table)))['source_text'], '原文第一行\n第二行')

    def test_existing_export_timeline_and_invalid_paths(self):
        export = {'video_id': 'example', 'source': {'sha256': self.sha},
                  'segments': [{'id': 'a', 'start_ms': 0, 'end_ms': 1500,
                                'clip_file': '素材 clip.mp4',
                                'verification': {'sha256': hashlib.sha256(b'clip').hexdigest()}}]}
        path = self.root / 'export.json'; path.write_text(json.dumps(export, ensure_ascii=False), encoding='utf8')
        assets = tool.asset_map_from_export(self.root, path)
        self.assertEqual(self.build(assets=assets)['rows'][0]['asset_status'], 'declared_mapping_hash_checked')
        export['segments'][0]['clip_file'] = '../escape.mp4'
        path.write_text(json.dumps(export), encoding='utf8')
        with self.assertRaisesRegex(ValueError, 'Unsafe'): tool.asset_map_from_export(self.root, path)

    def test_asset_range_mismatch_and_future_shot_rejected(self):
        bad = copy.deepcopy(self.assets); bad['assets'][0]['source_end_ms'] = 1499
        with self.assertRaisesRegex(ValueError, 'range'): self.build(assets=bad)
        bad = copy.deepcopy(self.manifest); bad['duration_sec'] = 2
        with self.assertRaisesRegex(ValueError, 'duration'): self.build(manifest=bad)

    def test_standalone_install_without_companion_and_alignment_snapshot(self):
        original = ROOT / 'skill/analyze-folder-videos-to-feishu/scripts/script_shot_alignment.py'
        snapshot = ROOT / 'skill/bilibili-editing-analysis/scripts/alignment_intersection.py'
        self.assertEqual(hashlib.sha256(original.read_bytes()).digest(), hashlib.sha256(snapshot.read_bytes()).digest())
        scripts = self.root / 'isolated' / 'bilibili-editing-analysis' / 'scripts'
        scripts.mkdir(parents=True)
        shutil.copy2(snapshot, scripts / snapshot.name)
        shutil.copy2(ROOT / 'skill/bilibili-editing-analysis/scripts/build_editing_handoff.py', scripts / 'build_editing_handoff.py')
        isolated_spec = importlib.util.spec_from_file_location('standalone_handoff', scripts / 'build_editing_handoff.py')
        isolated = importlib.util.module_from_spec(isolated_spec); isolated_spec.loader.exec_module(isolated)
        result = isolated.build(self.root, self.manifest, self.source, self.timeline, self.assets, self.events)
        self.assertEqual(len(result['rows']), 3)


if __name__ == '__main__': unittest.main()
