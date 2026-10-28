#!/usr/bin/env python3
"""Extract full-resolution frames with source PTS for an identified local range.

This prepares evidence only; it does not decide whether anyone saw motion or
heard audio. End is exclusive. Container origin must be known and zero; a
known nonnegative video stream offset is preserved in source PTS.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def prepare(source: Path, output: Path, start: float, end: float):
    import av
    from PIL import Image, ImageDraw
    if not all(type(x) in (int, float) and math.isfinite(x) for x in (start, end)):
        raise ValueError('Range must be finite numbers')
    if start < 0 or end <= start or end - start > 10:
        raise ValueError('Range must be positive and at most 10 seconds')
    source = source.resolve(strict=True)
    if not source.is_file(): raise ValueError('Source must be a file')
    if output.exists(): raise ValueError('Output exists; choose a new directory')
    source_hash = digest(source)
    with av.open(str(source)) as container:
        if not container.streams.video: raise ValueError('No video stream')
        stream = container.streams.video[0]
        if container.start_time is None or abs(container.start_time / av.time_base) > 1e-6:
            raise ValueError('Unsupported nonzero or unknown container origin')
        if stream.start_time is None or stream.time_base is None:
            raise ValueError('Unsupported unknown video stream origin')
        video_start = float(stream.start_time * stream.time_base)
        if not math.isfinite(video_start) or video_start < 0:
            raise ValueError('Unsupported negative video stream origin')
        if container.duration is None: raise ValueError('Unknown duration')
        duration = container.duration / av.time_base
        if not math.isfinite(duration) or duration <= 0 or end > duration + 1e-6:
            raise ValueError('Range exceeds source duration')
        if video_start >= duration: raise ValueError('Video start time outside container duration')
        if stream.codec_context.width * stream.codec_context.height > 16_000_000:
            raise ValueError('Frame resolution exceeds 16 megapixels')
        output.mkdir(parents=True, exist_ok=False)
        frames = []
        thumbs = []
        first = True
        previous_time = None
        total_pixels = 0
        for frame in container.decode(stream):
            if frame.pts is None or frame.time_base is None: raise ValueError('Video frame timestamp missing')
            t = float(frame.pts * frame.time_base)
            if first:
                first = False
                if abs(t - video_start) > max(float(stream.time_base), float(frame.time_base)) + 1e-6:
                    raise ValueError('First decoded video PTS differs from declared stream start')
            if previous_time is not None and t <= previous_time:
                raise ValueError('Video frame PTS is not strictly increasing')
            previous_time = t
            if t < start: continue
            if t >= end: break
            if len(frames) >= 600: raise ValueError('Range exceeds 600 frame resource limit')
            total_pixels += frame.width * frame.height
            if total_pixels > 300_000_000:
                raise ValueError('Range exceeds 300 megapixel total output limit; choose a shorter range')
            image = frame.to_image().convert('RGB')
            name = f'frame-{len(frames)+1:04d}.png'
            image.save(output / name)
            thumb = image.copy()
            thumb.thumbnail((240, 135))
            thumbs.append((t, thumb))
            frames.append({'path': name, 'sha256': digest(output / name),
                           'source_pts': frame.pts,
                           'time_base': str(frame.time_base), 'source_sec': t,
                           'width': image.width, 'height': image.height})
        if first: raise ValueError('No timestamped video frame')
        if not frames: raise ValueError('No frames in selected range')
    if digest(source) != source_hash: raise ValueError('Source changed during extraction')
    sheet = Image.new('RGB', (960, math.ceil(len(thumbs) / 4) * 160), 'white')
    draw = ImageDraw.Draw(sheet)
    for index, (t, thumb) in enumerate(thumbs):
        x, y = index % 4 * 240, index // 4 * 160
        sheet.paste(thumb, (x, y))
        draw.text((x + 3, y + 137), f'frame {index+1}  {t:.3f}s', fill='black')
    sheet_path = output / 'contact-sheet.jpg'
    sheet.save(sheet_path, quality=90)
    result = {'schema': 'bilibili-detail-frames/v1', 'source_sha256': source_hash,
              'container_start_sec': 0, 'video_stream_start_sec': video_start,
              'source_duration_sec': duration, 'requested_start_sec': start,
              'requested_end_sec_exclusive': end, 'time_mapping': 'source_pts_times_time_base',
              'frames': frames, 'contact_sheet': {'path': sheet_path.name, 'sha256': digest(sheet_path)},
              'review_status': 'not_reviewed',
              'scope': 'Full decoded video frames only. Audio not extracted or heard; no semantic judgment.'}
    (output / 'manifest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--start-sec', required=True, type=float)
    p.add_argument('--end-sec', required=True, type=float)
    args = p.parse_args()
    try:
        value = prepare(args.input, args.output, args.start_sec, args.end_sec)
    except (ValueError, OSError, ImportError, RuntimeError) as exc:
        print(json.dumps({'status': 'BLOCKED', 'error': str(exc)}, ensure_ascii=False)); return 1
    print(json.dumps({'status': 'prepared_not_reviewed', 'frames': len(value['frames']),
                      'manifest': str(args.output / 'manifest.json')}, ensure_ascii=False)); return 0


if __name__ == '__main__': raise SystemExit(main())
