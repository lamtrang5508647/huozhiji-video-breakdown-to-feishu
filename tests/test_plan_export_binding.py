"""Hash/provenance regressions using synthetic byte fixtures, not real media claims."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skill/analyze-folder-videos-to-feishu/scripts'))
import build_video_base_plan as builder


class ExportBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'semantic/clips').mkdir(parents=True)
        (self.root / 'semantic/thumbnails').mkdir()
        (self.root / 'shots').mkdir()
        for name in ('source.mp4', 'semantic/clips/S01.mp4', 'semantic/thumbnails/S01.jpg', 'shots/shot.mp4', 'shots/shot.jpg'):
            (self.root / name).write_bytes(('synthetic unit test bytes: ' + name).encode())
        source = {'path': 'source.mp4', 'sha256': builder.sha256_file(self.root / 'source.mp4')}
        shot = {'id': 'shot1', 'ordinal': 1, 'start_ms': 0, 'end_ms': 1000, 'reviewed': True,
                'subtitle_excerpt': 'Synthetic fixture', 'description': 'Synthetic card'}
        original = {'video_id': 'clip-001', 'duration_sec': 1, 'source': source,
                    'coverage': 'full', 'review_status': 'complete', 'segments': [shot],
                    'semantic_groups': [{'id': 'G01', 'name': 'Fixture group', 'source_shot_ids': ['shot1']}]}
        self.write('original-timeline.json', original)
        manifest = {'video_id': 'clip-001', 'duration_sec': 1, 'source': dict(source,
                    timeline_manifest_path='original-timeline.json',
                    timeline_manifest_sha256=builder.sha256_file(self.root / 'original-timeline.json')),
                    'segments': [{'id': 'S01'}]}
        self.write('script_manifest.json', manifest)
        self.write('review.json', {'manifest_sha256': builder.sha256_file(self.root / 'script_manifest.json'),
                   'content_and_provenance': 'PASS', 'reviewer': 'synthetic-unit-fixture',
                   'approved_actions': ['render continuous segment clips']})
        timeline = copy.deepcopy(original)
        timeline['segments'][0].update(clip_file='shot.mp4', thumbnail_file='shot.jpg',
                    verification={'fully_decoded': True, 'sha256': builder.sha256_file(self.root / 'shots/shot.mp4')},
                    thumbnail_sha256=builder.sha256_file(self.root / 'shots/shot.jpg'))
        self.write('shots/timeline.json', timeline)
        record = {'segment_id': 'S01', 'clip_sha256': builder.sha256_file(self.root / 'semantic/clips/S01.mp4'),
                  'thumbnail_sha256': builder.sha256_file(self.root / 'semantic/thumbnails/S01.jpg')}
        record['verification'] = {'fully_decoded': True, 'sha256': record['clip_sha256']}
        exported = copy.deepcopy(manifest)
        exported['segments'][0]['export'] = record
        exported['export'] = {'build_id': 'fixture-build', 'status': 'complete'}
        self.write('semantic/export_manifest.json', exported)
        for name in ('index.html', 'README.md', 'segments.csv'):
            (self.root / 'semantic' / name).write_text('synthetic fixture report ' + name, encoding='utf-8')
        self.write('semantic/export_status.json', {'status': 'complete', 'completed_segments': 1})
        self.write('semantic/receipt.json', {'status': 'complete', 'build_id': 'fixture-build',
                   'manifest_sha256': builder.sha256_file(self.root / 'script_manifest.json'),
                   'source_sha256': source['sha256'], 'timeline_sha256': manifest['source']['timeline_manifest_sha256'],
                   'review_receipt_sha256': builder.sha256_file(self.root / 'review.json'), 'segments': [record],
                   'report_sha256': {name: builder.sha256_file(self.root / 'semantic' / name)
                       for name in ('index.html', 'README.md', 'segments.csv', 'export_manifest.json')}})

    def write(self, name, value):
        (self.root / name).write_text(json.dumps(value), encoding='utf-8')

    def run_bundle(self):
        manifest = self.root / 'script_manifest.json'
        export = self.root / 'semantic/export_manifest.json'
        timeline = self.root / 'shots/timeline.json'
        return builder.validate_export_bundle(manifest, builder.read_json(manifest), export, builder.read_json(export),
               self.root / 'semantic/segments.csv', timeline, builder.read_json(timeline), self.root, self.root / 'review.json')

    def test_complete_bound_bundle_and_explicit_group_membership_pass(self):
        result = self.run_bundle()
        self.assertEqual(result['manifest_sha256'], builder.sha256_file(self.root / 'script_manifest.json'))
        shots, _ = builder.validate_timeline(builder.read_json(self.root / 'script_manifest.json'),
                   builder.read_json(self.root / 'shots/timeline.json'), self.root, self.root / 'shots/timeline.json')
        self.assertEqual(shots[0]['group_id'], 'G01')
        self.assertEqual(shots[0]['group_name'], 'Fixture group')

    def test_unreviewed_and_not_reviewed_are_explicitly_rejected(self):
        manifest = builder.read_json(self.root / 'script_manifest.json')
        for status in ('unreviewed', 'not_reviewed', 'reviewed_but_rejected', 'review pending'):
            manifest['review_status'] = status
            self.write('script_manifest.json', manifest)
            with self.subTest(status=status), self.assertRaisesRegex(builder.PlanError, 'explicitly approved status'):
                self.run_bundle()

    def test_changed_manifest_is_not_rebound_to_old_export(self):
        manifest = builder.read_json(self.root / 'script_manifest.json')
        manifest['title'] = 'Changed after review'
        self.write('script_manifest.json', manifest)
        with self.assertRaisesRegex(builder.PlanError, 'review receipt'):
            self.run_bundle()

    def test_changed_reports_and_same_path_media_are_rejected(self):
        for relative in ('semantic/segments.csv', 'semantic/index.html', 'semantic/README.md',
                         'semantic/clips/S01.mp4', 'semantic/thumbnails/S01.jpg', 'source.mp4'):
            path = self.root / relative
            original = path.read_bytes()
            path.write_bytes(original + b' altered')
            with self.subTest(path=relative), self.assertRaises(builder.PlanError):
                self.run_bundle()
            path.write_bytes(original)

    def test_changed_original_or_exported_timeline_is_rejected(self):
        for relative in ('original-timeline.json', 'shots/timeline.json'):
            path = self.root / relative
            original = path.read_bytes()
            data = builder.read_json(path)
            data['segments'][0]['subtitle_excerpt'] = 'altered source text'
            self.write(relative, data)
            with self.subTest(path=relative), self.assertRaises(builder.PlanError):
                self.run_bundle()
            path.write_bytes(original)

    def test_shot_clip_or_thumbnail_changed_after_export_is_rejected(self):
        for relative in ('shots/shot.mp4', 'shots/shot.jpg'):
            path = self.root / relative
            original = path.read_bytes()
            path.write_bytes(original + b' altered')
            with self.subTest(path=relative), self.assertRaisesRegex(builder.PlanError, 'Shot media changed'):
                builder.validate_timeline(builder.read_json(self.root / 'script_manifest.json'),
                    builder.read_json(self.root / 'shots/timeline.json'), self.root, self.root / 'shots/timeline.json')
            path.write_bytes(original)

    def test_review_receipt_cannot_be_replaced_or_have_false_status(self):
        review = builder.read_json(self.root / 'review.json')
        review['content_and_provenance'] = 'FAIL'
        self.write('review.json', review)
        with self.assertRaisesRegex(builder.PlanError, 'review receipt'):
            self.run_bundle()


if __name__ == '__main__':
    unittest.main()
