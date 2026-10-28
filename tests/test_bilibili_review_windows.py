import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
import wave

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('review_windows', ROOT / 'skill/bilibili-editing-analysis/scripts/prepare_review_windows.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


@unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg unavailable')
class WindowsTest(unittest.TestCase):
    def test_media_evidence_actual_pts_hash_and_unreviewed_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / 'source.mp4'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=160x90:rate=10:duration=2',
                            '-f','lavfi','-i','sine=frequency=440:duration=2','-shortest',
                            '-c:v','mpeg4','-c:a','aac',str(source)], check=True, capture_output=True)
            out = root / 'review'
            result = mod.prepare(source, out, 1, 2)
            self.assertEqual(result['source_sha256'], hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual(len(result['windows']), 2)
            for w in result['windows']:
                self.assertEqual(w['review'], {'visual':'not_reviewed', 'audio':'not_reviewed'})
                self.assertEqual(w['sample_count'], 2)
                for sample in w['sample_times']:
                    self.assertGreaterEqual(sample['actual_pts_sec'] + .000001, sample['target_sec'])
                for name in ('contact_sheet','audio'):
                    artifact = w[name]; path = out / artifact['path']
                    self.assertEqual(artifact['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
                with wave.open(str(out / w['audio']['path'])) as audio:
                    self.assertEqual(audio.getframerate(), 44100)
                    self.assertEqual(audio.getframerate(), w['audio']['sample_rate_hz'])
                    self.assertEqual(audio.getnchannels(), w['audio']['channels'])
                    self.assertGreater(audio.getnframes(), 43000)
            self.assertIn('not continuous playback', result['scope'])
            self.assertEqual(json.loads((out / 'manifest.json').read_text(encoding='utf8')), result)
            with self.assertRaisesRegex(ValueError, 'Output exists'):
                mod.prepare(source, out, 1, 2)

    def test_silent_video_and_invalid_rate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / 'silent.mp4'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=80x60:rate=5:duration=1',
                            '-c:v','mpeg4',str(source)], check=True, capture_output=True)
            with self.assertRaises(ValueError): mod.prepare(source, root / 'bad', 2, 0)
            self.assertFalse((root / 'bad').exists())
            r = mod.prepare(source, root / 'silent', 2, 2)
            self.assertIsNone(r['windows'][0]['audio'])
            self.assertEqual(r['windows'][0]['review']['audio'], 'no_audio_stream')

    def test_nonzero_media_timestamps_are_rejected_before_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / 'offset.mp4'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=80x60:rate=5:duration=1',
                            '-output_ts_offset','5','-c:v','mpeg4',str(source)], check=True, capture_output=True)
            with self.assertRaisesRegex(ValueError, 'nonzero or unknown'):
                mod.prepare(source, root / 'output', 2, 2)
            self.assertFalse((root / 'output').exists())

    def test_audio_windows_are_aligned_to_zero_origin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / 'sync.mp4'; original = root / 'two-levels.wav'
            with wave.open(str(original), 'wb') as wav:
                wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(44100)
                wav.writeframes(struct.pack('<h', 1000) * 44100 + struct.pack('<h', 10000) * 44100)
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=80x60:rate=5:duration=2',
                            '-i',str(original),'-shortest','-c:v','mpeg4','-c:a','aac',str(source)], check=True, capture_output=True)
            result = mod.prepare(source, root / 'output', 1, 2)
            levels = []
            for w in result['windows'][:2]:
                with wave.open(str(root / 'output' / w['audio']['path']), 'rb') as audio:
                    audio.setpos(audio.getframerate() // 2)
                    raw = audio.readframes(1000)
                    samples = struct.unpack('<' + str(len(raw)//2) + 'h', raw)
                    levels.append(sum(abs(v) for v in samples) / len(samples))
            self.assertGreater(levels[1], levels[0] * 5)


if __name__ == '__main__': unittest.main()
