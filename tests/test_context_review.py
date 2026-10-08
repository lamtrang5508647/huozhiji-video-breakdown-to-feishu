import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'skill' / 'analyze-folder-videos-to-feishu' / 'scripts'
sys.path.insert(0, str(SCRIPTS))

import context_review as cr
import phrase_annotation as pa


def run_for(text, family='scene_audience'):
    return {'char_start': 0, 'char_end': len(text), 'text': text,
            'category_family': family, 'function_tag': '具体语用角色', 'reason': '结合本段上下文说明'}


def make_analysis(*, observation=False, formula_family='scene_audience'):
    section = {'id': 's1'}
    units = [
        {'id': 'u1', 'section_id': 's1', 'source_text': '我在家使用。',
         'phrase_runs': [run_for('我在家使用。')]},
        {'id': 'u2', 'section_id': 's1', 'source_text': '这是补充说明。',
         'phrase_runs': [run_for('这是补充说明。')]},
    ]
    if observation:
        units.append({'id': 'u3', 'section_id': 's1', 'source_text': '画面显示瓶身。',
                      'display_kind': 'observation_note', 'reading_note': '画面注释',
                      'phrase_runs': [run_for('画面显示瓶身。', 'product_info')]})
    step = {'section_id': 's1', 'source_unit_ids': ['u1'], 'label': '使用场景',
            'phrase_runs': [run_for('使用场景', formula_family)]}
    return {'annotation_profile': pa.PROFILE, 'taxonomy': pa.canonical_taxonomy(),
            'sections': [section], 'units': units,
            'source_formula': [copy.deepcopy(step)],
            'reusable_formula': [copy.deepcopy(step)]}


def analysis_bytes_sha256(analysis):
    payload = json.dumps(analysis, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def make_receipt(analysis):
    unit_reviews = []
    for i, unit in enumerate(analysis['units']):
        neighbor = analysis['units'][i - 1 if i == len(analysis['units']) - 1 else i + 1]
        context_ids = [unit['id']] if len(analysis['units']) == 1 else [unit['id'], neighbor['id']]
        review = {'unit_id': unit['id'], 'reading': 'read_with_context',
                  'reading_rationale': '已连同相邻单元阅读，确认本句在段落中的作用',
                  'unit_text_sha256': pa.text_sha256(unit['source_text']),
                  'phrase_runs_sha256': cr.phrase_runs_sha256(unit['phrase_runs']),
                  'review_decision': 'retain',
                  'context_unit_ids': context_ids}
        if len(analysis['units']) == 1:
            review['single_unit_context_reason'] = '分析仅有这一个文本单元，以完整句内上下文作审阅范围'
        if unit.get('display_kind') == 'observation_note':
            review['observation_note_exclusion'] = {
                'excluded_from_source': True, 'reason': '这是画面观察注释，不属于口播原文'}
        unit_reviews.append(review)
    formula_reviews = []
    for group in ('source_formula', 'reusable_formula'):
        step = analysis[group][0]
        formula_reviews.append({'group': group, 'index': 0, 'section_id': 's1',
                                'source_unit_ids': ['u1'],
                                'reading_rationale': '对照引用原句检查公式步骤的表达作用',
                                'review_decision': 'retain',
                                'label_sha256': pa.text_sha256(step['label']),
                                'phrase_runs_sha256': cr.phrase_runs_sha256(step['phrase_runs'])})
    return {'profile': cr.PROFILE, 'result': cr.RESULT, 'semantic_truth': 'not_assessed',
            'analysis_sha256': analysis_bytes_sha256(analysis), 'unit_reviews': unit_reviews,
            'decisions': [], 'formula_reviews': formula_reviews}


class ContextReviewTests(unittest.TestCase):
    def validate(self, analysis, receipt):
        return cr.validate_context_review(receipt, analysis, analysis_bytes_sha256(analysis))

    def test_valid_complete_review_returns_record_only_receipt(self):
        analysis = make_analysis()
        result = self.validate(analysis, make_receipt(analysis))
        self.assertEqual(result['result'], 'context_record_validated')
        self.assertEqual(result['semantic_truth'], 'not_assessed')

    def test_missing_unit_review_is_rejected(self):
        analysis = make_analysis()
        receipt = make_receipt(analysis)
        receipt['unit_reviews'].pop()
        with self.assertRaisesRegex(ValueError, 'cover every analysis unit'):
            self.validate(analysis, receipt)

    def test_global_checked_or_units_list_cannot_replace_per_unit_read(self):
        analysis = make_analysis()
        receipt = make_receipt(analysis)
        receipt['unit_reviews'][0] = {'unit_id': 'u1', 'checked': True, 'context_unit_ids': ['u1']}
        receipt['checked'] = True
        with self.assertRaisesRegex(ValueError, 'read_with_context'):
            self.validate(analysis, receipt)

    def test_self_reference_does_not_count_as_adjacent_context(self):
        analysis = make_analysis()
        receipt = make_receipt(analysis)
        receipt['unit_reviews'][0]['context_unit_ids'] = ['u1']
        with self.assertRaisesRegex(ValueError, 'adjacent unit'):
            self.validate(analysis, receipt)

    def test_single_unit_analysis_needs_explicit_context_basis(self):
        analysis = make_analysis()
        analysis['units'] = analysis['units'][:1]
        receipt = make_receipt(analysis)
        self.assertEqual(self.validate(analysis, receipt)['unresolved_count'], 0)
        del receipt['unit_reviews'][0]['single_unit_context_reason']
        with self.assertRaisesRegex(ValueError, 'single-unit review needs'):
            self.validate(analysis, receipt)

    def test_generic_checked_rationale_is_rejected(self):
        analysis = make_analysis()
        receipt = make_receipt(analysis)
        receipt['unit_reviews'][0]['reading_rationale'] = 'checked'
        with self.assertRaisesRegex(ValueError, 'generic reading_rationale'):
            self.validate(analysis, receipt)

    def test_updated_analysis_hash_does_not_hide_stale_unit_run_hash(self):
        analysis = make_analysis()
        receipt = make_receipt(analysis)
        analysis['units'][0]['phrase_runs'][0]['reason'] = '更新后的标注说明'
        receipt['analysis_sha256'] = analysis_bytes_sha256(analysis)
        with self.assertRaisesRegex(ValueError, 'stale phrase_runs hash'):
            self.validate(analysis, receipt)

    def test_formula_ids_without_formula_reading_decision_and_hashes_are_rejected(self):
        analysis = make_analysis()
        receipt = make_receipt(analysis)
        receipt['formula_reviews'][0] = {'group': 'source_formula', 'index': 0,
            'section_id': 's1', 'source_unit_ids': ['u1']}
        with self.assertRaisesRegex(ValueError, 'reading_rationale required'):
            self.validate(analysis, receipt)

    def test_uncertain_unit_and_formula_are_counted_as_unresolved(self):
        analysis = make_analysis()
        receipt = make_receipt(analysis)
        receipt['unit_reviews'][0]['review_decision'] = 'uncertain'
        receipt['formula_reviews'][1]['review_decision'] = 'uncertain'
        result = self.validate(analysis, receipt)
        self.assertEqual(result['unresolved_count'], 2)

    def test_stale_source_hash_in_specific_decision_is_rejected(self):
        analysis = make_analysis()
        receipt = make_receipt(analysis)
        receipt['decisions'] = [{'decision': 'dispute', 'unit_id': 'u1',
            'unit_text_sha256': '0' * 64, 'char_start': 0, 'char_end': 1,
            'phrase': '我', 'phrase_sha256': pa.text_sha256('我'),
            'selected_family': 'scene_audience', 'alternative_family': 'hook_pain',
            'rationale': '争议项的语境解释', 'context_unit_ids': ['u1', 'u2']}]
        with self.assertRaisesRegex(ValueError, 'stale unit text hash'):
            self.validate(analysis, receipt)

    def test_formula_family_difference_requires_context_rationale(self):
        analysis = make_analysis(formula_family='action_cta')
        receipt = make_receipt(analysis)
        with self.assertRaisesRegex(ValueError, 'context_rationale required'):
            self.validate(analysis, receipt)
        receipt['formula_reviews'][0]['context_rationale'] = '压缩表达从场景说明转为观众下一步引导'
        receipt['formula_reviews'][1]['context_rationale'] = '复用时将场景说明改写成明确行动引导'
        self.assertEqual(self.validate(analysis, receipt)['semantic_truth'], 'not_assessed')

    def test_same_formula_and_source_family_needs_no_difference_reason(self):
        analysis = make_analysis(formula_family='scene_audience')
        receipt = make_receipt(analysis)
        self.assertEqual(self.validate(analysis, receipt)['result'], 'context_record_validated')

    def test_observation_note_must_explain_source_exclusion(self):
        analysis = make_analysis(observation=True)
        receipt = make_receipt(analysis)
        del receipt['unit_reviews'][2]['observation_note_exclusion']
        with self.assertRaisesRegex(ValueError, 'source exclusion needs an explicit reason'):
            self.validate(analysis, receipt)

    def test_explicit_nochange_decision_binds_whole_runs_and_allows_multiple_families(self):
        analysis = make_analysis()
        unit = analysis['units'][0]
        unit['phrase_runs'] = [run_for('我在', 'scene_audience'),
                               dict(run_for('家使用。', 'experience_claim'), char_start=2, char_end=6)]
        receipt = make_receipt(analysis)
        receipt['analysis_sha256'] = analysis_bytes_sha256(analysis)
        receipt['decisions'] = [{'decision': 'nochange', 'unit_id': 'u1',
            'unit_text_sha256': pa.text_sha256(unit['source_text']),
            'phrase_runs_sha256': cr.phrase_runs_sha256(unit['phrase_runs']),
            'rationale': '复核后保留现有多段分类，不做改动',
            'context_unit_ids': ['u1', 'u2']}]
        self.assertEqual(self.validate(analysis, receipt)['decisions'], 1)

    def test_section_formula_must_also_be_reviewed(self):
        analysis = make_analysis()
        analysis['sections'][0]['formula'] = '场景引入'
        analysis['sections'][0]['formula_runs'] = [run_for('场景引入')]
        receipt = make_receipt(analysis)
        receipt['analysis_sha256'] = analysis_bytes_sha256(analysis)
        with self.assertRaisesRegex(ValueError, 'formula_reviews must cover'):
            self.validate(analysis, receipt)


if __name__ == '__main__':
    unittest.main()
