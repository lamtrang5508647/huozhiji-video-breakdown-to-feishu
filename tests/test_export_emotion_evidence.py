"""Synthetic export/presentation contracts; no remote or semantic verification."""
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skill/analyze-folder-videos-to-feishu/scripts'))
import emotion_progression
import export_script_v3 as exporter
import phrase_annotation
import render_platform_chapter as renderer
import validate_script_v3 as validator

spec = importlib.util.spec_from_file_location('export_synthetic_fixture', ROOT / 'examples/synthetic/build.py')
synthetic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(synthetic)


class ExportEmotionEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'source.mp4').write_bytes(b'Synthetic contract bytes, not decodable media')
        self.manifest = synthetic.fixture(self.root, phrase_annotation)
        self.path = self.root / 'script_manifest.json'

    def validate(self, require_approval=False):
        self.path.write_text(json.dumps(self.manifest), encoding='utf-8')
        review = self.root / 'review.json'
        review.write_text(json.dumps({
            'manifest_sha256': hashlib.sha256(self.path.read_bytes()).hexdigest(),
            'content_and_provenance': 'PASS', 'reviewer': 'synthetic fixture',
            'approved_actions': ['render continuous segment clips']}), encoding='utf-8')
        with patch.object(validator, 'ffprobe_duration', return_value=(4.0, {
                'streams': [{'codec_type': 'video', 'avg_frame_rate': '30/1'}]})):
            return validator.validate(self.manifest, self.path, None, 'ffprobe',
                                      review if require_approval else None, require_approval)

    def set_emotion(self, status, count):
        segment = self.manifest['segments'][0]
        segment['emotion'] = {
            'progression_status': status, 'review_note': '缺少连续原声；仍需复核',
            'observable_timeline': [{'observable': f'第{i + 1}项可观察动作 <source>',
                'evidence_time_ms': i * 400, 'evidence_shot_ids': ['shot-0001']}
                for i in range(count)]}
        return segment['emotion']

    def evidence(self):
        return {'source_video_id': self.manifest['video_id'],
                'source_sha256': self.manifest['source']['sha256'],
                'source_shot_ids': ['shot-0001'], 'start_ms': 0, 'end_ms': 1000,
                'description': '合成色卡中的可见标签', 'verification_status': 'source_material_only'}

    def test_explicit_emotion_without_audience_hypothesis_exports_pending_state(self):
        for status, count in [('observed_changes', 2), ('no_clear_change', 1), ('insufficient_evidence', 0)]:
            with self.subTest(status=status):
                value = self.set_emotion(status, count)
                self.assertEqual(self.validate()['status'], 'valid_draft')
                html = exporter.render_html(self.manifest)
                self.assertIn(emotion_progression.STATUSES[status], html)
                self.assertIn(value['review_note'], html)
                self.assertIn('未提供受众反应假设', html)
                if count:
                    self.assertIn('shot-0001', html)
                    self.assertIn('第1项可观察动作 &lt;source&gt;', html)
                else:
                    self.assertIn('暂无可核对事件', html)

    def test_legacy_emotion_events_and_audience_are_preserved(self):
        html = exporter.render_html(self.manifest)
        self.assertIn('No audience response measured.', html)
        self.assertIn('起点', html)
        self.assertIn('00:00:00.400', html)

    def test_csv_preserves_emotion_status_review_gap_and_events(self):
        value = self.set_emotion('insufficient_evidence', 0)
        path = self.root / 'segments.csv'
        exporter.render_csv(path, self.manifest)
        with path.open(encoding='utf-8-sig', newline='') as handle:
            row = next(csv.DictReader(handle))
        self.assertEqual(row['emotion_progression_status'], 'insufficient_evidence')
        self.assertEqual(row['emotion_review_note'], value['review_note'])
        self.assertEqual(json.loads(row['emotion_events_json']), [])

    def test_export_gate_accepts_valid_material_and_legacy_absence(self):
        self.assertEqual(self.validate(True)['status'], 'valid_draft')
        self.manifest['segments'][0]['source_evidence'] = [self.evidence()]
        self.assertEqual(self.validate(True)['status'], 'valid_draft')

    def test_export_approval_cannot_bypass_bad_material_binding_or_verified_status(self):
        for change in ({'source_video_id': 'foreign-video'}, {'source_sha256': 'f' * 64},
                       {'source_shot_ids': ['shot-0002']}, {'end_ms': 2001},
                       {'verification_status': 'verified'}):
            with self.subTest(change=change):
                self.manifest['segments'][0]['source_evidence'] = [{**self.evidence(), **change}]
                with self.assertRaisesRegex(validator.InvalidManifest, 'source_evidence'):
                    self.validate(True)

    def test_docx_renderer_rejects_foreign_material_evidence(self):
        analysis = json.loads((self.root / 'analysis.json').read_text(encoding='utf-8'))
        content = json.loads((self.root / 'sections-content.json').read_text(encoding='utf-8'))
        self.manifest['segments'][0]['source_evidence'] = [{**self.evidence(), 'source_video_id': 'foreign-video'}]
        with self.assertRaisesRegex(ValueError, 'source_evidence'):
            renderer.render(analysis, self.manifest, content,
                            'https://example.invalid/materials', 'https://example.invalid/segments')


if __name__ == '__main__':
    unittest.main()
