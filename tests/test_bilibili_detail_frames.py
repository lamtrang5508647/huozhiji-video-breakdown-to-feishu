import hashlib
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('detail_frames', ROOT / 'skill/bilibili-editing-analysis/scripts/prepare_detail_frames.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


@unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg unavailable')
class DetailFramesTest(unittest.TestCase):
    def test_original_pts_exact_range_and_real_frame_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); video = root / 'source.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                            'testsrc2=size=160x90:rate=10:duration=2', '-c:v', 'mpeg4', str(video)],
                           check=True, capture_output=True)
            output = root / 'detail'
            result = mod.prepare(video, output, 0.25, 0.76)
            self.assertEqual(result['source_sha256'], hashlib.sha256(video.read_bytes()).hexdigest())
            self.assertEqual([round(x['source_sec'], 1) for x in result['frames']], [.3, .4, .5, .6, .7])
            self.assertEqual(result['review_status'], 'not_reviewed')
            for x in result['frames']:
                self.assertEqual((x['width'], x['height']), (160, 90))
                self.assertEqual(x['sha256'], hashlib.sha256((output / x['path']).read_bytes()).hexdigest())
            with self.assertRaisesRegex(ValueError, 'Output exists'):
                mod.prepare(video, output, 0, 1)
            for start, end in [(-1, 1), (1, 1), (0, 2.1), (0, 11), (float('nan'), 1)]:
                with self.assertRaises(ValueError): mod.prepare(video, root / 'bad', start, end)
            self.assertFalse((root / 'bad').exists())

    def test_nonzero_origin_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); video = root / 'offset.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                            'testsrc2=size=80x60:rate=5:duration=1', '-output_ts_offset', '5',
                            '-c:v', 'mpeg4', str(video)], check=True, capture_output=True)
            with self.assertRaisesRegex(ValueError, 'nonzero or unknown'):
                mod.prepare(video, root / 'out', 0, 1)
            self.assertFalse((root / 'out').exists())


if __name__ == '__main__': unittest.main()
