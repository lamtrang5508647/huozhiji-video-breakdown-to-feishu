#!/usr/bin/env python3
"""Summarize manually reviewed contiguous shot timing; no media interpretation."""
import argparse, json, math, statistics
from pathlib import Path


def summarize(data):
    if not isinstance(data, dict) or data.get("review_status") != "reviewed":
        raise ValueError("Input must declare review_status=reviewed after actual review")
    sha = data.get("source_sha256")
    if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
        raise ValueError("source_sha256 must be lowercase 64-character hex")
    start, end = data.get("coverage_start_sec"), data.get("coverage_end_sec")
    def num(x): return type(x) in (int, float) and math.isfinite(x)
    if not num(start) or not num(end) or start < 0 or end <= start:
        raise ValueError("Invalid coverage range")
    shots = data.get("shots")
    if not isinstance(shots, list) or not shots:
        raise ValueError("Reviewed shots required")
    cursor = start
    durations = []
    for shot in shots:
        if not isinstance(shot, dict): raise ValueError("Each shot must be an object")
        a,b = shot.get("start_sec"), shot.get("end_sec")
        if not num(a) or not num(b) or abs(a-cursor) > 1e-6 or b <= a or b > end + 1e-6:
            raise ValueError("Shots must be contiguous, ordered and inside coverage")
        durations.append(b-a); cursor = b
    if abs(cursor-end) > 1e-6: raise ValueError("Shot coverage incomplete")
    return {"status":"PASS", "source_sha256":sha, "coverage_start_sec":start, "coverage_end_sec":end,
            "shot_count":len(shots), "cut_count":len(shots)-1,
            "median_shot_sec":statistics.median(durations), "cut_count_per_minute":(len(shots)-1)*60/(end-start),
            "shot_durations_sec":durations, "scope":"arithmetic on supplied reviewed intervals only; no media observation or semantic verification"}


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--input",required=True,type=Path); p.add_argument("--output",type=Path); args=p.parse_args()
    try: result=summarize(json.loads(args.input.read_text(encoding="utf8")))
    except (ValueError, OSError, json.JSONDecodeError) as e:
        print(json.dumps({"status":"BLOCKED", "error":str(e)},ensure_ascii=False));return 1
    out=json.dumps(result,ensure_ascii=False,indent=2)+"\n"
    if args.output:
        if args.output.exists(): raise FileExistsError("Output exists; select a new path")
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(out,encoding="utf8")
    print(out);return 0
if __name__ == "__main__": raise SystemExit(main())
