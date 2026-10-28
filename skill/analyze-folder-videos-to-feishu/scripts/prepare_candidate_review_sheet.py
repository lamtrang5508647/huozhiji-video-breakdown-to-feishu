#!/usr/bin/env python3
"""Prepare a source-bound candidate review worksheet without deciding content."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import quote

from validate_interval_evidence import validate


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def inside(root: Path, path: Path, *, directory=False) -> Path:
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root) or (not resolved.is_dir() if directory else not resolved.is_file()):
        raise ValueError('Input is missing or outside the chosen project root')
    return resolved


def display(value) -> str:
    """Keep untrusted input as plain table text, never as Markdown/HTML markup."""
    text = str(value if value is not None else '')
    return (text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            .replace('\\', '\\\\').replace('|', '\\|').replace('[', '&#91;')
            .replace(']', '&#93;').replace('\r', ' ').replace('\n', ' '))


def csv_value(value) -> str:
    text = str(value if value is not None else '')
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) else text


def prepare(root: Path, timeline_path: Path, evidence_path: Path, output: Path) -> dict:
    root = inside(root.resolve(strict=True), root, directory=True)
    timeline_path = inside(root, timeline_path)
    evidence_path = inside(root, evidence_path)
    output = output.resolve(strict=False)
    if not output.is_relative_to(root) or output == root or output.exists():
        raise ValueError('Output must be a new directory inside the chosen project root')
    if not output.parent.is_dir():
        raise ValueError('Output parent directory must already exist')

    timeline_bytes = timeline_path.read_bytes()
    evidence_bytes = evidence_path.read_bytes()
    timeline = json.loads(timeline_bytes.decode('utf-8-sig'))
    evidence = json.loads(evidence_bytes.decode('utf-8-sig'))
    if not isinstance(timeline, dict) or not isinstance(evidence, dict):
        raise ValueError('Timeline and evidence must be JSON objects')
    source = timeline.get('source')
    if not isinstance(source, dict) or not isinstance(source.get('path'), str) or not isinstance(source.get('sha256'), str):
        raise ValueError('Timeline source path and SHA-256 are required')
    source_path = Path(source['path'])
    if not source_path.is_absolute():
        source_path = timeline_path.parent / source_path
    source_path = inside(root, source_path)
    source_sha = digest(source_path)
    if source_sha.lower() != source['sha256'].lower():
        raise ValueError('Actual source file hash differs from timeline')
    segments = timeline.get('segments')
    if not isinstance(segments, list) or not segments or any(not isinstance(x, dict) for x in segments):
        raise ValueError('Nonempty timeline segments required')
    ids = [x.get('id') for x in segments]
    if any(not isinstance(x, str) or not x.strip() for x in ids) or len(ids) != len(set(ids)):
        raise ValueError('Shot IDs must be unique strings')
    for x in segments:
        a, b = x.get('start_ms'), x.get('end_ms')
        if type(a) is not int or type(b) is not int or not 0 <= a < b:
            raise ValueError('Shot ranges must be nonnegative integer milliseconds')
    # Validate each referenced path *before* passing the index to the legacy
    # validator, so user-provided evidence cannot read outside the root.
    shots = evidence.get('shots')
    if not isinstance(shots, dict) or set(shots) != set(ids):
        raise ValueError('Evidence shot IDs differ from timeline')
    images = {}
    for index, segment in enumerate(segments):
        sid = segment['id']
        item = shots.get(sid)
        if not isinstance(item, dict):
            raise ValueError('Evidence shot must be an object')
        for role in ['start', 'middle', 'end'] + (['cut_before', 'cut_after'] if index else []):
            frame = item.get(role)
            if not isinstance(frame, dict) or not isinstance(frame.get('path'), str):
                raise ValueError(f'{sid}: missing {role} frame')
            raw = Path(frame['path'])
            path = inside(root, raw if raw.is_absolute() else evidence_path.parent / raw)
            images[(sid, role)] = path
    checked = validate(timeline_path, evidence_path)
    if checked.get('status') != 'PASS':
        raise ValueError('Interval evidence invalid: ' + '; '.join(checked.get('errors', [])))

    def link(path: Path) -> str:
        # Markdown is generated in output; `..` is allowed only after proving
        # the resolved input remains under the caller's project root.
        relative = os.path.relpath(path, output).replace('\\', '/')
        return quote(relative, safe='/.-_~')

    heading = ['# 候选画面多帧审阅工作表', '',
               '**草稿，仅供人工复核。** 本表不表示已观看、音频听校、语义/边界正确或批准发布。', '',
               f"视频：{display(timeline.get('video_id', '未提供'))}；原片 SHA-256：`{source_sha}`。", '',
               f"原时间线状态：{display(timeline.get('review_status', '未提供'))}。",
               '每镜只提供离散帧，不足以确认完整动作、字幕、转场或听觉；需要时播放原片或加密度取帧。', '',
               f'[打开原片]({link(source_path)}) ｜ [逐镜 CSV](candidate-review.csv) ｜ '
               '[审片页输入 JSON](review-events.json)', '']
    columns = ['画面', '源开始秒', '源结束秒', '候选描述', '原字幕摘录', '原字幕状态', '原标签',
               '开始帧秒', '开始帧', '中点帧秒', '中点帧', '结束帧秒', '结束帧',
               '切点前帧秒', '切点前帧', '切点后帧秒', '切点后帧', '本次审阅']
    rows = []
    events = []
    for segment in segments:
        sid, a, b = segment['id'], segment['start_ms'], segment['end_ms']
        frames = shots[sid]
        desc = segment.get('description', '')
        subtitle = segment.get('subtitle_excerpt', '')
        subtitle_status = segment.get('subtitle_status', segment.get('subtitle_excerpt_status', '未提供'))
        tags = segment.get('tags', [])
        tags_text = json.dumps(tags, ensure_ascii=False) if not isinstance(tags, str) else tags
        roles = ['start', 'middle', 'end'] + (['cut_before', 'cut_after'] if (sid, 'cut_before') in images else [])
        refs = {role: link(images[(sid, role)]) for role in roles}
        actual = {role: frames[role]['actual_pts_ms'] / 1000 for role in refs}
        heading += [f"## {display(sid)}｜{a / 1000:.3f}–{b / 1000:.3f} 秒", '',
                    f"候选描述（未由本工具审定）：{display(desc)}", '',
                    f"候选字幕摘录（原声明 {display(subtitle_status)}）：{display(subtitle)}", '',
                    f"候选标签（未由本工具审定）：{display(tags_text)}", '',
                    ' ｜ '.join(f"[{label} {actual[role]:.3f} 秒]({refs[role]})" for role, label in
                               (('start', '开始帧'), ('middle', '中点帧'), ('end', '末尾帧'))),
                    '', *([' ｜ '.join(f"[{label} {actual[role]:.3f} 秒]({refs[role]})" for role, label in
                                      (('cut_before', '切点前'), ('cut_after', '切点后'))), '']
                          if 'cut_before' in refs else []),
                    '本次画面、边界、字幕、标签与声音：待复核。', '']
        rows.append([sid, f'{a / 1000:.3f}', f'{b / 1000:.3f}', desc, subtitle, subtitle_status, tags_text,
                     f"{actual['start']:.3f}", refs['start'], f"{actual['middle']:.3f}", refs['middle'],
                     f"{actual['end']:.3f}", refs['end'],
                     f"{actual['cut_before']:.3f}" if 'cut_before' in actual else '', refs.get('cut_before', ''),
                     f"{actual['cut_after']:.3f}" if 'cut_after' in actual else '', refs.get('cut_after', ''), 'not_reviewed'])
        events.append({'start_sec': a / 1000, 'end_sec': b / 1000, 'title': sid,
                       'description_candidate': desc, 'subtitle_candidate': subtitle,
                       'review': {'visual': 'not_reviewed', 'audio': 'not_reviewed'},
                       'frame_evidence': {role: {'path': refs[role], 'actual_pts_ms': frames[role]['actual_pts_ms'],
                                                 'sha256': frames[role]['sha256']} for role in refs}})
    result = {'schema': 'bilibili-review-events/v1', 'source_sha256': source_sha,
              'timeline_sha256': hashlib.sha256(timeline_bytes).hexdigest(),
              'evidence_sha256': hashlib.sha256(evidence_bytes).hexdigest(),
              'video_id': timeline.get('video_id'), 'events': events,
              'scope': 'Candidate samples only; image meaning, actual viewing, full motion, audio and approval not assessed.'}
    if digest(source_path) != source_sha or timeline_path.read_bytes() != timeline_bytes or evidence_path.read_bytes() != evidence_bytes:
        raise ValueError('Input changed during preparation')
    # Re-read each referenced frame immediately before writing; files may be
    # shared across roles so avoid redundant hashing.
    expected = {}
    for (sid, role), path in images.items():
        declared = shots[sid][role].get('sha256')
        if path in expected and expected[path] != declared:
            raise ValueError('Conflicting hashes for a reused frame')
        expected[path] = declared
    if any(digest(path) != sha for path, sha in expected.items()):
        raise ValueError('Evidence changed during preparation')
    output.mkdir(exist_ok=False)
    with (output / 'candidate-review.md').open('x', encoding='utf8', newline='\n') as f:
        f.write('\n'.join(heading) + '\n')
    with (output / 'candidate-review.csv').open('x', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        writer.writerows([[csv_value(v) for v in row] for row in rows])
    with (output / 'review-events.json').open('x', encoding='utf8', newline='\n') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write('\n')
    return {'status': 'prepared_not_reviewed', 'shots': len(segments), 'output': str(output),
            'source_sha256': source_sha, 'publication_ready': False}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('root', 'timeline', 'evidence-index', 'output'):
        p.add_argument('--' + name, type=Path, required=True)
    a = p.parse_args(argv)
    try:
        result = prepare(a.root, a.timeline, a.evidence_index, a.output)
    except (OSError, ValueError, TypeError, KeyError, AssertionError) as exc:
        print(json.dumps({'status': 'BLOCKED', 'error': str(exc), 'publication_ready': False}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
