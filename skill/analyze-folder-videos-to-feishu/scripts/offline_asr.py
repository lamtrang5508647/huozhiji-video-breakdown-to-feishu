#!/usr/bin/env python3
"""Normalize local OpenAI Whisper CLI or whisper.cpp output without remote APIs."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


class AsrFailure(RuntimeError):
    pass


def local_model_file(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def resolve_openai_model(
    model: str, model_dir: Optional[Path] = None, allow_download: bool = False
) -> str:
    """Pass a file path by default, so Whisper never enters its download branch."""
    candidate = Path(model).expanduser()
    if candidate.exists():
        if not local_model_file(candidate):
            raise AsrFailure(f"Model is not a non-empty local file: {candidate}")
        return str(candidate.resolve())
    # A typo in an explicitly supplied path must not become a model download.
    if candidate.is_absolute() or "/" in model or "\\" in model or model.endswith(".pt"):
        raise AsrFailure(f"Local model file does not exist: {candidate}")
    if allow_download:
        return model
    cache = model_dir or Path(os.getenv("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "whisper"
    # These named aliases use a different checkpoint basename in Whisper's cache.
    cache_name = {"large": "large-v3", "turbo": "large-v3-turbo"}.get(model, model)
    cached_model = cache.expanduser().resolve() / f"{cache_name}.pt"
    if not local_model_file(cached_model):
        raise AsrFailure(
            f"No non-empty local Whisper model found: {cached_model}. "
            "No download attempted. Supply --model /path/to/model.pt, "
            "--model-dir /path/to/cache, or explicitly opt in with --allow-download. "
            "Audio has not been transcribed."
        )
    return str(cached_model)


def run(argv: Sequence[str]) -> None:
    completed = subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        output = (completed.stderr or completed.stdout).strip()
        raise AsrFailure(f"ASR command failed ({completed.returncode}):\n{output}")


def read_json(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise AsrFailure(f"Cannot read ASR JSON {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise AsrFailure("ASR JSON root is not an object")
    return data


def normalize_openai_whisper(data: Dict[str, Any], engine: str) -> Dict[str, Any]:
    segments: List[Dict[str, Any]] = []
    for segment in data.get("segments", []):
        if not isinstance(segment, dict):
            continue
        value = segment.get("text", "")
        text = value.strip() if isinstance(value, str) else ""
        if text:
            segments.append(
                {
                    "start": segment.get("start"),
                    "end": segment.get("end"),
                    "text": text,
                }
            )
    value = data.get("text", "")
    text = (value.strip() if isinstance(value, str) else "") or "".join(
        segment["text"] for segment in segments
    )
    return {
        "engine": engine,
        "language": data.get("language"),
        "text": text,
        "segments": segments,
    }


def timestamp_to_seconds(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    pieces = value.replace(",", ".").split(":")
    try:
        numbers = [float(piece) for piece in pieces]
    except ValueError:
        return None
    seconds = 0.0
    for number in numbers:
        seconds = seconds * 60 + number
    return seconds


def normalize_whisper_cpp(data: Dict[str, Any]) -> Dict[str, Any]:
    source = data.get("transcription") or data.get("segments") or []
    segments: List[Dict[str, Any]] = []
    for segment in source:
        if not isinstance(segment, dict):
            continue
        value = segment.get("text", "")
        text = value.strip() if isinstance(value, str) else ""
        timestamps = segment.get("timestamps") or {}
        start = segment.get("start", timestamps.get("from"))
        end = segment.get("end", timestamps.get("to"))
        if text:
            segments.append(
                {
                    "start": timestamp_to_seconds(start),
                    "end": timestamp_to_seconds(end),
                    "text": text,
                }
            )
    return {
        "engine": "whisper-cpp",
        "language": data.get("result", {}).get("language") or data.get("language"),
        "text": "".join(segment["text"] for segment in segments),
        "segments": segments,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("--engine", choices=("auto", "openai-whisper", "whisper-cpp"), default="auto")
    parser.add_argument("--model", help="local checkpoint path or cached OpenAI Whisper model name (default: small)")
    parser.add_argument("--model-dir", type=Path, help="OpenAI Whisper cache directory; defaults to XDG_CACHE_HOME/whisper or ~/.cache/whisper")
    parser.add_argument("--allow-download", action="store_true", help="explicitly allow OpenAI Whisper to download a named model; default is local files only")
    parser.add_argument("--language", default="zh")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--whisper", help="path to OpenAI Whisper CLI")
    parser.add_argument("--whisper-cpp", help="path to whisper.cpp executable")
    args = parser.parse_args()

    audio = args.audio.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    if not audio.is_file():
        parser.error(f"Audio file does not exist: {audio}")
    output_dir.mkdir(parents=True, exist_ok=True)

    openai_cli = args.whisper or shutil.which("whisper")
    cpp_cli = args.whisper_cpp or shutil.which("whisper-cli") or shutil.which("main")
    engine = args.engine
    if engine == "auto":
        engine = "openai-whisper" if openai_cli else "whisper-cpp" if cpp_cli else ""
    if not engine:
        parser.error("No offline ASR executable found; install one explicitly, do not guess the transcript")

    try:
        if engine == "openai-whisper":
            if not openai_cli:
                raise AsrFailure("OpenAI Whisper CLI executable not found")
            model = resolve_openai_model(args.model or "small", args.model_dir, args.allow_download)
            # Whisper can exit successfully after failing an individual audio file.
            # A fresh directory prevents reuse of a transcript from an older run.
            with tempfile.TemporaryDirectory(prefix="asr-run-", dir=output_dir) as raw_dir:
                command = [
                    openai_cli,
                    str(audio),
                    "--model",
                    model,
                    "--language",
                    args.language,
                    "--task",
                    "transcribe",
                    "--output_dir",
                    raw_dir,
                    "--output_format",
                    "json",
                ]
                if args.model_dir:
                    command.extend(("--model_dir", str(args.model_dir.expanduser().resolve())))
                run(command)
                raw_path = Path(raw_dir) / f"{audio.stem}.json"
                normalized = normalize_openai_whisper(read_json(raw_path), "openai-whisper")
                shutil.copyfile(raw_path, output_dir / raw_path.name)
        else:
            if not cpp_cli:
                raise AsrFailure("whisper.cpp executable not found")
            if not args.model:
                raise AsrFailure("whisper.cpp requires --model with a local model file")
            model_path = Path(args.model).expanduser().resolve()
            if not local_model_file(model_path):
                raise AsrFailure(f"whisper.cpp model is not a non-empty local file: {model_path}")
            with tempfile.TemporaryDirectory(prefix="asr-run-", dir=output_dir) as raw_dir:
                prefix = Path(raw_dir) / "whisper_cpp_raw"
                run((
                    cpp_cli,
                    "-m",
                    str(model_path),
                    "-f",
                    str(audio),
                    "-l",
                    args.language,
                    "-oj",
                    "-of",
                    str(prefix),
                ))
                raw_path = prefix.with_suffix(".json")
                normalized = normalize_whisper_cpp(read_json(raw_path))
                shutil.copyfile(raw_path, output_dir / raw_path.name)

        if not normalized["text"].strip():
            raise AsrFailure("ASR produced no transcript text; no usable transcript was verified")
        normalized.update({"transcript_status": "asr_draft", "human_verified": False})
        normalized_path = output_dir / "transcript.json"
        with normalized_path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(normalized, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        (output_dir / "transcript.txt").write_text(
            normalized["text"] + "\n", encoding="utf-8"
        )
        print(json.dumps({"ok": True, "output": str(normalized_path), **normalized}, ensure_ascii=False, indent=2))
        return 0
    except (AsrFailure, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
