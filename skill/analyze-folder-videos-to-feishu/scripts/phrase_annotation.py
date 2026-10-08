"""Shared v9 annotation structure contract; never infers or certifies semantics.

All offsets count Python Unicode characters. Formula runs cover their own label,
not transcript offsets. Source bindings prove identity, not evidential relevance.
"""
import hashlib

PROFILE = 'phrase-functions/v9'
FAMILY_SPECS = (
    ('hook_pain', '钩子/痛点', '#D83931', '注意力引入、痛点揭示、问题召回、冲突或损失叙述'),
    ('scene_audience', '场景/人群', '#245BDB', '人物身份、生活或使用场景、适用对象'),
    ('product_info', '商品信息', '#2EA121', '品牌或商品身份、成分、规格、客观使用信息'),
    ('experience_claim', '体验/效果主张', '#1F2329', '带原有限定的体验过程、感受、变化比较、机制或效果主张'),
    ('evidence_trust', '证据/信任表达', '#6425D0', '用作支持主张的研究、测试、资质、证言或明确可信性表达；不代表事实已验证'),
    ('offer_reason', '优惠/促单理由', '#DE7802', '交易优惠、价格或性价比说服、稀缺时限、明确购买理由'),
    ('action_cta', '行动号召', '#1F2329', '对观众的行动请求、购买或互动入口、具体下一步引导'),
    ('unclassified', '结构连接/未明确', '#8F959E', '中性叙事、连接或祝愿收束；待判定角色须在细标签中说明'),
)
FAMILY_IDS = tuple(row[0] for row in FAMILY_SPECS)
DEFINITIONS = {row[0]: row[3] for row in FAMILY_SPECS}
_GENERIC_TAGS = {row[0] for row in FAMILY_SPECS} | {row[1] for row in FAMILY_SPECS} | {'未明确/结构连接'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical_taxonomy():
    """Return a fresh canonical v9 legend, including actual display styles."""
    return [dict(family_id=family, label=label, color=color, style={
        'bold': family in ('experience_claim', 'action_cta'),
        'underline': family == 'action_cta',
        'background_color': '#FFF67A' if family == 'experience_claim' else None,
        'background_opacity': 0.8 if family == 'experience_claim' else None,
    }) for family, label, color, _ in FAMILY_SPECS]


def is_v9(analysis):
    return analysis.get('annotation_profile') == PROFILE


def text_sha256(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def validate_runs(text, runs, *, context='phrase', strict=True):
    """Validate complete exact coverage. No word-based role classification."""
    require(_nonempty(text), f'{context}: missing text')
    require(isinstance(runs, list) and bool(runs), f'{context}: phrase_runs required')
    cursor = 0
    for run in runs:
        require(isinstance(run, dict), f'{context}: run must be an object')
        start, end = run.get('char_start'), run.get('char_end')
        require(type(start) is int and type(end) is int and start == cursor and cursor < end <= len(text),
                f'{context}: incomplete or overlapping character coverage')
        require(run.get('text') == text[start:end], f'{context}: run changed source text')
        require(run.get('category_family') in FAMILY_IDS, f'{context}: unknown family')
        if strict:
            tag = run.get('function_tag')
            require(_nonempty(tag) and tag.strip() not in _GENERIC_TAGS,
                    f'{context}: specific function_tag required; family labels are not fine functions')
            require(_nonempty(run.get('reason')), f'{context}: reason required')
        cursor = end
    require(cursor == len(text), f'{context}: incomplete character coverage')
    return runs


def validate_reading_scope(unit, *, strict=False):
    """A declared review is checked for freshness; its judgment is not inferred."""
    kind = unit.get('display_kind', 'source_text')
    require(kind in ('source_text', 'observation_note'), 'Unknown reading provenance')
    if kind == 'observation_note':
        require(_nonempty(unit.get('reading_note')), 'Observation note needs explicit wording')
    if 'reading_span' not in unit:
        return
    source = unit['source_text']
    span = unit['reading_span']
    require(isinstance(span, dict), 'Reading span must be an object')
    start, end = span.get('char_start'), span.get('char_end')
    require(type(start) is int and type(end) is int and 0 <= start < end <= len(source),
            'Reading span must select exact source characters')
    require(_nonempty(unit.get('reading_note')), 'A selected reading span needs a provenance note')
    if strict:
        scope = unit.get('reviewed_scope')
        require(isinstance(scope, dict), 'v9 reading_span needs reviewed_scope')
        require(scope.get('approved') is True, 'Reading scope approval missing')
        require(scope.get('source_text_sha256') == text_sha256(source), 'Reading scope source hash is stale')
        require(scope.get('kept_text') == source[start:end], 'Reading scope kept_text differs')
        require(_nonempty(scope.get('reason')), 'Reading scope reason missing')


def reading_runs(unit, *, strict=False):
    """Return reviewed visible runs without mutating source evidence."""
    validate_reading_scope(unit, strict=strict)
    if unit.get('display_kind') == 'observation_note':
        return []
    source = unit['source_text']
    span = unit.get('reading_span', {'char_start': 0, 'char_end': len(source)})
    start, end = span['char_start'], span['char_end']
    runs = []
    for run in unit['phrase_runs']:
        lo, hi = max(start, run['char_start']), min(end, run['char_end'])
        if lo < hi:
            clipped = dict(run, text=source[lo:hi], char_start=lo - start, char_end=hi - start)
            runs.append(clipped)
    require(''.join(run['text'] for run in runs) == source[start:end], 'Reading span coverage differs')
    return runs


def _source_refs(step, units, context):
    references = step.get('source_unit_ids')
    require(isinstance(references, list) and bool(references)
            and all(isinstance(ref, str) for ref in references), f'{context}: source_unit_ids required')
    require(len(references) == len(set(references)), f'{context}: duplicate source_unit_ids')
    require(all(ref in units for ref in references), f'{context}: unknown source_unit_id')
    require(all(units[ref]['section_id'] == step['section_id'] for ref in references),
            f'{context}: source_unit_ids belong to another section')


def validate_annotations(analysis, content=None):
    """Return a structural receipt; legacy callers are explicitly not v9-checked.

    v9 fields: canonical taxonomy; exact unit phrase_runs; each formula step has
    section_id, source_unit_ids and phrase_runs covering label. Section formula
    strings, including content.sections, require their own formula_runs.
    """
    profile = analysis.get('annotation_profile')
    require(profile in (None, 'phrase-functions/v8', PROFILE), 'Unknown annotation_profile')
    if not is_v9(analysis):
        return {'annotation_profile': profile or 'legacy', 'annotation_structure': 'legacy_not_checked',
                'semantic_verdict': 'not_assessed', 'claim_verification': 'not_assessed'}
    require(analysis.get('taxonomy') == canonical_taxonomy(), 'v9 taxonomy label/color/style drift')
    sections_list = analysis.get('sections')
    require(isinstance(sections_list, list) and bool(sections_list), 'v9 sections required')
    require(all(isinstance(s, dict) and _nonempty(s.get('id')) for s in sections_list), 'Invalid section identity')
    sections = {s['id']: s for s in sections_list}
    require(len(sections) == len(sections_list), 'Duplicate section identity')
    unit_list = analysis.get('units')
    require(isinstance(unit_list, list) and bool(unit_list), 'v9 units required')
    require(all(isinstance(u, dict) and _nonempty(u.get('id')) for u in unit_list), 'Invalid unit identity')
    units = {u['id']: u for u in unit_list}
    require(len(units) == len(unit_list), 'Duplicate unit identity')
    for unit in unit_list:
        require(unit.get('section_id') in sections, f"Unknown unit section: {unit['id']}")
        validate_runs(unit.get('source_text'), unit.get('phrase_runs'), context=unit['id'])
        validate_reading_scope(unit, strict=True)
        for run in unit['phrase_runs']:
            refs = run.get('context_unit_ids')
            if refs is not None:
                require(isinstance(refs, list) and bool(refs) and len(refs) == len(set(refs))
                        and all(ref in units for ref in refs),
                        f"{unit['id']}: stale or invalid contextual scope references")
    for group in ('source_formula', 'reusable_formula'):
        steps = analysis.get(group)
        require(isinstance(steps, list) and bool(steps), f'{group}: steps required')
        for index, step in enumerate(steps):
            context = f'{group}[{index}]'
            require(isinstance(step, dict) and step.get('section_id') in sections, f'{context}: unknown section')
            validate_runs(step.get('label'), step.get('phrase_runs'), context=context)
            _source_refs(step, units, context)
            for run in step['phrase_runs']:
                refs = run.get('context_unit_ids')
                if refs is not None:
                    require(isinstance(refs, list) and bool(refs) and len(refs) == len(set(refs))
                            and all(ref in units for ref in refs), f'{context}: invalid contextual scope references')
        require(set(step['section_id'] for step in steps) == set(sections), f'{group}: uncovered section')
    for section in sections_list:
        if 'formula' in section:
            validate_runs(section['formula'], section.get('formula_runs'), context=f"section {section['id']} formula")
    if content is not None:
        details = content.get('sections')
        require(isinstance(details, list) and len(details) == len(sections_list), 'Content section count differs')
        for section, detail in zip(sections_list, details):
            validate_runs(detail.get('formula'), detail.get('formula_runs'), context=f"content {section['id']} formula")
            if 'formula' in section:
                require(section['formula'] == detail['formula'] and section['formula_runs'] == detail['formula_runs'],
                        f"Section/content formula differs: {section['id']}")
    return {'annotation_profile': PROFILE, 'annotation_structure': 'validated',
            'semantic_verdict': 'not_assessed', 'claim_verification': 'not_assessed',
            'units': len(units), 'source_formula_steps': len(analysis['source_formula']),
            'reusable_formula_steps': len(analysis['reusable_formula'])}


# Stable aliases for authoring and repair helpers.
validate_annotation = validate_annotations
validate_analysis = validate_annotations


def validate_supervisor_review(receipt, analysis_sha256, manifest_sha256, unit_ids):
    """Bind an independently authored receipt; this does not infer its verdict."""
    require(isinstance(receipt, dict) and receipt.get('status') == 'text_function_review_completed'
            and receipt.get('reviewer_role') == 'supervisor',
            'A worker/renderer structure PASS cannot replace supervising text review')
    require(receipt.get('analysis_sha256') == analysis_sha256
            and receipt.get('manifest_sha256') == manifest_sha256, 'Semantic review is stale')
    require(receipt.get('unit_ids') == unit_ids, 'Semantic review unit coverage differs')
    require(isinstance(receipt.get('scope'), str) and receipt['scope'].strip(), 'Review scope missing')
    return receipt
