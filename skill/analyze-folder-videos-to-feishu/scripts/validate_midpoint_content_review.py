"""Check human-declared midpoint review bindings, not the truth of a caption."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from validate_interval_evidence import validate as validate_intervals


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def load(path: Path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def verify(timeline_path: Path, evidence_path: Path, review_path: Path) -> dict:
    timeline_path = timeline_path.resolve(strict=True)
    evidence_path = evidence_path.resolve(strict=True)
    review_path = review_path.resolve(strict=True)
    snapshot = {path: sha(path) for path in (timeline_path, evidence_path, review_path)}
    timeline, evidence, review = load(timeline_path), load(evidence_path), load(review_path)
    errors = []

    def check(condition, message):
        if not condition:
            errors.append(message)

    check(isinstance(timeline, dict) and isinstance(evidence, dict) and isinstance(review, dict),
          'Inputs must be JSON objects')
    if errors:
        return {'status': 'BLOCKED', 'errors': errors, 'semantic_truth': 'not_assessed'}
    interval_result = validate_intervals(timeline_path, evidence_path)
    check(interval_result.get('status') == 'PASS', 'Interval evidence failed: ' + '; '.join(interval_result.get('errors', [])))
    check(review.get('schema') == 'midpoint-content-review/v1', 'Unsupported review schema')
    check(review.get('video_id') == timeline.get('video_id'), 'Video identity mismatch')
    check(review.get('timeline_sha256') == sha(timeline_path), 'Stale timeline review')
    check(review.get('evidence_sha256') == sha(evidence_path), 'Stale interval evidence review')
    source = timeline.get('source', {})
    check(isinstance(source, dict) and isinstance(source.get('path'), str)
          and isinstance(source.get('sha256'), str), 'Timeline source path and hash required')
    if isinstance(source, dict) and isinstance(source.get('path'), str) and isinstance(source.get('sha256'), str):
        source_path = Path(source['path'])
        if not source_path.is_absolute():
            source_path = timeline_path.parent / source_path
        check(source_path.is_file() and sha(source_path).lower() == source['sha256'].lower(), 'Actual source file hash mismatch')
        check(isinstance(review.get('source_sha256'), str)
              and review['source_sha256'].lower() == source['sha256'].lower(), 'Review source hash mismatch')
    author, reviewer = review.get('author_id'), review.get('reviewer_id')
    check(isinstance(author, str) and bool(author.strip()) and isinstance(reviewer, str)
          and bool(reviewer.strip()), 'Author and reviewer identities required')
    if isinstance(author, str) and isinstance(reviewer, str):
        check(author.strip().casefold() != reviewer.strip().casefold(), 'Independent reviewer must differ from author')
    check(review.get('review_mode') == 'independent', 'Independent review declaration required')
    check(review.get('scope') == 'midpoint_only', 'Review scope must explicitly be midpoint_only')
    segments, records = timeline.get('segments'), review.get('shots')
    if not isinstance(segments, list) or not segments or not isinstance(records, list):
        errors.append('Timeline segments and review shots are required')
        segments, records = [], []
    ids = [s.get('id') for s in segments if isinstance(s, dict)]
    rids = [s.get('shot_id') for s in records if isinstance(s, dict)]
    check(len(ids) == len(segments) and all(isinstance(s, str) and s for s in ids) and len(ids) == len(set(ids)),
          'Timeline shot IDs must be unique strings')
    check(len(rids) == len(records) and all(isinstance(s, str) and s for s in rids)
          and all(isinstance(s, str) and s for s in ids) and len(rids) == len(set(rids)) and set(rids) == set(ids),
          'Review must contain each shot exactly once')
    by_id = {x.get('shot_id'): x for x in records if isinstance(x, dict)}
    frames = evidence.get('shots') if isinstance(evidence.get('shots'), dict) else {}
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        sid = segment.get('id')
        record = by_id.get(sid)
        if not isinstance(record, dict):
            continue
        label = str(sid)
        middle = frames.get(sid, {}).get('middle', {}) if isinstance(frames.get(sid, {}), dict) else {}
        description = segment.get('description')
        check(isinstance(description, str) and bool(description.strip()) and record.get('description') == description,
              label + ': description does not exactly match timeline')
        check(isinstance(middle, dict) and record.get('frame_sha256') == middle.get('sha256')
              and isinstance(middle.get('sha256'), str), label + ': midpoint frame hash mismatch')
        actual = middle.get('actual_pts_ms') if isinstance(middle, dict) else None
        claimed = record.get('actual_pts_ms')
        check(type(actual) in (int, float) and math.isfinite(actual) and type(claimed) in (int, float)
              and math.isfinite(claimed) and abs(actual - claimed) < 0.0001, label + ': midpoint PTS mismatch')
        start, end = segment.get('start_ms'), segment.get('end_ms')
        requested = middle.get('requested_ms') if isinstance(middle, dict) else None
        check(type(start) is int and type(end) is int and 0 <= start < end
              and type(requested) in (int, float) and math.isfinite(requested)
              and abs(requested - (start + end) / 2) < 0.001, label + ': evidence is not requested at this midpoint')
        check(isinstance(record.get('observed'), str) and bool(record['observed'].strip()), label + ': observed detail required')
        check(isinstance(record.get('reason'), str) and bool(record['reason'].strip()), label + ': judgment reason required')
        check(record.get('decision') == 'confirm_midpoint', label + ': needs_revision or uncertain blocks a completed record')
    for path, original_hash in snapshot.items():
        check(sha(path) == original_hash, 'Review input changed during validation: ' + path.name)
    if isinstance(source, dict) and isinstance(source.get('path'), str) and isinstance(source.get('sha256'), str):
        check(source_path.is_file() and sha(source_path).lower() == source['sha256'].lower(),
              'Source changed during validation')
    return {'status': 'BLOCKED' if errors else 'PASS_STRUCTURE_ONLY', 'video_id': timeline.get('video_id'),
            'shots': len(segments), 'errors': errors, 'semantic_truth': 'not_assessed',
            'review_independence': 'declared_only', 'publication_ready': False,
            'scope': 'Human-declared midpoint observations and unchanged inputs only; does not authenticate the reviewer, prove vision, establish description truth, whole-shot continuity, boundaries, subtitles, audio or publication readiness.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('timeline', 'evidence-index', 'review'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.output and args.output.exists():
            raise ValueError('Output exists; choose a new path')
        result = verify(args.timeline, args.evidence_index, args.review)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open('x', encoding='utf-8') as target:
                json.dump(result, target, ensure_ascii=False, indent=2)
                target.write('\n')
    except (OSError, ValueError, TypeError, KeyError, AssertionError) as exc:
        result = {'status': 'BLOCKED', 'errors': [str(exc)], 'semantic_truth': 'not_assessed'}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'PASS_STRUCTURE_ONLY' else 1


if __name__ == '__main__':
    raise SystemExit(main())
