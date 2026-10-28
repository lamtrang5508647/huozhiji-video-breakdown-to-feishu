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
    def test_low_rate_fps_filter_labels_differ_from_actual_source_frame(self):
        """A resampled zero timestamp can represent a later, different source frame."""
        import av
        from PIL import Image, ImageChops, ImageStat
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / 'moving.mkv'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                            'testsrc2=size=160x90:rate=10:duration=4', '-c:v', 'ffv1', str(source)],
                           check=True, capture_output=True)
            old = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'info', '-i', str(source),
                                  '-vf', 'showinfo@before,fps=1/2:start_time=0,showinfo@after',
                                  '-frames:v', '1', '-f', 'null', 'NUL' if __import__('os').name == 'nt' else '/dev/null'],
                                 check=True, capture_output=True, text=True, encoding='utf8', errors='replace')
            import re
            before, after = {}, []
            for line in old.stderr.splitlines():
                match = re.search(r'pts_time:\s*([0-9.]+).*?checksum:([0-9A-F]+)', line)
                if not match: continue
                t, checksum = float(match.group(1)), match.group(2)
                if 'showinfo@before' in line: before.setdefault(checksum, []).append(t)
                if 'showinfo@after' in line: after.append((t, checksum))
            self.assertEqual(len(after), 1)
            self.assertEqual(after[0][0], 0.0)
            source_times = before.get(after[0][1], [])
            self.assertEqual(source_times, [0.9])

            result = mod.prepare(source, root / 'review', 2, .5)
            sample = result['windows'][0]['sample_times'][0]
            self.assertEqual(sample['target_sec'], 0.0)
            self.assertEqual(sample['actual_pts_sec'], 0.0)
            # The saved contact sheet's first cell must show the declared frame,
            # not merely attach the expected PTS to the fps-filtered picture.
            with av.open(str(source)) as media:
                images = {round(float(f.pts * f.time_base), 1): f.to_image().convert('RGB')
                          for f in media.decode(video=0) if f.pts is not None}
            cell = Image.open(root / 'review' / 'window-0001.jpg').convert('RGB').crop((0, 0, 160, 90))
            mean_delta = lambda reference: sum(ImageStat.Stat(ImageChops.difference(cell, reference)).mean)
            self.assertLess(mean_delta(images[0.0]), mean_delta(images[0.9]))

    def test_positive_video_offset_retains_pts_and_audio_zero_origin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / 'video-delayed.mkv'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=80x60:rate=10:duration=2',
                            '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=2',
                            '-filter:v','setpts=PTS+0.2/TB','-fps_mode','passthrough',
                            '-c:v','mpeg4','-c:a','pcm_s16le',str(source)],check=True,capture_output=True)
            result = mod.prepare(source, root / 'out', 1, 2)
            self.assertEqual(result['container_start_sec'], 0)
            self.assertAlmostEqual(result['video_stream_start_sec'], .2, places=3)
            self.assertAlmostEqual(result['windows'][0]['sample_times'][0]['actual_pts_sec'], .2, places=3)
            self.assertAlmostEqual(result['windows'][0]['audio']['source_coverage_sec'][0]['start_sec'], 0, places=3)
            self.assertEqual(result['windows'][0]['audio']['coverage_status'], 'complete')
            self.assertEqual(result['windows'][0]['review']['audio'], 'not_reviewed')

    def test_positive_audio_offset_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / 'audio-delayed.mkv'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=80x60:rate=10:duration=2',
                            '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=2',
                            '-filter:a','asetpts=PTS+0.2/TB','-fps_mode','passthrough',
                            '-c:v','mpeg4','-c:a','pcm_s16le',str(source)],check=True,capture_output=True)
            with self.assertRaisesRegex(ValueError, 'nonzero or unknown audio stream'):
                mod.prepare(source, root / 'out', 1, 2)
            self.assertFalse((root / 'out').exists())

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
                    self.assertEqual(audio.getnframes(), w['audio']['sample_count'])
                self.assertEqual(w['audio']['coverage_status'], 'complete')
                self.assertAlmostEqual(w['audio']['duration_sec'], 1.0, places=3)
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

    def test_short_audio_reports_complete_partial_and_empty_without_confusing_silence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / 'short.mkv'; audio_file = root / 'audio.wav'
            # 1.4 seconds of silent PCM is present audio, not missing audio.
            with wave.open(str(audio_file), 'wb') as wav:
                wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(48000)
                wav.writeframes(b'\x00\x00' * int(48000 * 1.4))
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=80x60:rate=10:duration=3',
                            '-i',str(audio_file),'-c:v','mpeg4','-c:a','pcm_s16le',str(source)], check=True, capture_output=True)
            result = mod.prepare(source, root / 'out', 1, 2)
            clips = [w['audio'] for w in result['windows']]
            self.assertEqual([a['coverage_status'] for a in clips], ['complete', 'partial', 'empty'])
            self.assertAlmostEqual(clips[0]['duration_sec'], 1.0, places=3)
            self.assertAlmostEqual(clips[1]['duration_sec'], 0.4, places=3)
            self.assertEqual(clips[2]['sample_count'], 0)
            self.assertEqual(clips[2]['source_coverage_sec'], [])
            self.assertEqual(result['windows'][2]['review']['audio'], 'not_reviewed')

    def test_internal_timestamp_gap_is_not_reported_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / 'gap.mkv'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=80x60:rate=10:duration=2',
                            '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=2',
                            '-af',r'aselect=not(between(t\,0.4\,0.7))', '-c:v','mpeg4','-c:a','pcm_s16le',str(source)],
                           check=True, capture_output=True)
            result = mod.prepare(source, root / 'out', 1, 2)
            first = result['windows'][0]['audio']
            self.assertEqual(first['coverage_status'], 'partial')
            self.assertEqual(len(first['source_coverage_sec']), 2)
            self.assertLess(first['source_coverage_sec'][0]['end_sec'], first['source_coverage_sec'][1]['start_sec'])
            self.assertEqual(result['windows'][1]['audio']['coverage_status'], 'complete')

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
