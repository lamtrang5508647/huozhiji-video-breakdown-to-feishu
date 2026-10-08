#!/usr/bin/env python3
"""Detect hard cuts with FFmpeg and export clips, keyframes, and an edit list."""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
from pathlib import Path


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and p.returncode != 0:
        raise RuntimeError("Command failed:\n" + " ".join(cmd) + "\n" + p.stderr[-2000:])
    return p


def duration(ffprobe: str, video: Path) -> float:
    p = run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(video)])
    return float(p.stdout.strip())


def detect_cuts(ffmpeg: str, video: Path, threshold: float) -> list[float]:
    # showinfo emits pts_time for frames selected by the scene filter.
    p = run([ffmpeg, "-hide_banner", "-i", str(video), "-vf", f"select='gt(scene,{threshold})',showinfo", "-an", "-f", "null", "-"], check=False)
    return sorted({round(float(x), 3) for x in re.findall(r"pts_time:\s*([0-9.]+)", p.stderr)})


def safe_name(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or "video"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--threshold", type=float, default=0.28)
    ap.add_argument("--min-scene-sec", type=float, default=0.5)
    ap.add_argument("--copy", action="store_true", help="stream copy instead of frame-accurate re-encode")
    ap.add_argument("--ffmpeg", default=None)
    ap.add_argument("--ffprobe", default=None)
    args = ap.parse_args()
    video = args.video.resolve()
    if not video.is_file():
        ap.error(f"video not found: {video}")
    ffmpeg = args.ffmpeg or shutil.which("ffmpeg")
    ffprobe = args.ffprobe or shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        ap.error("ffmpeg and ffprobe are required")
    out = args.output_dir.resolve()
    shots_dir, frames_dir = out / "shots", out / "keyframes"
    shots_dir.mkdir(parents=True, exist_ok=True)
    frames_dir.mkdir(parents=True, exist_ok=True)
    total = duration(ffprobe, video)
    cuts = [x for x in detect_cuts(ffmpeg, video, args.threshold) if args.min_scene_sec <= x < total - 0.05]
    boundaries = [0.0]
    for cut in cuts:
        if cut - boundaries[-1] >= args.min_scene_sec:
            boundaries.append(cut)
    if total - boundaries[-1] < args.min_scene_sec and len(boundaries) > 1:
        boundaries.pop()
    boundaries.append(total)
    rows = []
    for idx, (start, end) in enumerate(zip(boundaries, boundaries[1:]), 1):
        stem = f"shot_{idx:04d}"
        clip = shots_dir / f"{stem}.mp4"
        frame = frames_dir / f"{stem}.jpg"
        dur = max(0.01, end - start)
        if args.copy:
            cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", str(video), "-t", f"{dur:.3f}", "-c", "copy", "-y", str(clip)]
        else:
            cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", str(video), "-t", f"{dur:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-movflags", "+faststart", "-y", str(clip)]
        run(cmd)
        run([ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", str(video), "-frames:v", "1", "-q:v", "2", "-y", str(frame)])
        rows.append({"shot": idx, "start": round(start, 3), "end": round(end, 3), "duration": round(dur, 3), "clip": str(clip), "keyframe": str(frame)})
    (out / "shots.json").write_text(json.dumps({"source": str(video), "duration": total, "threshold": args.threshold, "shots": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out / "shots.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["shot", "start", "end", "duration", "clip", "keyframe"])
        w.writeheader(); w.writerows(rows)
    # OTIO-compatible structural JSON for editors/pipelines that accept a simple timeline manifest.
    otio = {"OTIO_SCHEMA": "Timeline.1", "name": video.stem, "tracks": [{"OTIO_SCHEMA": "Track.1", "name": "video", "children": [{"OTIO_SCHEMA": "Clip.2", "name": f"shot_{r['shot']:04d}", "source_range": {"OTIO_SCHEMA": "TimeRange.1", "start_time": {"OTIO_SCHEMA": "RationalTime.1", "value": 0, "rate": 1}, "duration": {"OTIO_SCHEMA": "RationalTime.1", "value": r['duration'], "rate": 1}}, "media_reference": {"OTIO_SCHEMA": "ExternalReference.1", "target_url": r['clip']} } for r in rows]}]}
    (out / "edit-list.otio.json").write_text(json.dumps(otio, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "source": str(video), "shots": len(rows), "output": str(out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
