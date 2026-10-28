#!/usr/bin/env python3
"""Validate optional shot-to-semantic-group intersections without editing input.

A shot's primary group_id is not the same as all groups its time interval
intersects. This check does not alter primary ownership or certify semantics.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def _range(obj: dict, label: str) -> tuple[int, int]:
    a, b = obj.get('start_ms'), obj.get('end_ms')
    if type(a) is not int or type(b) is not int or not 0 <= a < b:
        raise ValueError(f'{label}: expected nonnegative integer start_ms < end_ms')
    return a, b


def check(timeline_path: Path) -> dict:
    raw = timeline_path.read_bytes()
    timeline = json.loads(raw.decode('utf-8-sig'))
    if not isinstance(timeline, dict):
        raise ValueError('Timeline must be an object')
    shots = timeline.get('segments')
    if not isinstance(shots, list) or not shots:
        raise ValueError('Timeline segments must be a nonempty list')
    if any(not isinstance(shot, dict) for shot in shots):
        raise ValueError('Each segment must be an object')
    ids = [shot.get('id') for shot in shots]
    if any(not isinstance(sid, str) or not sid.strip() for sid in ids) or len(set(ids)) != len(ids):
        raise ValueError('Segment IDs must be unique nonempty strings')

    timeline_sha256 = hashlib.sha256(raw).hexdigest()
    source = timeline.get('source')
    source_sha256 = source.get('sha256') if isinstance(source, dict) else None
    present = sum('source_group_spans' in shot for shot in shots)
    base = {'video_id': timeline.get('video_id'), 'timeline_sha256': timeline_sha256,
            'declared_source_sha256': source_sha256, 'source_file_verified': False,
            'shots_total': len(shots), 'shots_with_spans': present,
            'scope': 'Declared time intersections only; not source, frame, speech, audio, semantic or publication validation. Primary group_id is not changed.'}
    if not present:
        return {'status': 'NOT_PRESENT', **base, 'shots_missing_spans': ids,
                'errors': [], 'publication_ready': False}

    groups = timeline.get('semantic_groups')
    if not isinstance(groups, list) or not groups:
        raise ValueError('semantic_groups with timed ranges are required when source_group_spans is present')
    parsed = []
    group_ids = set()
    for index, group in enumerate(groups):
        if not isinstance(group, dict):
            raise ValueError(f'Group {index + 1} must be an object')
        gid = group.get('id')
        if not isinstance(gid, str) or not gid.strip() or gid in group_ids:
            raise ValueError('Semantic group IDs must be unique nonempty strings')
        group_ids.add(gid)
        a, b = _range(group, f'group {gid}')
        parsed.append((a, b, gid))
    parsed.sort()
    if any(parsed[i][0] < parsed[i-1][1] for i in range(1, len(parsed))):
        raise ValueError('Semantic group time ranges overlap')

    missing, errors, results = [], [], []
    for shot in shots:
        sid = shot['id']
        start, end = _range(shot, f'shot {sid}')
        expected = [{'script_section_id': gid, 'start_ms': max(start, a), 'end_ms': min(end, b)}
                    for a, b, gid in parsed if a < end and start < b]
        if 'source_group_spans' not in shot:
            missing.append(sid)
            results.append({'shot_id': sid, 'status': 'NOT_PRESENT', 'expected': expected})
            continue
        spans = shot['source_group_spans']
        actual = []
        if not isinstance(spans, list):
            errors.append(f'{sid}: source_group_spans must be a list')
            results.append({'shot_id': sid, 'status': 'MISMATCH', 'expected': expected})
            continue
        try:
            for span in spans:
                if not isinstance(span, dict):
                    raise ValueError('each span must be an object')
                gid = span.get('script_section_id')
                if not isinstance(gid, str) or not gid.strip():
                    raise ValueError('script_section_id must be a nonempty string')
                if 'id' in span and span['id'] != sid:
                    raise ValueError('span shot ID differs from parent shot')
                a, b = _range(span, 'span')
                actual.append({'script_section_id': gid, 'start_ms': a, 'end_ms': b})
        except ValueError as exc:
            errors.append(f'{sid}: {exc}')
            results.append({'shot_id': sid, 'status': 'MISMATCH', 'expected': expected})
            continue
        actual.sort(key=lambda item: (item['start_ms'], item['end_ms'], item['script_section_id']))
        fully_covered = sum(span['end_ms'] - span['start_ms'] for span in expected) == end - start
        if not expected:
            errors.append(f'{sid}: shot does not intersect any declared semantic group')
        elif not fully_covered:
            errors.append(f'{sid}: semantic groups do not cover the entire shot interval')
        elif actual != expected:
            errors.append(f'{sid}: declared source_group_spans differ from exact semantic-group intersections')
        results.append({'shot_id': sid, 'status': 'PASS' if expected and fully_covered and actual == expected else 'MISMATCH',
                        'expected': expected, 'declared': actual, 'primary_group_id': shot.get('group_id')})

    status = 'BLOCKED' if errors else ('PARTIAL' if missing else 'PASS')
    return {'status': status, **base, 'shots_missing_spans': missing, 'results': results,
            'errors': errors, 'publication_ready': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeline', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.output and args.output.exists():
            raise ValueError('Output exists; choose a new path')
        result = check(args.timeline)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open('x', encoding='utf-8', newline='\n') as handle:
                json.dump(result, handle, ensure_ascii=False, indent=2)
                handle.write('\n')
    except (OSError, ValueError, TypeError, KeyError) as exc:
        result = {'status': 'BLOCKED', 'errors': [str(exc)], 'publication_ready': False}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'PASS' else (1 if result['status'] == 'BLOCKED' else 2)


if __name__ == '__main__':
    raise SystemExit(main())
