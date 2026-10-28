#!/usr/bin/env python3
"""Build a local script/shot/editing-event handoff; never infer observations."""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import io
import json
import math
import re
from pathlib import Path, PurePosixPath
from urllib.parse import quote


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def relative_file(root, raw):
    require(isinstance(raw, str) and bool(raw) and '\\' not in raw, 'Expected a POSIX relative path')
    path = PurePosixPath(raw)
    require(not path.is_absolute() and all(x not in ('..', '.') for x in path.parts), 'Unsafe relative path')
    require(not re.match(r'^[A-Za-z]:', raw), 'Drive paths are not relative')
    resolved = (root / Path(*path.parts)).resolve(strict=True)
    require(resolved.is_relative_to(root) and resolved.is_file(), 'Missing file or path escapes handoff root')
    return resolved


def field(value):
    value = str(value)
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value


def md(value):
    return str(value).replace('\\', '\\\\').replace('|', '\\|').replace('\n', '<br>').replace('<', '&lt;').replace('>', '&gt;')


def asset_map_from_export(root, export_path):
    export_path = export_path.resolve(strict=True)
    require(export_path.is_relative_to(root), 'Export timeline must be inside handoff root')
    export = load(export_path)
    require(isinstance(export, dict) and isinstance(export.get('source'), dict)
            and isinstance(export.get('segments'), list), 'Invalid export timeline')
    assets = []
    for shot in export['segments']:
        require(isinstance(shot, dict) and isinstance(shot.get('clip_file'), str), 'Export shot has no clip path')
        clip = PurePosixPath(shot['clip_file'])
        require(not clip.is_absolute() and all(x not in ('..', '.') for x in clip.parts)
                and '\\' not in shot['clip_file'] and not re.match(r'^[A-Za-z]:', shot['clip_file']), 'Unsafe export clip path')
        path = (export_path.parent / Path(*clip.parts)).resolve(strict=True)
        require(path.is_relative_to(root) and path.is_file(), 'Export clip is missing or outside handoff root')
        proof = shot.get('verification')
        require(isinstance(proof, dict) and isinstance(proof.get('sha256'), str), 'Export clip hash is missing')
        assets.append({'shot_id': shot.get('id'), 'source_start_ms': shot.get('start_ms'),
                       'source_end_ms': shot.get('end_ms'), 'path': path.relative_to(root).as_posix(),
                       'sha256': proof['sha256']})
    return {'schema': 'editing-assets/v1', 'video_id': export.get('video_id'),
            'source_sha256': export['source'].get('sha256'), 'assets': assets}


def build(root, manifest, source, timeline, assets, events):
    # Bundled snapshot of this repository's alignment module; no dependency on
    # another installed Skill's version or the caller's Python search path.
    module_path = Path(__file__).resolve().parent / 'alignment_intersection.py'
    require(module_path.is_file(), 'Bundled alignment module is required')
    spec = importlib.util.spec_from_file_location('handoff_bundled_alignment', module_path)
    require(spec is not None and spec.loader is not None, 'Cannot load bundled alignment module')
    alignment_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(alignment_module)
    alignment = alignment_module.build_alignment(manifest)
    video_id, sha = alignment['video_id'], alignment['source_sha256'].lower()
    require(digest(source) == sha, 'Source file SHA-256 does not match manifest')
    require(digest(timeline) == alignment['timeline_sha256'].lower(), 'Timeline SHA-256 does not match manifest')
    require(isinstance(assets, dict) and assets.get('schema') == 'editing-assets/v1', 'Invalid asset map')
    require(isinstance(events, dict) and events.get('schema') == 'editing-events/v1', 'Invalid editing events')
    for obj in (assets, events):
        require(obj.get('video_id') == video_id and isinstance(obj.get('source_sha256'), str)
                and obj['source_sha256'].lower() == sha,
                'Input video identity or source hash differs')
    shots = {row['shot_id']: (row['source_shot_start_ms'], row['source_shot_end_ms']) for row in alignment['rows']}
    mapped = {}
    require(isinstance(assets.get('assets'), list), 'assets must be a list')
    for item in assets['assets']:
        require(isinstance(item, dict) and item.get('shot_id') in shots, 'Unknown shot in asset map')
        sid = item['shot_id']
        require(sid not in mapped, 'Duplicate shot asset')
        require(type(item.get('source_start_ms')) is int and type(item.get('source_end_ms')) is int
                and (item['source_start_ms'], item['source_end_ms']) == shots[sid], 'Asset range differs from source shot')
        path = relative_file(root, item.get('path'))
        require(isinstance(item.get('sha256'), str) and digest(path) == item['sha256'].lower(), 'Asset hash mismatch')
        mapped[sid] = item['path']
    duration = manifest.get('duration_sec')
    require(type(duration) in (int, float) and math.isfinite(duration) and duration > 0, 'Source duration is missing')
    duration_ms = round(duration * 1000)
    require(all(row['source_end_ms'] <= duration_ms and row['source_shot_end_ms'] <= duration_ms
                for row in alignment['rows']), 'Script or shot interval exceeds source duration')
    entries, seen = [], set()
    require(isinstance(events.get('events'), list), 'events must be a list')
    for item in events['events']:
        require(isinstance(item, dict) and isinstance(item.get('id'), str) and item['id'].strip(), 'Event id required')
        require(item['id'] not in seen, 'Duplicate event id')
        seen.add(item['id'])
        a, b = item.get('start_ms'), item.get('end_ms')
        require(type(a) is int and type(b) is int and 0 <= a < b <= duration_ms, 'Event outside source')
        require(item.get('visual_status') in ('observed_sampled', 'observed_frames', 'unreviewed'), 'Invalid visual status')
        require(item.get('audio_status') in ('not_reviewed', 'listened', 'no_audio_stream'), 'Invalid audio status')
        for key in ('observation', 'recreation', 'evidence'):
            require(isinstance(item.get(key), str) and item[key].strip(), f'Event {key} required')
        require(any(r['source_start_ms'] < b and a < r['source_end_ms'] for r in alignment['rows']),
                'Event does not intersect any script/shot row')
        entries.append(item)
    rows = []
    for row in alignment['rows']:
        relevant = [e for e in entries if e['start_ms'] < row['source_end_ms'] and row['source_start_ms'] < e['end_ms']]
        rows.append({'segment_id': row['segment_id'], 'segment_name': row['segment_name'], 'shot_id': row['shot_id'],
                     'source_start_ms': row['source_start_ms'], 'source_end_ms': row['source_end_ms'],
                     'source_shot_start_ms': row['source_shot_start_ms'], 'source_shot_end_ms': row['source_shot_end_ms'],
                     'source_text': row['source_text'], 'source_text_status': row['source_text_status'],
                     'asset_path': mapped.get(row['shot_id'], ''), 'asset_status': 'declared_mapping_hash_checked' if row['shot_id'] in mapped else 'missing',
                     'events': relevant})
    return {'schema': 'editing-handoff/v1', 'video_id': video_id, 'source_sha256': sha,
            'timeline_sha256': alignment['timeline_sha256'], 'transcript_status': alignment['transcript_status'],
            'audio_review_status': alignment['audio_review_status'], 'rows': rows,
            'scope': 'Time intersections and input hashes checked; observations are supplied by reviewer and not independently verified. Source captions are not sentence-level audio alignment.'}


def render(result):
    columns = ['video_id', 'source_sha256', 'transcript_status', 'audio_review_status',
               'segment_id', 'segment_name', 'shot_id', 'source_start_ms', 'source_end_ms',
               'source_shot_start_ms', 'source_shot_end_ms', 'source_text', 'source_text_status',
               'asset_path', 'asset_status', 'events', '源交集起始秒', '源交集结束秒', '源交集时长秒',
               '源画面时长秒', '画面观察', '复刻步骤', '证据', '视觉核对状态', '音频核对状态']
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=columns)
    writer.writeheader()
    for row in result['rows']:
        fields = {**row, **{key: result[key] for key in ('video_id', 'source_sha256', 'transcript_status', 'audio_review_status')}}
        ev = row['events']
        fields.update({'源交集起始秒': row['source_start_ms']/1000, '源交集结束秒': row['source_end_ms']/1000,
                       '源交集时长秒': (row['source_end_ms'] - row['source_start_ms'])/1000,
                       '源画面时长秒': (row['source_shot_end_ms'] - row['source_shot_start_ms'])/1000,
                       '画面观察': '\n'.join(x['observation'] for x in ev) or '未提供，待审阅',
                       '复刻步骤': '\n'.join(x['recreation'] for x in ev) or '未提供，待审阅',
                       '证据': '\n'.join(x['evidence'] for x in ev),
                       '视觉核对状态': '\n'.join(x['visual_status'] for x in ev) or '未审阅',
                       '音频核对状态': '\n'.join(x['audio_status'] for x in ev) or str(result['audio_review_status'])})
        writer.writerow({k: field(json.dumps(fields[k], ensure_ascii=False) if isinstance(fields[k], list) else fields[k]) for k in columns})
    lines = [f"# 剪辑师交接｜{md(result['video_id'])}", '',
             f"源视频 SHA-256：`{result['source_sha256']}`；时间线 SHA-256：`{result['timeline_sha256']}`。", '',
             f"语音校对状态：{md(result['audio_review_status'])}；转写状态：{md(result['transcript_status'])}。", '',
             '每行是脚本段与源画面相交范围；素材是映射文件，标注为整段画面，非交集切片。素材文件哈希及声明的源区间已核对，但未对视频帧复验映射的真实性。字幕可能只是摘录，未必对应逐句口播。观察和复刻方案由审阅者提供，程序不验证语义或是否听校。', '']
    if result.get('input_hashes'):
        lines.extend(['输入 SHA-256：' + '；'.join(f'{md(k)} `{v}`' for k, v in result['input_hashes'].items()), ''])
    last = None
    for row in result['rows']:
        if row['segment_id'] != last:
            last = row['segment_id']
            lines.extend([f"## {md(last)}｜{md(row['segment_name'])}", ''])
        path = row['asset_path']
        link = f"[打开画面素材]({quote(path, safe='/')})" if path else '素材缺失'
        lines.extend([f"### {md(row['shot_id'])}｜源交集 {row['source_start_ms']/1000:.3f}–{row['source_end_ms']/1000:.3f} 秒", '',
                      f"源画面完整区间：{row['source_shot_start_ms']/1000:.3f}–{row['source_shot_end_ms']/1000:.3f} 秒；{link}", '',
                      f"字幕（{md(row['source_text_status'])}）：{md(row['source_text'] or '未提供')}", ''])
        if not row['events']:
            lines.extend(['剪辑观察和复刻步骤：未提供，待审阅。', ''])
        for event in row['events']:
            lines.extend([f"事件 {md(event['id'])}（{event['start_ms']/1000:.3f}–{event['end_ms']/1000:.3f} 秒；画面 {md(event['visual_status'])}，音频 {md(event['audio_status'])}）",
                          f"观察：{md(event['observation'])}；证据：{md(event['evidence'])}",
                          f"复刻步骤：{md(event['recreation'])}", ''])
    return '\n'.join(lines), stream.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('root', 'manifest', 'source', 'timeline', 'events'):
        parser.add_argument('--' + key, type=Path, required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--assets', type=Path, help='Explicit source-bound shot asset map')
    group.add_argument('--export-timeline', type=Path, help='Existing shot-export timeline with clip hashes')
    parser.add_argument('--name', required=True, help='New output filename prefix within root')
    args = parser.parse_args()
    try:
        root = args.root.resolve(strict=True)
        require(root.is_dir(), 'Root must be an existing directory')
        require(re.fullmatch(r'[A-Za-z0-9_-]{1,80}', args.name) is not None, 'Unsafe output name')
        out_md, out_csv = root / (args.name + '.md'), root / (args.name + '.csv')
        require(not out_md.exists() and not out_csv.exists(), 'Output exists; choose a new name')
        assets = load(args.assets) if args.assets else asset_map_from_export(root, args.export_timeline)
        result = build(root, load(args.manifest), args.source.resolve(strict=True), args.timeline.resolve(strict=True),
                       assets, load(args.events))
        result['input_hashes'] = {'script_manifest': digest(args.manifest), 'events': digest(args.events),
                                  'assets_or_export_timeline': digest(args.assets or args.export_timeline)}
        document, table = render(result)
        with out_md.open('x', encoding='utf8', newline='\n') as f: f.write(document)
        with out_csv.open('x', encoding='utf-8-sig', newline='') as f: f.write(table)
        print(json.dumps({'status': 'built_not_semantically_verified', 'rows': len(result['rows']),
                          'missing_assets': sum(r['asset_status'] == 'missing' for r in result['rows']),
                          'markdown': out_md.name, 'csv': out_csv.name}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, ImportError, TypeError, KeyError) as exc:
        print(json.dumps({'status': 'BLOCKED', 'error': str(exc)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
