#!/usr/bin/env python3
"""Discover videos and extract local analysis assets with FFmpeg."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from validate_payload import resolve_executable


VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


class PreparationFailure(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_name(value: str, limit: int = 60) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip().rstrip(". ")
    value = value or "video"
    if value.upper() in WINDOWS_RESERVED:
        value = f"_{value}"
    return value[:limit].rstrip(". ") or "video"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_ffmpeg(executable: str, args: Sequence[str], expected_success: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [executable, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if expected_success and completed.returncode != 0:
        tail = "\n".join(completed.stderr.strip().splitlines()[-20:])
        raise PreparationFailure(f"FFmpeg failed ({completed.returncode}):\n{tail}")
    return completed


def probe_video(executable: str, path: Path) -> Dict[str, Any]:
    completed = run_ffmpeg(executable, ("-hide_banner", "-i", str(path)), expected_success=False)
    output = completed.stderr
    duration_match = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", output)
    if not duration_match:
        raise PreparationFailure("Cannot determine video duration")
    hours, minutes, seconds = duration_match.groups()
    duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)

    video_line = next((line for line in output.splitlines() if "Video:" in line), "")
    resolution_match = re.search(r"(?<!\d)(\d{2,5})x(\d{2,5})(?!\d)", video_line)
    fps_match = re.search(r"([\d.]+)\s+fps", video_line)
    if not resolution_match:
        raise PreparationFailure("Cannot determine video dimensions")
    width, height = (int(value) for value in resolution_match.groups())
    fps = float(fps_match.group(1)) if fps_match else None
    return {
        "duration_seconds": round(duration, 3),
        "width": width,
        "height": height,
        "fps": fps,
        "audio_present": any("Audio:" in line for line in output.splitlines()),
    }


def discover_videos(folder: Path, top_level_only: bool) -> List[Path]:
    iterator: Iterable[Path] = folder.iterdir() if top_level_only else folder.rglob("*")
    files = [
        path.resolve()
        for path in iterator
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
    ]
    return sorted(files, key=lambda item: str(item.relative_to(folder.resolve())).casefold())


def relative_to_work(path: Path, work_dir: Path) -> str:
    return path.relative_to(work_dir).as_posix()


def extract_video(
    ffmpeg: str, video: Path, asset_dir: Path, metadata: Dict[str, Any], work_dir: Path
) -> Dict[str, Any]:
    opening_dir = asset_dir / "opening"
    sheets_dir = asset_dir / "sheets"
    audio_dir = asset_dir / "audio"
    opening_dir.mkdir(parents=True)
    sheets_dir.mkdir(parents=True)
    audio_dir.mkdir(parents=True)

    duration = float(metadata["duration_seconds"])
    opening_paths: List[str] = []
    for seconds in (0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0):
        if seconds >= duration:
            continue
        millis = int(seconds * 1000)
        output = opening_dir / f"frame_{millis:05d}ms.jpg"
        run_ffmpeg(
            ffmpeg,
            (
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{seconds:.3f}",
                "-i",
                str(video),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                "-y",
                str(output),
            ),
        )
        if output.is_file():
            opening_paths.append(relative_to_work(output, work_dir))

    sheet_paths: List[str] = []
    contact_filter = (
        "fps=2,"
        "scale=if(gt(a\\,1)\\,512\\,-2):if(gt(a\\,1)\\,-2\\,512),"
        "tile=4x5:padding=4:margin=4:color=white"
    )
    for start in range(0, max(1, int(math.ceil(duration))), 10):
        output = sheets_dir / f"sheet_{start:05d}_{start + 10:05d}s.jpg"
        run_ffmpeg(
            ffmpeg,
            (
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                str(start),
                "-t",
                f"{min(10.0, max(0.01, duration - start)):.3f}",
                "-i",
                str(video),
                "-vf",
                contact_filter,
                "-frames:v",
                "1",
                "-q:v",
                "2",
                "-y",
                str(output),
            ),
        )
        if output.is_file():
            sheet_paths.append(relative_to_work(output, work_dir))

    audio_path: Optional[str] = None
    if metadata["audio_present"]:
        output = audio_dir / "audio_16k_mono.wav"
        run_ffmpeg(
            ffmpeg,
            (
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(video),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                "-y",
                str(output),
            ),
        )
        if output.is_file():
            audio_path = relative_to_work(output, work_dir)

    return {
        "opening_frames": opening_paths,
        "contact_sheets": sheet_paths,
        "audio_wav": audio_path,
        # These are overview assets, not frame-accurate evidence. FFmpeg's fps
        # filter may select a source frame later than its output-grid timestamp.
        # The existing path fields above remain unchanged for legacy callers.
        "visual_sampling_provenance": {
            "opening_filename_time": "requested_input_seek_time_not_source_pts",
            "contact_sheet_sampling": "ffmpeg_fps_2_then_tile",
            "contact_sheet_filename_time": "requested_window_bounds_not_each_source_pts",
            "actual_source_pts": "not_recorded",
            "scope": "Overview only; do not use filenames or grid positions as exact source-frame timestamps. Re-extract and record native PTS for precise claims.",
        },
    }


def atomic_write_json(path: Path, value: Dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--ffmpeg", help="absolute path or command name")
    parser.add_argument("--top-level-only", action="store_true")
    parser.add_argument("--limit", type=int, help="prepare only the first N sorted videos")
    args = parser.parse_args()

    folder = args.folder.expanduser().resolve()
    work_dir = args.work_dir.expanduser().resolve()
    if not folder.is_dir():
        parser.error(f"Source folder does not exist: {folder}")
    if work_dir.exists() and any(work_dir.iterdir()):
        parser.error(f"Work directory must be new or empty: {work_dir}")
    work_dir.mkdir(parents=True, exist_ok=True)

    ffmpeg = resolve_executable(args.ffmpeg, "FFMPEG_BIN", ("ffmpeg",))
    if not ffmpeg:
        parser.error("ffmpeg not found; install it, set FFMPEG_BIN, or pass --ffmpeg")

    videos = discover_videos(folder, args.top_level_only)
    if args.limit is not None:
        if args.limit < 1:
            parser.error("--limit must be at least 1")
        videos = videos[: args.limit]

    manifest: Dict[str, Any] = {
        "schema_version": 1,
        "created_at": utc_now(),
        "source_folder": str(folder),
        "work_dir": str(work_dir),
        "recursive": not args.top_level_only,
        "ffmpeg": ffmpeg,
        "supported_extensions": sorted(VIDEO_EXTENSIONS),
        "discovered_count": len(videos),
        "videos": [],
    }
    manifest_path = work_dir / "batch_manifest.json"
    atomic_write_json(manifest_path, manifest)

    failed = 0
    for index, video in enumerate(videos, start=1):
        entry: Dict[str, Any] = {
            "index": index,
            "status": "preparing",
            "source_path": str(video),
            "source_basename": video.name,
            "source_relative_path": video.relative_to(folder).as_posix(),
            "size": video.stat().st_size,
            "mtime": video.stat().st_mtime,
        }
        manifest["videos"].append(entry)
        atomic_write_json(manifest_path, manifest)
        try:
            digest = sha256_file(video)
            entry["sha256"] = digest
            asset_dir = work_dir / f"{index:04d}_{safe_name(video.stem)}_{digest[:8]}"
            asset_dir.mkdir(parents=True)
            metadata = probe_video(ffmpeg, video)
            entry.update(metadata)
            entry["asset_dir"] = relative_to_work(asset_dir, work_dir)
            entry.update(extract_video(ffmpeg, video, asset_dir, metadata, work_dir))
            entry["status"] = "prepared"
            entry["prepared_at"] = utc_now()
            atomic_write_json(asset_dir / "video_manifest.json", entry)
        except (OSError, PreparationFailure) as exc:
            failed += 1
            entry["status"] = "failed"
            entry["error"] = str(exc)
        atomic_write_json(manifest_path, manifest)

    manifest["prepared_count"] = sum(
        1 for entry in manifest["videos"] if entry["status"] == "prepared"
    )
    manifest["failed_count"] = failed
    manifest["completed_at"] = utc_now()
    atomic_write_json(manifest_path, manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0 if not failed else 2


if __name__ == "__main__":
    sys.exit(main())
