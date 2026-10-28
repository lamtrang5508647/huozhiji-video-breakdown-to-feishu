import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skill/analyze-folder-videos-to-feishu/scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('midpoint_content', SCRIPTS / 'validate_midpoint_content_review.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


class MidpointReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source.mp4'
        self.source.write_bytes(b'synthetic source identity only')
        self.frame = self.root / 'same-frame.ppm'
        self.frame.write_bytes(b'P6\n8 8\n255\n' + bytes([255, 0, 0]) * 64)
        self.timeline = self.root / 'timeline.json'
        self.index = self.root / 'interval-evidence.json'
        self.review = self.root / 'review.json'
        self.timeline_data = {'video_id': 'fixture', 'source': {'path': str(self.source), 'sha256': sha(self.source)},
                              'segments': [{'id': 's1', 'start_ms': 0, 'end_ms': 500, 'description': '红色画面'},
                                           {'id': 's2', 'start_ms': 500, 'end_ms': 1000, 'description': '红色画面'}]}
        write(self.timeline, self.timeline_data)

        def frame(ms, requested):
            return {'path': self.frame.name, 'sha256': sha(self.frame), 'actual_pts_ms': ms,
                    'pts': ms, 'time_base': '1/1000', 'requested_ms': requested}
        self.index_data = {'video_id': 'fixture', 'source_sha256': sha(self.source),
                           'timeline_sha256': sha(self.timeline), 'shots': {
                               's1': {'start': frame(0, 0), 'middle': frame(250, 250), 'end': frame(499, 500)},
                               's2': {'start': frame(500, 500), 'middle': frame(750, 750), 'end': frame(999, 1000),
                                      'cut_before': frame(499, 500), 'cut_after': frame(500, 500)}}}
        write(self.index, self.index_data)
        self.review_data = {'schema': 'midpoint-content-review/v1', 'video_id': 'fixture',
                            'source_sha256': sha(self.source), 'timeline_sha256': sha(self.timeline),
                            'evidence_sha256': sha(self.index), 'author_id': 'author', 'reviewer_id': 'reviewer',
                            'review_mode': 'independent', 'scope': 'midpoint_only', 'shots': [
                                {'shot_id': 's1', 'description': '红色画面', 'frame_sha256': sha(self.frame),
                                 'actual_pts_ms': 250, 'observed': '红色', 'reason': '中点画面是红色', 'decision': 'confirm_midpoint'},
                                {'shot_id': 's2', 'description': '红色画面', 'frame_sha256': sha(self.frame),
                                 'actual_pts_ms': 750, 'observed': '红色', 'reason': '中点画面是红色', 'decision': 'confirm_midpoint'}]}
        write(self.review, self.review_data)

    def tearDown(self):
        self.tmp.cleanup()

    def run_check(self):
        return module.verify(self.timeline, self.index, self.review)

    def test_complete_record_is_structure_only_and_repeated_pixels_are_allowed(self):
        result = self.run_check()
        self.assertEqual(result['status'], 'PASS_STRUCTURE_ONLY', result)
        self.assertEqual(result['semantic_truth'], 'not_assessed')
        self.assertEqual(result['review_independence'], 'declared_only')
        self.assertFalse(result['publication_ready'])

    def test_consistent_but_wrong_human_description_is_not_detected_by_structure(self):
        # The fixture image is red. A structurally consistent false declaration
        # can still pass: semantic correctness remains a reviewer obligation.
        for item in self.timeline_data['segments']:
            item['description'] = '蓝色画面'
        write(self.timeline, self.timeline_data)
        self.index_data['timeline_sha256'] = sha(self.timeline)
        write(self.index, self.index_data)
        self.review_data['timeline_sha256'] = sha(self.timeline)
        self.review_data['evidence_sha256'] = sha(self.index)
        for item in self.review_data['shots']:
            item.update(description='蓝色画面', observed='蓝色', reason='中点画面是蓝色')
        write(self.review, self.review_data)
        result = self.run_check()
        self.assertEqual(result['status'], 'PASS_STRUCTURE_ONLY', result)
        self.assertEqual(result['semantic_truth'], 'not_assessed')

    def test_missing_duplicate_wrong_shot_and_needs_revision_block(self):
        for mutation in ('missing', 'duplicate', 'wrong', 'needs_revision', 'uncertain'):
            with self.subTest(mutation=mutation):
                data = json.loads(json.dumps(self.review_data))
                if mutation == 'missing': data['shots'].pop()
                if mutation == 'duplicate': data['shots'].append(data['shots'][0].copy())
                if mutation == 'wrong': data['shots'][0]['shot_id'] = 'elsewhere'
                if mutation in ('needs_revision', 'uncertain'): data['shots'][0]['decision'] = mutation
                write(self.review, data)
                self.assertEqual(self.run_check()['status'], 'BLOCKED')

    def test_same_author_or_misreported_scope_blocks(self):
        for key, value in [('reviewer_id', ' AUTHOR '), ('review_mode', 'same_agent_second_pass'), ('scope', 'full_video')]:
            data = dict(self.review_data); data[key] = value; write(self.review, data)
            self.assertEqual(self.run_check()['status'], 'BLOCKED')

    def test_bad_caption_frame_or_pts_blocks(self):
        for key, value in [('description', '错误描述'), ('frame_sha256', '0' * 64), ('actual_pts_ms', 750),
                           ('observed', ''), ('reason', '')]:
            data = json.loads(json.dumps(self.review_data)); data['shots'][0][key] = value; write(self.review, data)
            self.assertEqual(self.run_check()['status'], 'BLOCKED')

    def test_changing_source_timeline_or_evidence_invalidates_record(self):
        self.source.write_bytes(b'changed')
        self.assertEqual(self.run_check()['status'], 'BLOCKED')
        self.source.write_bytes(b'synthetic source identity only')
        self.timeline_data['segments'][0]['description'] = '已修改描述'; write(self.timeline, self.timeline_data)
        self.assertEqual(self.run_check()['status'], 'BLOCKED')
        write(self.timeline, json.loads(json.dumps(self.timeline_data, ensure_ascii=False).replace('已修改描述', '红色画面')))
        self.assertEqual(sha(self.timeline), self.review_data['timeline_sha256'])
        self.frame.write_bytes(b'changed frame')
        self.assertEqual(self.run_check()['status'], 'BLOCKED')

    def test_non_midpoint_evidence_request_blocks_even_with_updated_binding(self):
        self.index_data['shots']['s1']['middle']['requested_ms'] = 10
        write(self.index, self.index_data); self.review_data['evidence_sha256'] = sha(self.index); write(self.review, self.review_data)
        result = self.run_check()
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertTrue(any('not requested at this midpoint' in e for e in result['errors']))

    def test_cli_does_not_overwrite_existing_receipt(self):
        output = self.root / 'receipt.json'; output.write_text('keep', encoding='utf-8')
        code = module.main(['--timeline', str(self.timeline), '--evidence-index', str(self.index), '--review', str(self.review), '--output', str(output)])
        self.assertEqual(code, 1)
        self.assertEqual(output.read_text(encoding='utf-8'), 'keep')


if __name__ == '__main__':
    unittest.main()
