#!/usr/bin/env python3
"""Export only explicitly reviewed timeline intervals. No cut detection or Feishu writes.

Dry-run is the default. A new/empty output directory is required for --render;
partial or completed exports are never overwritten. Paths may contain Unicode.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ENGINE_VERSION = "reviewed-timeline-export/1.0"


class ValidationError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def nonempty_string(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{label} must be a nonempty string")
    return value


def validate_manifest(data: dict, manifest_path: Path) -> tuple[Path, int]:
    if not isinstance(data, dict):
        raise ValidationError("manifest must be a JSON object")
    source = data.get("source")
    if not isinstance(source, dict):
        raise ValidationError("source must be an object")
    source_path = Path(nonempty_string(source.get("path"), "source.path"))
    if not source_path.is_absolute():
        source_path = manifest_path.parent / source_path
    source_path = source_path.resolve()
    if not source_path.is_file():
        raise ValidationError(f"source file does not exist: {source_path}")
    expected_hash = source.get("sha256")
    if not isinstance(expected_hash, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", expected_hash):
        raise ValidationError("source.sha256 must contain exactly 64 hexadecimal characters")
    actual_hash = sha256_file(source_path)
    if actual_hash != expected_hash.lower():
        raise ValidationError("source SHA-256 mismatch; the reviewed source has changed")
    nonempty_string(data.get("video_id"), "video_id")
    nonempty_string(data.get("revision"), "revision")
    if data.get("review_status") != "complete":
        raise ValidationError("review_status must be 'complete' before export")
    if data.get("coverage") not in {"full", "sample"}:
        raise ValidationError("coverage must be 'full' or 'sample'")
    duration = data.get("duration_sec")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
        raise ValidationError("duration_sec must be a finite positive number")
    duration_ms = round(duration * 1000)
    if duration_ms < 1:
        raise ValidationError("duration_sec must be at least 0.001 seconds")
    segments = data.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ValidationError("segments must be a nonempty list")
    seen = set()
    previous_end = 0
    for index, segment in enumerate(segments):
        label = f"segments[{index}]"
        if not isinstance(segment, dict):
            raise ValidationError(f"{label} must be an object")
        segment_id = nonempty_string(segment.get("id"), f"{label}.id")
        if segment_id in seen:
            raise ValidationError(f"duplicate segment id: {segment_id}")
        seen.add(segment_id)
        if segment.get("reviewed") is not True:
            raise ValidationError(f"{label}.reviewed must be true")
        nonempty_string(segment.get("description"), f"{label}.description")
        for key in ("tags", "evidence"):
            values = segment.get(key)
            if not isinstance(values, list) or not values:
                raise ValidationError(f"{label}.{key} must be a nonempty list of strings")
            for value in values:
                nonempty_string(value, f"{label}.{key} item")
        start, end = segment.get("start_ms"), segment.get("end_ms")
        if type(start) is not int or type(end) is not int:
            raise ValidationError(f"{label} boundaries must be integer milliseconds")
        if not 0 <= start < end <= duration_ms:
            raise ValidationError(f"{label} must satisfy 0 <= start_ms < end_ms <= {duration_ms}")
        if start < previous_end:
            raise ValidationError(f"{label} overlaps its predecessor or is out of order")
        if data["coverage"] == "full" and start != previous_end:
            raise ValidationError(f"full coverage has a gap before {label}: {previous_end}..{start} ms")
        previous_end = end
    if data["coverage"] == "full" and previous_end != duration_ms:
        raise ValidationError(f"full coverage must end at {duration_ms} ms, not {previous_end} ms")
    return source_path, duration_ms


def run_command(command: list[str]) -> subprocess.CompletedProcess:
    result = subprocess.run(command, capture_output=True, encoding="utf-8", errors="replace")
    if result.returncode:
        raise ValidationError(f"command failed ({Path(command[0]).name}): {result.stderr[-5000:].strip()}")
    return result


def probe(path: Path, ffprobe: str) -> dict:
    result = run_command([
        ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)
    ])
    try:
        return json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise ValidationError(f"invalid ffprobe output: {path}") from exc


def probe_duration(probe_result: dict) -> float:
    value = probe_result.get("format", {}).get("duration")
    try:
        duration = float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError("ffprobe did not return a numeric container duration") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise ValidationError("ffprobe returned an invalid duration")
    return duration


def frame_rate(stream: dict) -> float:
    for key in ("avg_frame_rate", "r_frame_rate"):
        value = stream.get(key, "0/0")
        try:
            numerator, denominator = value.split("/")
            fps = float(numerator) / float(denominator)
            if math.isfinite(fps) and fps > 0:
                return fps
        except (AttributeError, ValueError, ZeroDivisionError):
            pass
    return 25.0


def inspect_source(data: dict, source_path: Path, ffprobe: str) -> dict:
    metadata = probe(source_path, ffprobe)
    streams = metadata.get("streams", [])
    videos = [stream for stream in streams if stream.get("codec_type") == "video" and not stream.get("disposition", {}).get("attached_pic")]
    if not videos:
        raise ValidationError("source has no ordinary video stream")
    actual_duration = probe_duration(metadata)
    duration_tolerance = max(0.15, 2 / frame_rate(videos[0]))
    if abs(actual_duration - data["duration_sec"]) > duration_tolerance:
        raise ValidationError(f"manifest duration {data['duration_sec']:.3f}s does not match source {actual_duration:.3f}s")
    return {
        "duration_sec": actual_duration,
        "video_stream_index": videos[0]["index"],
        "fps": frame_rate(videos[0]),
        "has_audio": any(stream.get("codec_type") == "audio" for stream in streams),
        "width": videos[0].get("width"),
        "height": videos[0].get("height"),
        "duration_tolerance_sec": duration_tolerance,
    }


def stable_ascii(value: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-_")[:32] or "item"
    suffix = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    return f"{stem}-{suffix}"


def timecode(milliseconds: int) -> str:
    seconds, ms = divmod(milliseconds, 1000)
    minutes, sec = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    return f"{hours:02d}:{minute:02d}:{sec:02d}.{ms:03d}"


def build_plan(data: dict, source_path: Path, source_info: dict) -> dict:
    prefix = f"{stable_ascii(data['video_id'])}__{stable_ascii(data['revision'])}"
    records = []
    for index, segment in enumerate(data["segments"], 1):
        stem = f"{prefix}__{index:04d}"
        records.append({
            **segment,
            "sequence": index,
            "label": f"画面 {index:03d}",
            "start_timecode": timecode(segment["start_ms"]),
            "end_timecode": timecode(segment["end_ms"]),
            "duration_sec": (segment["end_ms"] - segment["start_ms"]) / 1000,
            "clip_file": f"clips/{stem}.mp4",
            "thumbnail_file": f"thumbnails/{stem}.jpg",
        })
    return {
        **data,
        "engine": ENGINE_VERSION,
        "video_id": data["video_id"],
        "revision": data["revision"],
        "review_status": data["review_status"],
        "coverage": data["coverage"],
        "duration_sec": data["duration_sec"],
        "source": {**data["source"], "path": str(source_path), "sha256": data["source"]["sha256"].lower(), "probe": source_info},
        "segments": records,
    }


def verify_clip(path: Path, expected_duration: float, source_info: dict, ffprobe: str, ffmpeg: str) -> dict:
    metadata = probe(path, ffprobe)
    videos = [stream for stream in metadata.get("streams", []) if stream.get("codec_type") == "video"]
    audios = [stream for stream in metadata.get("streams", []) if stream.get("codec_type") == "audio"]
    if len(videos) != 1 or videos[0].get("codec_name") != "h264":
        raise ValidationError(f"clip must have exactly one H.264 video stream: {path.name}")
    if source_info["has_audio"]:
        if len(audios) != 1 or audios[0].get("codec_name") != "aac":
            raise ValidationError(f"clip lost the source audio or does not contain AAC: {path.name}")
    elif audios:
        raise ValidationError(f"silent source unexpectedly produced an audio stream: {path.name}")
    duration = probe_duration(metadata)
    tolerance = max(0.15, 2 / source_info["fps"])
    if abs(duration - expected_duration) > tolerance:
        raise ValidationError(f"clip duration mismatch for {path.name}: expected {expected_duration:.3f}s, got {duration:.3f}s")
    stream_durations = {}
    for stream in videos + audios:
        kind = stream["codec_type"]
        try:
            stream_duration = float(stream["duration"])
        except (KeyError, ValueError, TypeError) as exc:
            raise ValidationError(f"clip has no verifiable {kind} duration: {path.name}") from exc
        if not math.isfinite(stream_duration) or stream_duration <= 0 or abs(stream_duration - expected_duration) > tolerance:
            raise ValidationError(f"clip {kind} duration mismatch for {path.name}: expected {expected_duration:.3f}s, got {stream_duration:.3f}s")
        stream_durations[kind] = stream_duration
    # A valid header alone is insufficient: decode every selected frame and sample.
    decoded = run_command([
        ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-xerror", "-i", str(path),
        "-map", "0:v:0", "-map", "0:a:0?", "-f", "null", "-"
    ])
    if decoded.stderr.strip():
        raise ValidationError(f"decode reported errors for {path.name}: {decoded.stderr[-2000:]}")
    return {
        "duration_sec": duration,
        "expected_duration_sec": expected_duration,
        "duration_delta_sec": round(duration - expected_duration, 6),
        "duration_tolerance_sec": tolerance,
        "video_codec": videos[0]["codec_name"],
        "audio_codec": audios[0]["codec_name"] if audios else None,
        "stream_duration_sec": stream_durations,
        "fully_decoded": True,
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def write_json(path: Path, payload: dict) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def write_csv(path: Path, plan: dict) -> None:
    fields = ["video_id", "revision", "sequence", "id", "label", "start_ms", "end_ms", "start_timecode", "end_timecode", "duration_sec", "description", "tags", "evidence", "clip_file", "thumbnail_file", "reviewed"]
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for segment in plan["segments"]:
            row = {**segment, "video_id": plan["video_id"], "revision": plan["revision"]}
            row["tags"] = json.dumps(segment["tags"], ensure_ascii=False)
            row["evidence"] = json.dumps(segment["evidence"], ensure_ascii=False)
            writer.writerow(row)


def render(plan: dict, output: Path, ffmpeg: str, ffprobe: str) -> None:
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValidationError(f"output must be new or empty; refusing to overwrite: {output}")
    output.mkdir(parents=True, exist_ok=True)
    (output / "clips").mkdir()
    (output / "thumbnails").mkdir()
    status = {"status": "rendering", "video_id": plan["video_id"], "revision": plan["revision"], "completed_segments": 0, "total_segments": len(plan["segments"])}
    write_json(output / "export_status.json", status)
    source = Path(plan["source"]["path"])
    source_info = plan["source"]["probe"]
    try:
        for segment in plan["segments"]:
            target = output / segment["clip_file"]
            partial = target.with_name(target.stem + ".partial.mp4")
            command = [
                ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-n",
                "-ss", f"{segment['start_ms'] / 1000:.3f}", "-i", str(source),
                "-t", f"{segment['duration_sec']:.3f}",
                "-map", f"0:{source_info['video_stream_index']}", "-map", "0:a:0?",
                "-sn", "-dn", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2,setpts=PTS-STARTPTS",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
            ]
            if source_info["has_audio"]:
                command += ["-af", "asetpts=PTS-STARTPTS", "-c:a", "aac", "-b:a", "192k"]
            command += ["-movflags", "+faststart", "-map_metadata", "-1", str(partial)]
            run_command(command)
            verification = verify_clip(partial, segment["duration_sec"], source_info, ffprobe, ffmpeg)
            partial.rename(target)
            thumbnail = output / segment["thumbnail_file"]
            run_command([
                ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-n",
                "-ss", f"{segment['duration_sec'] / 2:.6f}", "-i", str(target),
                "-map", "0:v:0", "-frames:v", "1", "-vf", "scale=640:-2",
                "-q:v", "2", "-update", "1", str(thumbnail)
            ])
            if not thumbnail.is_file() or thumbnail.stat().st_size < 100:
                raise ValidationError(f"missing/empty midpoint thumbnail: {thumbnail.name}")
            segment["verification"] = verification
            segment["thumbnail_sha256"] = sha256_file(thumbnail)
            status["completed_segments"] += 1
            write_json(output / "export_status.json", status)
            print(f"verified {status['completed_segments']}/{status['total_segments']}: {segment['id']}", file=sys.stderr, flush=True)
        if sha256_file(source) != plan["source"]["sha256"]:
            raise ValidationError("source changed during export; output cannot be accepted")
        plan["exported_at_utc"] = datetime.now(timezone.utc).isoformat()
        plan["status"] = "complete"
        write_json(output / "timeline.json", plan)
        write_csv(output / "timeline.csv", plan)
        status["status"] = "complete"
        write_json(output / "export_status.json", status)
    except BaseException as exc:
        status["status"] = "failed"
        status["error"] = f"{type(exc).__name__}: {exc}"
        write_json(output / "export_status.json", status)
        raise


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", type=Path, required=True, help="Reviewed JSON manifest; evidence references remain review provenance, not automatically interpreted")
    parser.add_argument("--output", type=Path, required=True, help="New or empty destination directory; default dry-run does not create it")
    parser.add_argument("--render", action="store_true", help="Actually export; without this flag only validate and print the plan")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg executable path")
    parser.add_argument("--ffprobe", default="ffprobe", help="ffprobe executable path")
    return parser


def main(argv: list[str] | None = None) -> int:
    # PowerShell redirection and child processes must receive UTF-8 even when
    # the host's legacy Windows code page cannot represent labels or paths.
    for output_stream in (sys.stdout, sys.stderr):
        if hasattr(output_stream, "reconfigure"):
            output_stream.reconfigure(encoding="utf-8", errors="replace")
    args = make_parser().parse_args(argv)
    try:
        manifest_path = args.manifest.resolve()
        with manifest_path.open("r", encoding="utf-8-sig") as stream:
            data = json.load(stream)
        source, _ = validate_manifest(data, manifest_path)
        source_info = inspect_source(data, source, args.ffprobe)
        plan = build_plan(data, source, source_info)
        if args.render:
            render(plan, args.output.resolve(), args.ffmpeg, args.ffprobe)
            print(json.dumps({"status": "complete", "output": str(args.output.resolve()), "segments": len(plan["segments"]), "coverage": plan["coverage"]}, ensure_ascii=False))
        else:
            plan["status"] = "dry_run_validated"
            plan["output"] = str(args.output.resolve())
            print(json.dumps(plan, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("ERROR: interrupted; any partial output is preserved", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
