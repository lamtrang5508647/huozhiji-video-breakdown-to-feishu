"""Validate the traceability of a human-authored contextual review record.

This module validates record completeness and freshness only. A successful
receipt does not judge whether any annotation, alternative, or formula is
semantically correct.
"""
from __future__ import annotations

import hashlib
import json
import re

from phrase_annotation import FAMILY_IDS, is_v9, text_sha256, validate_annotations


PROFILE = 'context-review/v1'
RESULT = 'context_record_validated'
_HEX_SHA256 = re.compile(r'^[0-9a-f]{64}$')
_GENERIC_RATIONALES = {'checked', 'pass', '已检查', '已审核', '同上', '无误', '保留', '保持不变'}
_REVIEW_DECISIONS = {'retain', 'change', 'exclude', 'uncertain'}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def phrase_runs_sha256(runs):
    """Hash an exact phrase_runs value for explicit whole-unit nochange records."""
    encoded = json.dumps(runs, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def _specific_rationale(value, *, context, field='reading_rationale'):
    _require(_nonempty(value), f'{context}: {field} required')
    _require(value.strip().lower() not in _GENERIC_RATIONALES,
             f'{context}: generic {field} is insufficient')


def _review_decision(item, *, context):
    decision = item.get('review_decision')
    _require(decision in _REVIEW_DECISIONS,
             f'{context}: review_decision must be retain, change, exclude, or uncertain')
    return decision


def _context_refs(item, target_id, units, *, context, require_adjacent=False, allow_single_unit=False):
    refs = item.get('context_unit_ids')
    _require(isinstance(refs, list) and refs and all(isinstance(x, str) for x in refs),
             f'{context}: context_unit_ids required')
    _require(len(refs) == len(set(refs)) and target_id in refs,
             f'{context}: context references must be unique and include the target unit')
    _require(all(ref in units for ref in refs), f'{context}: stale context unit reference')
    target_section = units[target_id]['section_id']
    crosses_section = any(units[ref]['section_id'] != target_section for ref in refs)
    if crosses_section:
        _require(_nonempty(item.get('cross_section_reason')),
                 f'{context}: cross-section context needs cross_section_reason')
    if require_adjacent:
        ordered_ids = list(units)
        target_index = ordered_ids.index(target_id)
        adjacent_ids = set(ordered_ids[max(0, target_index - 1):target_index]
                           + ordered_ids[target_index + 1:target_index + 2])
        if adjacent_ids:
            _require(bool(adjacent_ids.intersection(refs)),
                     f'{context}: context must include an adjacent unit; self-reference is not enough')
        else:
            _require(allow_single_unit and _nonempty(item.get('single_unit_context_reason')),
                     f'{context}: single-unit review needs single_unit_context_reason')


def _review_target(item, units, *, context):
    unit_id = item.get('unit_id')
    _require(unit_id in units, f'{context}: unknown unit_id')
    unit = units[unit_id]
    source = unit['source_text']
    _require(item.get('unit_text_sha256') == text_sha256(source),
             f'{context}: stale unit text hash')
    start, end = item.get('char_start'), item.get('char_end')
    _require(type(start) is int and type(end) is int and 0 <= start < end <= len(source),
             f'{context}: invalid source character range')
    phrase = source[start:end]
    _require(item.get('phrase') == phrase, f'{context}: phrase differs from source text')
    _require(item.get('phrase_sha256') == text_sha256(phrase), f'{context}: stale phrase hash')
    family = item.get('selected_family')
    _require(family in FAMILY_IDS, f'{context}: invalid selected_family')
    matching = [run for run in unit['phrase_runs']
                if max(start, run['char_start']) < min(end, run['char_end'])]
    _require(bool(matching) and all(run['category_family'] == family for run in matching),
             f'{context}: selected_family differs from the annotated source span')
    _context_refs(item, unit_id, units, context=context)
    return phrase


def _index_steps(analysis):
    result = {}
    for group in ('source_formula', 'reusable_formula'):
        for index, step in enumerate(analysis[group]):
            result[(group, index)] = step
    for section in analysis['sections']:
        if 'formula' in section:
            result[('section_formula', section['id'])] = {
                'section_id': section['id'],
                'source_unit_ids': [unit['id'] for unit in analysis['units']
                                    if unit['section_id'] == section['id']],
                'label': section['formula'],
                'phrase_runs': section['formula_runs'],
            }
    return result


def validate_context_review(receipt, analysis, analysis_sha256):
    """Validate a v1 review receipt against a v9 analysis and return a safe receipt.

    The returned ``semantic_truth`` is always ``not_assessed``. This function
    checks hashes, references and required declarations; it never interprets
    prose or awards a semantic quality pass.
    """
    _require(isinstance(analysis, dict) and is_v9(analysis),
             'Context review requires a phrase-functions/v9 analysis')
    validate_annotations(analysis)
    _require(isinstance(receipt, dict), 'Context review receipt must be an object')
    _require(receipt.get('profile') == PROFILE, 'Unknown context review profile')
    _require(receipt.get('result') == RESULT, 'Context review result must be context_record_validated')
    _require(receipt.get('semantic_truth') == 'not_assessed',
             'semantic_truth must remain not_assessed')
    _require(isinstance(analysis_sha256, str) and _HEX_SHA256.fullmatch(analysis_sha256),
             'Expected analysis byte SHA-256 is invalid')
    _require(receipt.get('analysis_sha256') == analysis_sha256, 'Context review analysis hash is stale')

    unit_list = analysis['units']
    units = {unit['id']: unit for unit in unit_list}
    unresolved_count = 0
    unit_reviews = receipt.get('unit_reviews')
    _require(isinstance(unit_reviews, list), 'unit_reviews must declare every unit read with context')
    reviewed_ids = []
    for index, review in enumerate(unit_reviews):
        context = f'unit_reviews[{index}]'
        _require(isinstance(review, dict), f'{context}: expected an object')
        unit_id = review.get('unit_id')
        _require(unit_id in units, f'{context}: unknown unit_id')
        reviewed_ids.append(unit_id)
        _require(review.get('reading') == 'read_with_context',
                 f'{context}: explicit read_with_context declaration required')
        _specific_rationale(review.get('reading_rationale'), context=context)
        unit = units[unit_id]
        _require(review.get('unit_text_sha256') == text_sha256(unit['source_text']),
                 f'{context}: stale unit text hash')
        _require(review.get('phrase_runs_sha256') == phrase_runs_sha256(unit['phrase_runs']),
                 f'{context}: stale phrase_runs hash')
        decision = _review_decision(review, context=context)
        unresolved_count += decision == 'uncertain'
        _context_refs(review, unit_id, units, context=context, require_adjacent=True,
                      allow_single_unit=len(unit_list) == 1)
        if units[unit_id].get('display_kind') == 'observation_note':
            exclusion = review.get('observation_note_exclusion')
            _require(isinstance(exclusion, dict) and exclusion.get('excluded_from_source') is True
                     and _nonempty(exclusion.get('reason')),
                     f'{context}: observation note source exclusion needs an explicit reason')
    _require(len(reviewed_ids) == len(set(reviewed_ids)), 'unit_reviews contains duplicate units')
    _require(set(reviewed_ids) == set(units), 'unit_reviews must cover every analysis unit exactly once')

    decisions = receipt.get('decisions')
    _require(isinstance(decisions, list), 'decisions must be present (use [] when no specific decisions were recorded)')
    for index, decision in enumerate(decisions):
        context = f'decisions[{index}]'
        _require(isinstance(decision, dict), f'{context}: expected an object')
        kind = decision.get('decision')
        _require(kind in ('dispute', 'nochange'), f'{context}: decision must be dispute or nochange')
        if kind == 'dispute':
            _review_target(decision, units, context=context)
            _specific_rationale(decision.get('rationale'), context=context, field='rationale')
            alternative = decision.get('alternative_family')
            _require(alternative in FAMILY_IDS and alternative != decision['selected_family'],
                     f'{context}: dispute needs a distinct alternative_family')
        else:
            unit_id = decision.get('unit_id')
            _require(unit_id in units, f'{context}: unknown unit_id')
            unit = units[unit_id]
            _require(decision.get('unit_text_sha256') == text_sha256(unit['source_text']),
                     f'{context}: stale unit text hash')
            _require(decision.get('phrase_runs_sha256') == phrase_runs_sha256(unit['phrase_runs']),
                     f'{context}: stale phrase_runs hash for nochange decision')
            _context_refs(decision, unit_id, units, context=context)
            _specific_rationale(decision.get('rationale'), context=context, field='rationale')
            _require(decision.get('alternative_family') in (None, ''),
                     f'{context}: nochange must not claim an alternative family')

    steps = _index_steps(analysis)
    formula_reviews = receipt.get('formula_reviews')
    _require(isinstance(formula_reviews, list), 'formula_reviews must cover every formula step')
    seen_steps = set()
    for index, review in enumerate(formula_reviews):
        context = f'formula_reviews[{index}]'
        _require(isinstance(review, dict), f'{context}: expected an object')
        group, step_index = review.get('group'), review.get('index')
        key = (group, step_index)
        _require(key in steps, f'{context}: unknown formula step')
        _require(key not in seen_steps, f'{context}: duplicate formula step review')
        seen_steps.add(key)
        step = steps[key]
        _require(review.get('section_id') == step['section_id'], f'{context}: stale section_id')
        _require(review.get('source_unit_ids') == step['source_unit_ids'],
                 f'{context}: source_unit_ids differ from formula step')
        _specific_rationale(review.get('reading_rationale'), context=context)
        _require(review.get('label_sha256') == text_sha256(step['label']),
                 f'{context}: stale formula label hash')
        _require(review.get('phrase_runs_sha256') == phrase_runs_sha256(step['phrase_runs']),
                 f'{context}: stale formula phrase_runs hash')
        formula_decision = _review_decision(review, context=context)
        unresolved_count += formula_decision == 'uncertain'
        source_families = {run['category_family'] for unit_id in step['source_unit_ids']
                           if units[unit_id].get('display_kind') != 'observation_note'
                           for run in units[unit_id]['phrase_runs']}
        formula_families = {run['category_family'] for run in step['phrase_runs']}
        if formula_families - source_families:
            _require(_nonempty(review.get('context_rationale')),
                     f'{context}: formula family differs from source families; context_rationale required')
    _require(seen_steps == set(steps),
             'formula_reviews must cover every source, reusable, and section formula')

    return {'profile': PROFILE, 'result': RESULT, 'semantic_truth': 'not_assessed',
            'analysis_sha256': analysis_sha256, 'unit_reviews': len(unit_reviews),
            'decisions': len(decisions), 'formula_reviews': len(formula_reviews),
            'unresolved_count': unresolved_count}
