import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from fractions import Fraction

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


@unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg required for actual source frame PTS')
class MidpointReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                        'color=red:size=32x32:rate=20:duration=1', '-c:v', 'mpeg4', str(self.source)],
                       check=True, capture_output=True)
        self.source_bytes = self.source.read_bytes()
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
                               's1': {'start': frame(0, 0), 'middle': frame(250, 250), 'end': frame(450, 500)},
                               's2': {'start': frame(500, 500), 'middle': frame(750, 750), 'end': frame(950, 1000),
                                      'cut_before': frame(450, 500), 'cut_after': frame(500, 500)}}}
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

    def use_current_extractor_jpegs(self):
        import av
        selected = {}
        with av.open(str(self.source)) as container:
            for frame in container.decode(video=0):
                ms = float(frame.pts * frame.time_base) * 1000
                if ms in (250, 750):
                    path = self.root / f'middle-{int(ms)}.jpg'
                    frame.to_image().save(path, quality=95)
                    selected[ms] = path
        for shot_id, ms in [('s1', 250), ('s2', 750)]:
            path = selected[ms]
            self.index_data['shots'][shot_id]['middle'].update(path=path.name, sha256=sha(path))
            next(x for x in self.review_data['shots'] if x['shot_id'] == shot_id)['frame_sha256'] = sha(path)
        write(self.index, self.index_data)
        self.review_data['evidence_sha256'] = sha(self.index)
        write(self.review, self.review_data)
        return selected

    def test_strict_current_extractor_accepts_matching_and_repeated_still_frames(self):
        selected = self.use_current_extractor_jpegs()
        self.assertEqual(sha(selected[250]), sha(selected[750]))  # static video can legitimately repeat
        result = module.verify(self.timeline, self.index, self.review, strict_current_extractor=True)
        self.assertEqual(result['status'], 'PASS_STRUCTURE_ONLY', result)
        self.assertEqual(result['current_extractor_jpeg_check'], 'matched')
        self.assertEqual(result['frame_time_uniqueness'], 'not_assessed')
        self.assertEqual(result['semantic_truth'], 'not_assessed')

    def test_strict_current_extractor_blocks_blue_image_with_consistent_hash_and_pts(self):
        from PIL import Image
        self.use_current_extractor_jpegs()
        blue = self.root / 'blue.jpg'
        Image.new('RGB', (32, 32), (0, 0, 255)).save(blue, quality=95)
        self.index_data['shots']['s1']['middle'].update(path=blue.name, sha256=sha(blue))
        write(self.index, self.index_data)
        self.review_data['evidence_sha256'] = sha(self.index)
        self.review_data['shots'][0]['frame_sha256'] = sha(blue)
        write(self.review, self.review_data)
        self.assertEqual(self.run_check()['status'], 'PASS_STRUCTURE_ONLY')  # legacy mode is binding only
        result = module.verify(self.timeline, self.index, self.review, strict_current_extractor=True)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['current_extractor_jpeg_check'], 'mismatch_or_unverified')
        self.assertTrue(any('current-extractor JPEG mismatch or unverified' in e for e in result['errors']))

    def test_strict_current_extractor_never_calls_encoder_mismatch_a_fake(self):
        from PIL import Image
        selected = self.use_current_extractor_jpegs()
        alt = self.root / 'different-encoding.png'
        Image.open(selected[250]).save(alt)
        self.index_data['shots']['s1']['middle'].update(path=alt.name, sha256=sha(alt))
        write(self.index, self.index_data)
        self.review_data['evidence_sha256'] = sha(self.index)
        self.review_data['shots'][0]['frame_sha256'] = sha(alt)
        write(self.review, self.review_data)
        result = module.verify(self.timeline, self.index, self.review, strict_current_extractor=True)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['current_extractor_jpeg_check'], 'mismatch_or_unverified')
        self.assertNotIn('fake', json.dumps(result).lower())

    def test_strict_partial_review_never_claims_every_jpeg_matched(self):
        self.use_current_extractor_jpegs()
        self.review_data['shots'].pop()
        write(self.review, self.review_data)
        result = module.verify(self.timeline, self.index, self.review, strict_current_extractor=True)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['current_extractor_jpeg_check'], 'partial_or_unverified')
        self.assertEqual(result['current_extractor_jpeg_checked'], 1)
        self.assertEqual(result['current_extractor_jpeg_total'], 2)

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
        self.source.write_bytes(self.source_bytes)
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

    def test_start_frame_falsely_claimed_as_midpoint_blocks(self):
        middle = self.index_data['shots']['s1']['middle']
        middle['actual_pts_ms'] = middle['pts'] = 0
        write(self.index, self.index_data)
        self.review_data['evidence_sha256'] = sha(self.index)
        self.review_data['shots'][0]['actual_pts_ms'] = 0
        write(self.review, self.review_data)
        result = self.run_check()
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertTrue(any('differs from decoded source selection' in e for e in result['errors']))

    def test_sparse_vfr_uses_actual_nearest_and_tie_selects_later(self):
        import av
        import numpy as np
        source = self.root / 'vfr.mp4'
        with av.open(str(source), mode='w') as c:
            stream = c.add_stream('mpeg4', rate=25)
            stream.width, stream.height, stream.pix_fmt = 32, 32, 'yuv420p'
            stream.time_base = Fraction(1, 1000)
            for timestamp in (0, 200, 900, 1000):
                frame = av.VideoFrame.from_ndarray(np.full((32, 32, 3), timestamp // 4, dtype=np.uint8), format='rgb24')
                frame.pts, frame.time_base = timestamp, Fraction(1, 1000)
                for packet in stream.encode(frame): c.mux(packet)
            for packet in stream.encode(): c.mux(packet)
        with av.open(str(source)) as c:
            times = [float(f.pts * f.time_base) * 1000 for f in c.decode(video=0)]
        self.assertEqual(times, [0, 200, 920, 1000])
        result = module.decoded_midpoints(source, [{'id': 'near', 'start_ms': 0, 'end_ms': 1000},
                                                   {'id': 'tie', 'start_ms': 0, 'end_ms': 1120}])
        self.assertEqual(result, {'near': 200, 'tie': 920})

    def test_cli_does_not_overwrite_existing_receipt(self):
        output = self.root / 'receipt.json'; output.write_text('keep', encoding='utf-8')
        code = module.main(['--timeline', str(self.timeline), '--evidence-index', str(self.index), '--review', str(self.review), '--strict-current-extractor', '--output', str(output)])
        self.assertEqual(code, 1)
        self.assertEqual(output.read_text(encoding='utf-8'), 'keep')


if __name__ == '__main__':
    unittest.main()
