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
        self.timeline = self.root / 'timeline.json'
        self.clip = self.root / '素材 clip.mp4'; self.clip.write_bytes(b'clip')
        self.sha = hashlib.sha256(b'source').hexdigest()
        self.timeline_data = {'video_id': 'example', 'source': {'sha256': self.sha},
                              'segments': [{'id': 'a', 'start_ms': 0, 'end_ms': 1500},
                                           {'id': 'b', 'start_ms': 1500, 'end_ms': 3000}]}
        self.timeline_bytes = json.dumps(self.timeline_data).encode('utf8')
        self.timeline.write_bytes(self.timeline_bytes)
        self.manifest = {'video_id': 'example', 'duration_sec': 3, 'source': {'sha256': self.sha,
                         'timeline_manifest_sha256': hashlib.sha256(self.timeline_bytes).hexdigest()},
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

    def test_multiple_overlapping_events_show_source_and_row_intersections(self):
        second = copy.deepcopy(self.events['events'][0])
        second.update({'id': 'E2', 'start_ms': 950, 'end_ms': 1600,
                       'observation': '字幕首行\n字幕次行', 'recreation': '字幕轨\n关键帧'})
        boundary = copy.deepcopy(second)
        boundary.update({'id': 'E3', 'start_ms': 0, 'end_ms': 1000,
                         'observation': '只在第一行', 'recreation': '保留'})
        self.events['events'].extend([second, boundary])
        result = self.build()
        doc, table = tool.render(result)
        rows = list(csv.DictReader(io.StringIO(table)))
        self.assertEqual(len(rows), 3)  # Existing script/shot row cardinality is unchanged.
        self.assertEqual([len(row['events']) for row in result['rows']], [3, 2, 1])
        first, second_row, third = [row['逐事件源时段与本行交集'].splitlines() for row in rows]
        self.assertEqual(len(first), 3)
        self.assertEqual(len(second_row), 2)
        self.assertEqual(len(third), 1)
        self.assertIn('事件 "E1"｜源事件 0.900–1.100 秒｜本行相交 0.900–1.000 秒（0.100 秒）', first[0])
        self.assertIn('事件 "E1"｜源事件 0.900–1.100 秒｜本行相交 1.000–1.100 秒（0.100 秒）', second_row[0])
        self.assertIn('事件 "E2"｜源事件 0.950–1.600 秒｜本行相交 0.950–1.000 秒（0.050 秒）', first[1])
        self.assertIn('本行相交 1.000–1.500 秒（0.500 秒）', second_row[1])
        self.assertIn('本行相交 1.500–1.600 秒（0.100 秒）', third[0])
        self.assertIn('观察 "字幕首行\\n字幕次行"｜复刻 "字幕轨\\n关键帧"', first[1])
        self.assertNotIn('E3', '\n'.join(second_row))  # Touching the boundary is not overlap.
        self.assertEqual(rows[0]['画面观察'], '画面变化\n字幕首行\n字幕次行\n只在第一行')
        self.assertEqual(rows[0]['复刻步骤'], '底轨叠字\n字幕轨\n关键帧\n保留')
        self.assertEqual([x['id'] for x in json.loads(rows[0]['events'])], ['E1', 'E2', 'E3'])
        self.assertIn('原视频源时间，不是新配音时间', doc)
        self.assertIn('源事件 0.900–1.100 秒；本行相交 1.000–1.100 秒', doc)

    def test_no_event_has_empty_readable_event_cell(self):
        _, table = tool.render(self.build())
        rows = list(csv.DictReader(io.StringIO(table)))
        self.assertEqual(rows[2]['逐事件源时段与本行交集'], '')
        self.assertEqual(rows[2]['画面观察'], '未提供，待审阅')

    def test_wrong_source_timeline_asset_and_foreign_video(self):
        self.source.write_bytes(b'wrong')
        with self.assertRaisesRegex(ValueError, 'Source file'): self.build()
        self.source.write_bytes(b'source'); self.timeline.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'Timeline'): self.build()
        self.timeline.write_bytes(self.timeline_bytes); self.clip.write_bytes(b'changed')
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

    def test_cross_segment_forged_range_matches_assets_but_not_hashed_timeline(self):
        bad = copy.deepcopy(self.manifest)
        bad['segments'][0]['source_shot_spans'][0]['source_shot_end_ms'] = 1600
        spans = bad['segments'][1]['source_shot_spans']
        spans[0]['source_shot_end_ms'] = 1600
        spans[0]['segment_intersection_end_ms'] = 1600
        spans[1]['source_shot_start_ms'] = 1600
        spans[1]['segment_intersection_start_ms'] = 1600
        assets = copy.deepcopy(self.assets)
        assets['assets'][0]['source_end_ms'] = 1600
        with self.assertRaisesRegex(ValueError, 'interval differs from source timeline'):
            self.build(manifest=bad, assets=assets)

    def test_timeline_identity_source_and_missing_or_duplicate_shot_rejected(self):
        variants = [
            ({**self.timeline_data, 'video_id': 'another'}, 'identity'),
            ({**self.timeline_data, 'source': {'sha256': 'a'*64}}, 'source hash'),
            ({**self.timeline_data, 'segments': self.timeline_data['segments'][1:]}, 'missing'),
            ({**self.timeline_data, 'segments': self.timeline_data['segments'] * 2}, 'Duplicate'),
        ]
        for data, message in variants:
            with self.subTest(message=message):
                raw = json.dumps(data).encode('utf8')
                self.timeline.write_bytes(raw)
                manifest = copy.deepcopy(self.manifest)
                manifest['source']['timeline_manifest_sha256'] = hashlib.sha256(raw).hexdigest()
                with self.assertRaisesRegex(ValueError, message):
                    self.build(manifest=manifest)

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
