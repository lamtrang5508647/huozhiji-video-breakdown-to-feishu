import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skill/analyze-folder-videos-to-feishu/scripts'
sys.path.insert(0, str(SCRIPTS))
import extract_interval_evidence
import prepare_candidate_review_sheet as sheet

MEDIA_AVAILABLE = bool(shutil.which('ffmpeg') and importlib.util.find_spec('av') and importlib.util.find_spec('PIL'))


@unittest.skipUnless(MEDIA_AVAILABLE, 'ffmpeg, PyAV and Pillow required')
class CandidateReviewSheetTests(unittest.TestCase):
    def create_case(self, root):
        source = root / '原片.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                        'testsrc2=size=160x90:rate=10:duration=2', '-c:v', 'mpeg4', str(source)],
                       check=True, capture_output=True)
        timeline = root / 'timeline.json'
        value = {'video_id': 'example', 'source': {'path': str(source), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()},
                 'review_status': 'draft', 'segments': [
                     {'id': 'shot-001', 'start_ms': 0, 'end_ms': 1000, 'description': '=untrusted', 'reviewed': False},
                     {'id': 'shot-002', 'start_ms': 1000, 'end_ms': 2000, 'description': '测试<画面>', 'reviewed': False},
                 ]}
        timeline.write_text(json.dumps(value, ensure_ascii=False), encoding='utf8')
        evidence = root / 'evidence'
        extract_interval_evidence.extract(timeline, evidence)
        return source, timeline, evidence / 'interval-evidence.json'

    def test_real_evidence_is_linked_and_never_claims_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, timeline, evidence = self.create_case(root)
            output = root / 'review'
            result = sheet.prepare(root, timeline, evidence, output)
            self.assertEqual(result['status'], 'prepared_not_reviewed')
            self.assertFalse(result['publication_ready'])
            text = (output / 'candidate-review.md').read_text(encoding='utf8')
            self.assertIn('草稿，仅供人工复核', text)
            self.assertIn('测试&lt;画面&gt;', text)
            links = re.findall(r'\]\(([^)]+)\)', text)
            self.assertEqual(len(links), 11)  # source, CSV, JSON, six frames, two cut frames
            for target in links:
                self.assertTrue((output / unquote(target)).resolve().is_file(), target)
            with (output / 'candidate-review.csv').open(encoding='utf-8-sig', newline='') as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]['候选描述'], "'=untrusted")
            self.assertTrue(all(row['本次审阅'] == 'not_reviewed' for row in rows))
            events = json.loads((output / 'review-events.json').read_text(encoding='utf8'))
            self.assertEqual(events['source_sha256'], hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual(events['schema'], 'bilibili-review-events/v1')
            self.assertTrue(all(x['review'] == {'visual':'not_reviewed','audio':'not_reviewed'} for x in events['events']))
            self.assertEqual(events['events'][0]['frame_evidence']['middle']['actual_pts_ms'], 500.0)
            with self.assertRaisesRegex(ValueError, 'new directory'):
                sheet.prepare(root, timeline, evidence, output)

    def test_tampering_and_paths_outside_root_are_blocked(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = Path(tmp)
            source, timeline, evidence = self.create_case(root)
            with self.assertRaisesRegex(ValueError, 'inside'):
                sheet.prepare(root, timeline, evidence, Path(outside) / 'output')
            data = json.loads(evidence.read_text(encoding='utf8'))
            frame = evidence.parent / data['shots']['shot-001']['middle']['path']
            frame.write_bytes(frame.read_bytes() + b'changed')
            with self.assertRaisesRegex(ValueError, 'Interval evidence invalid'):
                sheet.prepare(root, timeline, evidence, root / 'changed')
            self.assertFalse((root / 'changed').exists())

    def test_evidence_path_traversal_and_changed_source_are_blocked(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = Path(tmp)
            source, timeline, evidence = self.create_case(root)
            external = Path(outside) / 'outside.jpg'
            external.write_bytes(b'x' * 200)
            data = json.loads(evidence.read_text(encoding='utf8'))
            data['shots']['shot-001']['middle']['path'] = str(external)
            evidence.write_text(json.dumps(data), encoding='utf8')
            with self.assertRaisesRegex(ValueError, 'outside'):
                sheet.prepare(root, timeline, evidence, root / 'outside')
            source.write_bytes(source.read_bytes() + b'changed')
            with self.assertRaisesRegex(ValueError, 'source file hash'):
                sheet.prepare(root, timeline, evidence, root / 'source-changed')


if __name__ == '__main__':
    unittest.main()
