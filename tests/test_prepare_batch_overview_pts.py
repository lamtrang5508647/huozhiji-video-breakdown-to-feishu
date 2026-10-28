"""Legacy overview frames are not frame-accurate source timestamps."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skill/analyze-folder-videos-to-feishu/scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('legacy_batch_provenance', SCRIPTS / 'prepare_batch.py')
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)


@unittest.skipUnless(shutil.which('ffmpeg') and importlib.util.find_spec('PIL'), 'FFmpeg or Pillow unavailable')
class OverviewProvenanceTest(unittest.TestCase):
    def test_grid_position_can_differ_from_source_and_metadata_discloses_it(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            input_folder = root / 'input'
            input_folder.mkdir()
            source = input_folder / 'source.mp4'
            assets = root / 'assets'
            assets.mkdir()
            # At source PTS 0 this is red; from 0.1 s onward it is blue.
            # FFmpeg fps=2 may select the later blue frame for grid position 0.
            subprocess.run([
                'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                'color=c=red:s=160x90:r=10:d=1', '-vf',
                "drawbox=x=0:y=0:w=iw:h=ih:color=blue:t=fill:enable='gte(t,0.1)'",
                '-c:v', 'mpeg4', str(source),
            ], check=True, capture_output=True)
            result = batch.extract_video('ffmpeg', source, assets, batch.probe_video('ffmpeg', source), root)
            self.assertEqual(len(result['contact_sheets']), 1)
            self.assertTrue(result['opening_frames'])
            self.assertIsNone(result['audio_wav'])
            with Image.open(root / result['opening_frames'][0]) as image:
                opening = image.getpixel((80, 45))
            with Image.open(root / result['contact_sheets'][0]) as image:
                first_grid = image.getpixel((260, 146))
            self.assertGreater(opening[0], opening[2] + 150, 'source opening frame should be red')
            self.assertGreater(first_grid[2], first_grid[0] + 150, 'first overview grid should select later blue')
            p = result['visual_sampling_provenance']
            self.assertEqual(p['actual_source_pts'], 'not_recorded')
            self.assertEqual(p['opening_filename_time'], 'requested_input_seek_time_not_source_pts')
            self.assertEqual(p['contact_sheet_filename_time'], 'requested_window_bounds_not_each_source_pts')
            self.assertIn('Overview only', p['scope'])
            # The actual CLI writes the same disclosure into both manifests,
            # rather than merely returning it to a direct Python caller.
            job = root / 'cli-job'
            subprocess.run([sys.executable, str(SCRIPTS / 'prepare_batch.py'),
                            str(input_folder), '--work-dir', str(job)],
                           check=True, capture_output=True, text=True, encoding='utf8')
            import json
            batch_manifest = json.loads((job / 'batch_manifest.json').read_text(encoding='utf8'))
            entry = batch_manifest['videos'][0]
            self.assertEqual(entry['status'], 'prepared')
            self.assertEqual(entry['visual_sampling_provenance'], p)
            video_manifest = json.loads((job / entry['asset_dir'] / 'video_manifest.json').read_text(encoding='utf8'))
            self.assertEqual(video_manifest['visual_sampling_provenance'], p)


if __name__ == '__main__':
    unittest.main()
