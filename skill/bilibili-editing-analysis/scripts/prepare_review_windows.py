#!/usr/bin/env python3
"""Create local, timestamped review evidence, never an interpretation of media."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import shutil
import subprocess
import wave
from pathlib import Path


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def prepare(source: Path, output: Path, window_sec: float = 10, fps: float = 2):
    import av
    from PIL import Image, ImageDraw

    source = source.resolve(strict=True)
    if not source.is_file(): raise ValueError('Source must be a file')
    if output.exists(): raise ValueError('Output exists; choose a new directory')
    if not math.isfinite(window_sec) or not 1 <= window_sec <= 60: raise ValueError('Window must be 1–60 seconds')
    if not math.isfinite(fps) or not 0.25 <= fps <= 10: raise ValueError('Sampling must be 0.25–10 frames per second')
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg: raise ValueError('ffmpeg is required')
    source_hash = digest(source)
    with av.open(str(source)) as container:
        if not container.streams.video: raise ValueError('Source has no video stream')
        stream = container.streams.video[0]
        # ffmpeg -ss operates on file-relative time; only accept a time base
        # we can align exactly to decoded PTS without guessing offsets.
        if container.start_time is None or abs(container.start_time / av.time_base) > 0.000001:
            raise ValueError('Unsupported nonzero or unknown container start time')
        relevant = [stream] + ([container.streams.audio[0]] if container.streams.audio else [])
        for s in relevant:
            if s.start_time is None or s.time_base is None or abs(float(s.start_time * s.time_base)) > 0.000001:
                raise ValueError('Unsupported nonzero or unknown stream start time')
        if container.duration is None: raise ValueError('Container duration unavailable')
        duration = container.duration / av.time_base
        if duration <= 0 or not math.isfinite(duration): raise ValueError('Invalid duration')
        if duration > 1800 or duration * fps > 3600 or window_sec * fps > 200:
            raise ValueError('Input too large for one review batch; use a smaller rate or prepare an identified source clip')
        has_audio = bool(container.streams.audio)
        audio_rate = container.streams.audio[0].rate if has_audio else None
        audio_channels = container.streams.audio[0].channels if has_audio else None
        if has_audio and (not audio_rate or not audio_channels): raise ValueError('Audio sample rate/channels unavailable')
        n = math.ceil(duration / window_sec)
        output.mkdir(parents=True, exist_ok=False)
        windows = [{'start_sec': round(i*window_sec, 6), 'end_sec': round(min((i+1)*window_sec, duration), 6)} for i in range(n)]
        def write_sheet(i, frames):
            w = windows[i]
            w['sample_count'] = len(frames)
            w['sample_times'] = []
            if not frames: return
            cols = 4
            sheet = Image.new('RGB', (cols*240, math.ceil(len(frames)/cols)*158), 'white')
            draw = ImageDraw.Draw(sheet)
            for j, (target, actual, image) in enumerate(frames):
                x, y = (j % cols)*240, (j // cols)*158
                sheet.paste(image, (x, y))
                draw.text((x+3, y+137), f'target {target:.3f}s  pts {actual:.3f}s', fill='black')
                w['sample_times'].append({'target_sec': target, 'actual_pts_sec': actual})
            path = output / f'window-{i+1:04d}.jpg'
            sheet.save(path, quality=90)
            w['contact_sheet'] = {'path': path.name, 'sha256': digest(path)}
        sample_no = 0
        active = 0
        frames = []
        first = True
        for frame in container.decode(stream):
            if frame.pts is None: continue
            t = float(frame.pts * frame.time_base)
            if first:
                first = False
                if abs(t) > 0.000001: raise ValueError('First decoded video PTS is not zero')
            if t < 0: continue
            target = sample_no / fps
            if target >= duration: break
            if t + 1e-7 < target: continue
            # The same decoded frame must not be duplicated to fake a higher sampling rate.
            if t >= duration + 0.1: break
            # Assign by decoded PTS, not by a missed target in a different window.
            idx = min(int(t / window_sec), n-1)
            while active < idx:
                write_sheet(active, frames)
                frames = []
                active += 1
            img = frame.to_image().convert('RGB')
            img.thumbnail((240, 135))
            frames.append((round(target, 6), round(t, 6), img))
            sample_no = max(sample_no + 1, math.floor(t * fps) + 1)
        if first: raise ValueError('No timestamped frames decoded')
        write_sheet(active, frames)
        for i in range(active + 1, n): write_sheet(i, [])
    for i, w in enumerate(windows):
        if has_audio:
            path = output / f'window-{i+1:04d}.wav'
            cmd = [ffmpeg, '-nostdin', '-v', 'error', '-n', '-i', str(source), '-ss', str(w['start_sec']),
                   '-t', str(w['end_sec']-w['start_sec']), '-map', '0:a:0', '-vn', '-c:a', 'pcm_s16le', str(path)]
            subprocess.run(cmd, check=True, capture_output=True, timeout=120)
            with wave.open(str(path), 'rb') as audio:
                if audio.getframerate() != audio_rate or audio.getnchannels() != audio_channels:
                    raise ValueError('Generated WAV sample rate/channels differ from source metadata')
            w['audio'] = {'path': path.name, 'sha256': digest(path), 'sample_rate_hz': audio_rate, 'channels': audio_channels, 'source_audio_stream': 0}
        else:
            w['audio'] = None
        w['review'] = {'visual': 'not_reviewed', 'audio': 'not_reviewed' if has_audio else 'no_audio_stream'}
    if digest(source) != source_hash: raise ValueError('Source changed during preparation; discard partial output')
    result = {'schema':'bilibili-review-windows/v1', 'source_sha256':source_hash, 'duration_sec':duration,
              'sampling_fps':fps, 'window_sec':window_sec, 'windows':windows,
              'scope':'Contact sheets are sampled frames, not continuous playback; audio clips are unreviewed. No semantic interpretation or listening is asserted.'}
    (output / 'manifest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--window-sec', type=float, default=10)
    p.add_argument('--fps', type=float, default=2)
    args = p.parse_args()
    try:
        result = prepare(args.input, args.output, args.window_sec, args.fps)
    except (OSError, ValueError, ImportError, subprocess.SubprocessError) as exc:
        print(json.dumps({'status':'BLOCKED', 'error':str(exc)}, ensure_ascii=False)); return 1
    print(json.dumps({'status':'prepared_not_reviewed', 'windows':len(result['windows']), 'manifest':str(args.output / 'manifest.json')}, ensure_ascii=False)); return 0


if __name__ == '__main__': raise SystemExit(main())
