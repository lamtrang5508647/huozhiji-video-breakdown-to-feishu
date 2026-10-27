"""Render reviewed, source-bound analysis to a fixed Docx XML chapter, locally only.

This module neither analyses media nor reads credentials nor publishes documents.
The caller supplies explicit navigation URLs. Existing Docx anchors are the
publisher's responsibility; this renderer deliberately creates no block IDs.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
from phrase_annotation import (DEFINITIONS, PROFILE, is_v9, reading_runs as reviewed_reading_runs,
                               validate_annotations, validate_runs)
from emotion_progression import LEGACY_STAGES, match_authoring, progression_label, event_label
from source_evidence import validate_source_evidence
from script_shot_alignment import segment_alignment


FAMILIES = {
    "hook_pain": ("red", DEFINITIONS['hook_pain']),
    "scene_audience": ("blue", DEFINITIONS['scene_audience']),
    "product_info": ("green", DEFINITIONS['product_info']),
    "experience_claim": (None, DEFINITIONS['experience_claim']),
    "evidence_trust": ("purple", DEFINITIONS['evidence_trust']),
    "offer_reason": ("orange", DEFINITIONS['offer_reason']),
    "action_cta": (None, DEFINITIONS['action_cta']),
    "unclassified": ("gray", DEFINITIONS['unclassified']),
}
STAGES = LEGACY_STAGES
NATIVE_COLORS = {'red': 'rgb(216,57,49)', 'green': 'rgb(46,161,33)',
                 'blue': 'rgb(36,91,219)', 'purple': 'rgb(100,37,208)',
                 'orange': 'rgb(222,120,2)', 'gray': 'rgb(143,149,158)',
                 'light-yellow': 'rgba(255,246,122,0.8)'}

SOURCE_READING_PROFILE = 'source-reading/v9.2'
LEGACY_LAYOUT_PROFILE = 'legacy/v9.1'
LAYOUT_PROFILES = (SOURCE_READING_PROFILE, LEGACY_LAYOUT_PROFILE)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def readable(value, label):
    require(isinstance(value, str) and bool(value.strip()), f"{label} must be readable text")


def esc(value):
    return html.escape(str(value), quote=True)


def tc(ms):
    return f"{ms // 60000:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"


def display_shot(shot_id):
    match = re.fullmatch(r'(?:shot-|画面)(\d+)', shot_id)
    require(bool(match), f"Unsupported shot display ID: {shot_id}")
    return f"画面{int(match.group(1)):03d}"


def shot_range(shots):
    labels = [display_shot(s) for s in shots]
    numbers = [int(label[2:]) for label in labels]
    contiguous = numbers == list(range(numbers[0], numbers[0] + len(numbers)))
    summary = labels[0] if len(labels) == 1 else labels[0] + '–' + labels[-1]
    return summary + f'（{len(shots)}个相交镜头）' + (
        '' if contiguous else '；不连续，实际清单：' + '、'.join(labels))


def color(text, family, *, profile=None):
    require(family in FAMILIES, f"Unknown functional family: {family}")
    if family == "experience_claim":
        # Live Feishu readback mapped a custom dark RGB foreground to green.
        # The supported dark foreground is the default, without text-color.
        return '<b><span background-color="light-yellow">' + esc(text) + '</span></b>'
    if family == "action_cta":
        return '<b><u>' + esc(text) + '</u></b>'
    return '<span text-color="' + FAMILIES[family][0] + '">' + esc(text) + '</span>'


def color_runs(runs, *, profile=PROFILE):
    """Render already validated exact runs; never assign a default family."""
    return ''.join(color(run['text'], run['category_family'], profile=profile) for run in runs)


def paragraph(label, text):
    return '<p><b>' + esc(label) + '</b>' + esc(text) + '</p>'


def checked_url(value):
    parsed = urlsplit(value)
    require(parsed.scheme in ("https", "http") and bool(parsed.netloc)
            and not parsed.username and not parsed.password,
            "Navigation requires an explicit HTTP(S) URL without credentials")
    return value


def reading_runs(unit, *, strict=False):
    """Explicit reviewed display annotations; never infer transcript text from notes."""
    return reviewed_reading_runs(unit, strict=strict)


def _source_reading_provenance(analysis):
    """Keep source/status metadata outside the continuously readable source."""
    kind = analysis.get('text_kind')
    if kind == 'subtitle_excerpts':
        source = '现有字幕摘录连续排版；不等同于完整原声转写。'
    elif kind == 'user_provided_copy':
        source = '用户提供文案连续排版；不据此推定原音已经听校。'
    else:
        source = '现有原文字段连续排版；文字来源类型：' + str(kind or '未提供') + '。'
    audio_status = analysis.get('audio_review_status')
    if audio_status is None and isinstance(analysis.get('review_status'), dict):
        audio_status = analysis['review_status'].get('audio')
    if isinstance(audio_status, str) and audio_status.strip():
        source += '原音校对状态：' + audio_status + '。'
    elif kind == 'subtitle_excerpts':
        source = '现有字幕摘录连续排版；原音未逐句听校。不等同于完整原声转写。'
    else:
        source += '原音逐句听校状态：未提供。'
    for key, label in (('source_text_status', '原文状态'),
                       ('transcript_status', '转写状态'), ('status', '原记录状态')):
        value = analysis.get(key)
        if isinstance(value, str) and value.strip():
            source += label + '：' + value + '。'
    if analysis.get('disclosure'):
        source += str(analysis['disclosure'])
    return paragraph('原稿来源/校对状态：', source)


def render_source_reading(analysis):
    """Return an insertable source-only reading fragment, with provenance first.

    Each source section becomes one natural paragraph. Nonempty units use the
    same U+3000 layout separator as the detailed source, without punctuation or deduplication. Existing, explicitly
    reviewed reading scopes and observation-note exclusions are respected;
    every visible source character keeps its existing functional style. No
    formula/content/manifest, navigation, file access or publication is needed.
    """
    require(isinstance(analysis, dict), 'Source reading analysis must be an object')
    kind = analysis.get('text_kind')
    require(not isinstance(kind, str) or not kind.startswith('authored_'),
            'Authored adaptations cannot be rendered as source reading')
    sections = analysis.get('sections')
    require(isinstance(sections, list) and bool(sections), 'Source reading sections required')
    require(all(isinstance(s, dict) and isinstance(s.get('id'), str) and s['id'].strip()
                for s in sections), 'Invalid source reading section identity')
    ids = [s['id'] for s in sections]
    require(len(ids) == len(set(ids)), 'Duplicate source reading section identity')
    units = analysis.get('units')
    require(isinstance(units, list) and bool(units), 'Source reading units required')
    require(all(isinstance(u, dict) and isinstance(u.get('id'), str) and u['id'].strip()
                for u in units), 'Invalid source reading unit identity')
    require(len({u['id'] for u in units}) == len(units), 'Duplicate source reading unit identity')
    positions = {section_id: index for index, section_id in enumerate(ids)}
    require(all(u.get('section_id') in positions for u in units), 'Unknown source reading section')
    order = [positions[u['section_id']] for u in units]
    require(order == sorted(order), 'Source reading sections are out of source order')
    paragraphs = {section_id: [] for section_id in ids}
    notes = []
    strict = is_v9(analysis)
    profile = analysis.get('annotation_profile')
    for unit in units:
        validate_runs(unit.get('source_text'), unit.get('phrase_runs'),
                      context=unit['id'], strict=strict)
        runs = reading_runs(unit, strict=strict)
        if runs:
            # XML otherwise normalizes literal CR/CRLF to LF while parsing.
            paragraphs[unit['section_id']].append(
                color_runs(runs, profile=profile).replace('\r', '&#13;'))
        if unit.get('display_kind') == 'observation_note' or 'reading_span' in unit:
            note = unit.get('reading_note')
            if note:
                notes.append(note)
    out = [_source_reading_provenance(analysis)]
    if notes:
        out.append(paragraph('阅读范围/缺口说明：', '；'.join(notes)))
    if not any(paragraphs.values()):
        out.append(paragraph('可读原文状态：', '当前仅有辨读注释，暂无可读原文。'))
    out.append('<h2>原稿｜标色阅读</h2>')
    out.extend('<p>' + '　'.join(paragraphs[section_id]) + '</p>'
               for section_id in ids if paragraphs[section_id])
    return '\n'.join(out) + '\n'


def _layout_profile(analysis, override=None):
    profile = override if override is not None else analysis.get('layout_profile', LEGACY_LAYOUT_PROFILE)
    require(profile in LAYOUT_PROFILES, 'Unknown layout_profile')
    return profile


def validate_inputs(analysis, manifest, content, materials_url, segments_url):
    annotation_receipt = validate_annotations(analysis, content)
    require(type(content.get('script_shot_alignment', False)) is bool,
            'script_shot_alignment must be an explicit boolean')
    mode = content.get('analysis_mode', 'legacy')
    require(mode in ('legacy', 'qianchuan_deep'), "Unknown analysis_mode")
    if mode == 'qianchuan_deep':
        readable(content.get('strategy'), 'Deep analysis strategy')
    checked_url(materials_url)
    checked_url(segments_url)
    require(materials_url != segments_url, "Material and segment navigation must be distinct")
    require(analysis['video_id'] == manifest['video_id'], "Video identity mismatch")
    require(analysis['title'] == manifest['title'], "Original title mismatch")
    require(bool(analysis['disclosure']), "Source/audio disclosure is missing")
    segs = manifest['segments']
    require(bool(segs), "No reviewed segments")
    ids = [s['id'] for s in segs]
    require(len(ids) == len(set(ids)), "Duplicate segment identity")
    require([s['id'] for s in analysis['sections']] == ids, "Analysis segment order mismatch")
    require(len(content['sections']) == len(segs), "Content segment count mismatch")
    taxonomy = analysis['taxonomy']
    require([t['family_id'] for t in taxonomy] == list(FAMILIES), "Eight-family legend order mismatch")
    for key in ('source_formula', 'reusable_formula'):
        require(bool(analysis[key]), f"Missing {key}")
        for step in analysis[key]:
            require(bool(step['label']) and (is_v9(analysis) or step.get('category_family') in FAMILIES),
                    f"Invalid {key} step")
            if not is_v9(analysis) and 'phrase_runs' in step:
                validate_runs(step['label'], step['phrase_runs'], context=key, strict=False)
            require(step.get('section_id') in ids, f"Unbound {key} step")
    units = analysis['units']
    require(len({u['id'] for u in units}) == len(units), "Duplicate subtitle unit")
    require(all(u['section_id'] in ids for u in units), "Unknown subtitle section")
    require([ids.index(u['section_id']) for u in units] == sorted(ids.index(u['section_id']) for u in units),
            "Subtitle sections are out of source order")
    for seg, sec, detail in zip(segs, analysis['sections'], content['sections']):
        require(seg['name'] == sec['name'] == detail['name'], f"Segment name mismatch: {seg['id']}")
        require(seg['start_ms'] == detail['start_ms'] and seg['end_ms'] == detail['end_ms'],
                f"Segment interval mismatch: {seg['id']}")
        require(seg['start_ms'] < seg['end_ms'], "Empty segment interval")
        require(bool(seg['source_group_ids']) and bool(seg['source_shot_ids']), "Source mapping missing")
        require(seg['script_function'] == detail['function'], "Script function mismatch")
        if mode == 'qianchuan_deep':
            for field, source in (('question', 'core_question'), ('role', 'role'), ('structure', 'source_structure')):
                readable(detail.get(field), f'Deep analysis {field}')
                readable(seg.get(source), f'Manifest {source}')
                require(detail[field] == seg[source], f'Deep analysis {field} differs from manifest: {seg["id"]}')
            readable(detail.get('conditions'), 'Deep analysis reuse conditions')
        match_authoring(seg, detail)
        validate_source_evidence(seg, manifest, {
            span['shot_id']: {'start_ms': span['segment_intersection_start_ms'],
                              'end_ms': span['segment_intersection_end_ms']}
            for span in seg.get('source_shot_spans', [])})
        if content.get('script_shot_alignment'):
            segment_alignment(seg)
        selected = [u for u in units if u['section_id'] == seg['id']]
        expected = seg['source_excerpt']['items']
        require(len(selected) == len(expected), f"Subtitle item count mismatch: {seg['id']}")
        for unit, item in zip(selected, expected):
            require(unit['source_text'] == item['text'], f"Source text mismatch: {unit['id']}")
            require(unit['source_shot_ids'] == [item['shot_id']], f"Source shot mismatch: {unit['id']}")
            cursor = 0
            for run in unit['phrase_runs']:
                require(run['category_family'] in FAMILIES, "Unknown phrase color family")
                require(run['char_start'] == cursor and run['char_end'] > cursor, "Broken phrase offsets")
                require(unit['source_text'][cursor:run['char_end']] == run['text'], "Phrase differs from source text")
                cursor = run['char_end']
            require(cursor == len(unit['source_text']), "Phrase coverage incomplete")
            reading_runs(unit, strict=is_v9(analysis))
        for key in ('structure', 'formula', 'slots', 'conditions', 'shoot', 'audience'):
            require(bool(detail[key]), f"Missing section field: {key}")
        require(bool(detail['materials']), "Replication material list missing")
        if not is_v9(analysis) and 'formula_runs' in detail:
            validate_runs(detail['formula'], detail['formula_runs'], context='section formula', strict=False)
    return annotation_receipt


def alignment_paragraphs(segment, units, detail, profile):
    """Native paragraph blocks keep existing chapter repair/readback support."""
    out = [paragraph('脚本与镜头对齐：', '按脚本段与画面交集定位，非逐句原音对齐；仿写按段、镜头建议按组。')]
    for row in segment_alignment(segment):
        out.append(paragraph(row['shot_label'] + '｜',
            f"源片 {tc(row['source_start_ms'])}–{tc(row['source_end_ms'])}；"
            f"段内 {tc(row['segment_offset_start_ms'])}–{tc(row['segment_offset_end_ms'])}；"
            f"镜头内 {tc(row['shot_offset_start_ms'])}–{tc(row['shot_offset_end_ms'])}"
            + ('；仅作边界上下文' if row['context_only'] else '')))
        excerpts = []
        for item in row['excerpt_items']:
            unit = units[item['item_index']]
            runs = reading_runs(unit, strict=profile == PROFILE)
            if runs:
                excerpts.append(color_runs(runs, profile=profile)
                                + ('（边界上下文）' if item.get('context_only') else ''))
        out.append('<p><b>原字幕摘录：</b>' + ('　'.join(excerpts) if excerpts else '本画面未提供可读字幕摘录，原声待核对。') + '</p>')
        out.append(paragraph('仿写槽位（本段参考）：', detail['slots']))
        groups = row['shot_guidance']
        if not groups:
            out.append(paragraph('对应镜头组建议：', '未提供匹配的组级建议；待对照原片补充。'))
        for group in groups:
            shots = '、'.join(display_shot(shot) for shot in group['source_shot_ids'])
            out.append(paragraph('源镜头组观察：', group.get('source_observation', '未提供') + '；适用画面：' + shots))
            plan = group.get('replication_shooting_plan', {})
            out.append(paragraph('镜头处理（组级建议）：', '；'.join(
                label + '：' + str(plan.get(key) or '未提供') for key, label in (
                    ('subject_action', '主体/动作'), ('shot_size_angle', '景别/机位'),
                    ('example_sentence_slots', '组级句式槽'), ('information_order', '信息顺序'),
                    ('edit_transition', '剪辑衔接')))))
    return out


def _render(analysis, manifest, content, materials_url, segments_url, *, layout_profile):
    profile = analysis.get('annotation_profile')
    deep = content.get('analysis_mode') == 'qianchuan_deep'
    def formula(steps):
        return ' → '.join(color_runs(s['phrase_runs'], profile=profile) if 'phrase_runs' in s
                          else color(s['label'], s['category_family'], profile=profile) for s in steps)
    source_url = manifest.get('source', {}).get('url')
    if not source_url:
        source_url = next((a['url'] for a in analysis.get('links', []) if a['label'] == '原视频'), None)
    require(bool(source_url), "Original source URL missing")
    checked_url(source_url)
    out = ['<h1>' + esc(manifest['title']) + '</h1>',
           '<p>来源视频：<a href="' + esc(source_url) + '">' + esc(manifest['video_id']) + '</a>'
           + ' · 时长' + esc(manifest['duration_sec']) + '秒 · 源片可见品牌：' + esc(content['visible_brand']) + '</p>',
           '<p>' + esc(analysis['disclosure']) + '</p>',
           '<p><b>镜头素材库：</b><a href="' + esc(materials_url) + '">素材资产库·视频片段（点击打开）</a>；'
           + '<b>脚本段视频：</b><a href="' + esc(segments_url) + '">查看'
           + str(len(manifest['segments'])) + '个内容段视频</a></p>',
           *([render_source_reading(analysis).rstrip('\n')]
             if layout_profile == SOURCE_READING_PROFILE else []),
           '<h2>原片结构公式</h2>', '<p>' + formula(analysis['source_formula']) + '</p>',
           *([paragraph('原片表达策略：', content['strategy'])] if deep else []),
           '<p>这是源片表达顺序；经历、机制、效果和认证说法属于源片主张，未因此获得事实验证。</p>',
           '<h2>可复用变量公式</h2>', '<p>' + formula(analysis['reusable_formula']) + '</p>',
           '<p>复用表达顺序和待填变量；不移植本片品牌、产品、疗效、研究、经历或入口，缺少真实材料时不得编造。</p>',
           '<h2>句内功能颜色图例</h2>']
    for entry in analysis['taxonomy']:
        family = entry['family_id']
        out.append('<p>' + color(entry['label'], family, profile=profile) + ' — ' + esc(DEFINITIONS[family]) + '</p>')
    for ordinal, (seg, detail) in enumerate(zip(manifest['segments'], content['sections']), 1):
        units = [u for u in analysis['units'] if u['section_id'] == seg['id']]
        out.append('<h2>' + esc(f"段{ordinal:02d}｜{seg['name']}") + '</h2>')
        out.append('<p><b>脚本功能：</b>' + esc(detail['function']) + '；<b>源段时间：</b>'
                   + tc(seg['start_ms']) + '–' + tc(seg['end_ms']) + '；<b>源内容组：</b>'
                   + esc('、'.join(seg['source_group_ids'])) + '</p>')
        source_runs = [reading_runs(u, strict=is_v9(analysis)) for u in units]
        source_runs = [runs for runs in source_runs if runs]
        out.append('<p>' + ('　'.join(color_runs(runs, profile=profile) for runs in source_runs)
                            if source_runs else '<b>字幕辨读状态：</b>本段暂无可读字幕摘录，待原音核对。') + '</p>')
        sequence = [s for s in analysis['source_formula'] if s['section_id'] == seg['id']]
        require(bool(sequence), f"Source functional sequence missing: {seg['id']}")
        out.append('<p><b>本段源片功能序列：</b>' + formula(sequence) + '</p>')
        if deep:
            out.append(paragraph('本段回应的问题：', detail['question']))
            out.append(paragraph('本段表达作用（非效果验证）：', detail['role']))
            out.append(paragraph('论证与声画关系：', detail['structure']))
        arc = ' → '.join(event_label(a, i) + ' ' + tc(a['evidence_time_ms']) + '：' + a['observable']
                         + '（' + '、'.join(display_shot(s) for s in a['evidence_shot_ids']) + '）'
                         for i, a in enumerate(seg['emotion']['observable_timeline']))
        label = progression_label(seg['emotion'])
        observation = (label + '；观察记录：' + (arc or '未明确，暂无可核对事件')) if label else '推进：' + arc
        out.append(paragraph('原片情绪观察（段级）：', '、'.join(detail['tags']) + '；' + observation
                             + '；依据为可观察字幕/画面，不等同于真实心理判断。'))
        shots = seg['source_shot_ids']
        out.append(paragraph('源画面范围：', shot_range(shots)))
        notes = [shot_range(u['source_shot_ids']) + '：' + u['reading_note']
                 for u in units if u.get('reading_note')]
        if notes:
            out.append(paragraph('字幕辨读备注（分析注释）：', '；'.join(notes)))
        out.append(paragraph('受众反应假设（非测量）：', detail['audience']))
        rendered_formula = (color_runs(detail['formula_runs'], profile=profile) if 'formula_runs' in detail
                            else color(detail['formula'], detail['family'], profile=profile))
        out.append('<p><b>复用公式：</b>' + rendered_formula + '</p>')
        out.append(paragraph('填空句式：', detail['slots']))
        out.append(paragraph('复刻镜头建议：', detail['shoot']))
        if deep:
            out.append(paragraph('复用边界：', detail['conditions']))
        if content.get('script_shot_alignment'):
            out.extend(alignment_paragraphs(seg, units, detail, profile))
    out.append(paragraph('复刻方向：', content['replication_outline']))
    return '\n'.join(out) + '\n'


def render(analysis, manifest, content, materials_url, segments_url, *, layout_profile=None):
    profile = _layout_profile(analysis, layout_profile)
    validate_inputs(analysis, manifest, content, materials_url, segments_url)
    return _render(analysis, manifest, content, materials_url, segments_url, layout_profile=profile)


def _effective_attrs(node):
    def normalize(value):
        value = NATIVE_COLORS.get(value, value)
        if re.fullmatch(r'#[0-9a-fA-F]{6}', value):
            return 'rgb(' + ','.join(str(int(value[i:i + 2], 16)) for i in (1, 3, 5)) + ')'
        if value.startswith(('rgb(', 'rgba(')):
            return value.replace(' ', '')
        return value
    return {key: normalize(value) if key in ('text-color', 'background-color') else value
            for key, value in node.attrib.items() if key != 'id'}


def effective_signature(node):
    """Return (block tag, attributes, per-character effective inline style).

    Server IDs and equivalent color spellings are normalized. Adjacent equal
    spans may merge. Text, links, order, bold, underline and backgrounds remain
    exact. Accepts one parsed ElementTree block, not an entire chapter wrapper.
    """
    chars = []
    def text(value, style):
        chars.extend((ch, tuple(sorted(style.items()))) for ch in (value or ''))
    def inline(el, inherited):
        require(el.tag in ('a', 'b', 'em', 'u', 'del', 'code', 'span', 'br'),
                'Unexpected inline element in fixed chapter')
        style = dict(inherited)
        if el.tag in ('b', 'em', 'u', 'del', 'code'):
            style[el.tag] = True
        style.update(_effective_attrs(el))
        text('\n' if el.tag == 'br' else el.text, style)
        for child in el:
            inline(child, style)
            text(child.tail, style)
    text(node.text, {})
    for child in node:
        inline(child, {})
        text(child.tail, {})
    return node.tag, tuple(sorted(_effective_attrs(node).items())), tuple(chars)


def validate_layout(xml, analysis, manifest, content, materials_url, segments_url, *, layout_profile=None):
    """Reject omitted/reordered fields, altered source characters/styles or navigation.

    Whitespace between blocks and existing server block IDs are ignored; text
    inside a block, nesting, style attributes and link destinations are exact.
    This validates layout/provenance, not media analysis or live Docx rendering.
    """
    profile = _layout_profile(analysis, layout_profile)
    expected = render(analysis, manifest, content, materials_url, segments_url, layout_profile=profile)
    actual_tree = ET.fromstring('<chapter>' + xml + '</chapter>')
    expected_tree = ET.fromstring('<chapter>' + expected + '</chapter>')
    h1s = [''.join(n.itertext()) for n in actual_tree.findall('h1')]
    require(h1s == [manifest['title']], "H1 must preserve the single original video title")
    h2s = [''.join(n.itertext()) for n in actual_tree.findall('h2')]
    expected_h2s = [''.join(n.itertext()) for n in expected_tree.findall('h2')]
    require(h2s == expected_h2s, "H2 order or segment headings differ from fixed layout")
    require(len(actual_tree) == len(expected_tree), "Chapter block count mismatch; missing or extra field")
    for index, (actual, wanted) in enumerate(zip(actual_tree, expected_tree)):
        require(effective_signature(actual) == effective_signature(wanted),
                f"Block {index + 1} differs: source text, color, field order, evidence or navigation")
    return {'status': 'PASS', 'video_id': manifest['video_id'], 'layout_profile': profile, 'blocks': len(actual_tree),
            'h2_count': len(h2s), 'sections': len(manifest['segments']),
            'annotation_validation': validate_annotations(analysis, content),
            'scope': 'local fixed-layout and source-text/style/navigation validation; no remote verification'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('analysis', 'manifest', 'content', 'materials-url', 'segments-url', 'output'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--validate-only', action='store_true', help='Validate the existing output without overwriting it')
    parser.add_argument('--layout-profile', choices=LAYOUT_PROFILES, help='Select source reading or historical layout')
    args = parser.parse_args(argv)
    analysis, manifest, content = [json.loads(Path(p).read_text(encoding='utf-8-sig'))
                                   for p in (args.analysis, args.manifest, args.content)]
    path = Path(args.output)
    xml = path.read_text(encoding='utf-8') if args.validate_only else render(
        analysis, manifest, content, args.materials_url, args.segments_url, layout_profile=args.layout_profile)
    receipt = validate_layout(xml, analysis, manifest, content, args.materials_url, args.segments_url, layout_profile=args.layout_profile)
    if not args.validate_only:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(xml, encoding='utf-8')
    receipt.update(output=str(path.resolve()), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == '__main__':
    main()
