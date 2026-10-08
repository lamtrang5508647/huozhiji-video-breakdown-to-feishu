"""V9 structure/rendering regressions. These tests do not certify source semantics."""
import copy
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unittest
import xml.etree.ElementTree as ET

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skill/analyze-folder-videos-to-feishu/scripts'
sys.path.insert(0, str(SCRIPTS))
import phrase_annotation as annotation
import render_platform_chapter as docx
import render_script_analysis as local_html

spec = importlib.util.spec_from_file_location('legacy_layout_tests', SCRIPTS / 'test_render_platform_chapter.py')
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)


def runs(parts):
    result, offset = [], 0
    for text, family, tag in parts:
        result.append(dict(text=text, char_start=offset, char_end=offset + len(text),
                           category_family=family, function_tag=tag,
                           reason='测试夹具只验证角色字段和样式传递，不证明语义判断。'))
        offset += len(text)
    return result


def fixture():
    a, m, c = legacy.fixture()
    a.update(annotation_profile=annotation.PROFILE, taxonomy=annotation.canonical_taxonomy(),
             text_kind='subtitle_excerpts')
    # A real disputed subtitle retains its uncertainty across the entire claim.
    text = '不知道是不是我的错觉；最近感觉头发没有那么油了'
    unit = a['units'][0]
    unit.update(source_text=text, phrase_runs=runs([(text, 'experience_claim', '带保留的体验感受')]),
                locator_note='结构测试引用真实字幕；未进行原音复核')
    m['segments'][0]['source_excerpt']['items'][0]['text'] = text
    source_parts = [('带保留的体验', 'experience_claim', '限定体验主张'),
                    (' → ', 'unclassified', '公式顺序连接'),
                    ('入口提示', 'action_cta', '评论区入口提示')]
    reuse_parts = [('[具体体验]', 'experience_claim', '体验变量槽'),
                   (' → ', 'unclassified', '公式顺序连接'),
                   ('[行动入口]', 'action_cta', '行动变量槽')]
    for key, parts in [('source_formula', source_parts), ('reusable_formula', reuse_parts)]:
        a[key] = [dict(label=''.join(p[0] for p in parts), section_id='test-01',
                       source_unit_ids=['unit-01'], phrase_runs=runs(parts))]
    formula = a['reusable_formula'][0]['label']
    formula_runs = copy.deepcopy(a['reusable_formula'][0]['phrase_runs'])
    a['sections'][0].update(formula=formula, formula_runs=formula_runs)
    c['sections'][0].update(formula=formula, formula_runs=copy.deepcopy(formula_runs))
    return a, m, c


class PhraseAnnotationTests(unittest.TestCase):
    def setUp(self):
        self.a, self.m, self.c = fixture()

    def check_both(self, a=None, c=None):
        a, c = a or self.a, c or self.c
        local_html.validate(a)
        return docx.validate_inputs(a, self.m, c, legacy.MATERIALS, legacy.SEGMENTS)

    def rejected_both(self):
        for call in (lambda: local_html.validate(self.a),
                     lambda: docx.validate_inputs(self.a, self.m, self.c, legacy.MATERIALS, legacy.SEGMENTS)):
            with self.assertRaises(ValueError):
                call()

    def test_validated_structure_never_claims_semantic_pass(self):
        receipt = self.check_both()
        self.assertEqual(receipt['annotation_structure'], 'validated')
        self.assertEqual(receipt['semantic_verdict'], 'not_assessed')
        self.assertEqual(receipt['claim_verification'], 'not_assessed')

    def test_legacy_is_compatible_and_explicitly_not_v9_checked(self):
        a, m, c = legacy.fixture()
        xml = docx.render(a, m, c, legacy.MATERIALS, legacy.SEGMENTS)
        receipt = docx.validate_layout(xml, a, m, c, legacy.MATERIALS, legacy.SEGMENTS)
        self.assertEqual(receipt['annotation_validation']['annotation_structure'], 'legacy_not_checked')
        self.assertEqual(receipt['annotation_validation']['semantic_verdict'], 'not_assessed')

    def test_taxonomy_is_fresh_and_has_actual_native_styles(self):
        tax = annotation.canonical_taxonomy()
        self.assertEqual([t['color'] for t in tax],
                         ['#D83931', '#245BDB', '#2EA121', '#1F2329', '#6425D0', '#DE7802', '#1F2329', '#8F959E'])
        self.assertEqual(tax[3]['style']['background_color'], '#FFF67A')
        self.assertTrue(tax[6]['style']['underline'])
        tax[3]['style']['bold'] = False
        self.assertTrue(annotation.canonical_taxonomy()[3]['style']['bold'])

    def test_custom_label_color_or_style_drift_rejected(self):
        baseline = copy.deepcopy(self.a)
        for field, value in [('label', '已验证疗效'), ('color', '#000000'), ('style', {})]:
            with self.subTest(field=field):
                self.a = copy.deepcopy(baseline)
                self.a['taxonomy'][0][field] = value
                self.rejected_both()

    def test_missing_tags_reasons_and_family_only_tags_rejected(self):
        baseline = copy.deepcopy(self.a)
        for field, value in [('function_tag', None), ('function_tag', '体验/效果主张'), ('reason', '  ')]:
            with self.subTest(field=field, value=value):
                self.a = copy.deepcopy(baseline)
                self.a['units'][0]['phrase_runs'][0][field] = value
                self.rejected_both()

    def test_full_text_and_unicode_offsets_are_exact(self):
        r = runs([('不确定🙂', 'experience_claim', '含不确定限定的体验')])
        annotation.validate_runs('不确定🙂', r)
        r[0]['char_end'] += 1
        with self.assertRaises(ValueError):
            annotation.validate_runs('不确定🙂', r)
        self.a['units'][0]['phrase_runs'][0]['text'] = '最近感觉头发没有那么油了'
        self.rejected_both()

    def test_formula_cannot_fall_back_to_section_family(self):
        for key in ('source_formula', 'reusable_formula'):
            with self.subTest(key=key):
                a = copy.deepcopy(self.a)
                del a[key][0]['phrase_runs']
                a[key][0]['category_family'] = 'hook_pain'
                with self.assertRaisesRegex(ValueError, 'phrase_runs'):
                    annotation.validate_annotations(a)

    def test_formula_references_must_exist_in_same_section(self):
        for refs in ([], ['unknown'], ['unit-01', 'unit-01']):
            with self.subTest(refs=refs):
                a = copy.deepcopy(self.a)
                a['source_formula'][0]['source_unit_ids'] = refs
                with self.assertRaises(ValueError):
                    annotation.validate_annotations(a)
        self.a['sections'].append(dict(id='other', name='另段'))
        self.a['source_formula'][0]['section_id'] = 'other'
        self.rejected_both()

    def test_section_formula_needs_its_own_exact_runs(self):
        del self.a['sections'][0]['formula_runs']
        self.rejected_both()
        self.a, self.m, self.c = fixture()
        del self.c['sections'][0]['formula_runs']
        with self.assertRaisesRegex(ValueError, 'phrase_runs'):
            docx.validate_inputs(self.a, self.m, self.c, legacy.MATERIALS, legacy.SEGMENTS)

    def test_reading_crop_needs_fresh_explicit_scope_review(self):
        unit = self.a['units'][0]
        unit.update(reading_span={'char_start': 11, 'char_end': len(unit['source_text'])},
                    reading_note='边界测试，不代表批准删除限定。')
        self.rejected_both()
        unit['reviewed_scope'] = dict(source_text_sha256=annotation.text_sha256(unit['source_text']),
                                      kept_text=unit['source_text'][11:], reason='测试显式审批字段的绑定，不判定语言。',
                                      approved=True)
        self.check_both()
        for key, value in [('approved', 1), ('source_text_sha256', 'stale'), ('kept_text', '改写'), ('reason', '')]:
            old = unit['reviewed_scope'][key]
            unit['reviewed_scope'][key] = value
            with self.subTest(key=key):
                self.rejected_both()
            unit['reviewed_scope'][key] = old

    def test_negation_is_not_a_keyword_ban(self):
        text = '提醒大家千万不要自己搜着去买'
        annotation.validate_runs(text, runs([(text, 'action_cta', '防错购行动提醒')]))
        # A single complete qualified experience may legitimately have one color.
        self.check_both()

    def test_docx_formula_uses_runs_and_signature_ignores_span_merging(self):
        xml = docx.render(self.a, self.m, self.c, legacy.MATERIALS, legacy.SEGMENTS)
        tree = ET.fromstring('<chapter>' + xml + '</chapter>')
        formulas = [n for n in tree if n.tag == 'p' and ''.join(n.itertext()) == self.a['source_formula'][0]['label']]
        self.assertEqual(len(formulas), 1)
        signature = docx.effective_signature(formulas[0])
        styles = dict(signature[2][0][1])
        self.assertEqual(styles['background-color'], 'rgba(255,246,122,0.8)')
        self.assertNotIn('text-color', styles)
        self.assertTrue(dict(signature[2][-1][1])['u'])
        self.assertNotIn('text-color', dict(signature[2][-1][1]))
        changed = xml.replace('background-color="light-yellow"', 'background-color="white"', 1)
        with self.assertRaises(ValueError):
            docx.validate_layout(changed, self.a, self.m, self.c, legacy.MATERIALS, legacy.SEGMENTS)
        first = ET.fromstring('<p><span text-color="red">甲</span><span text-color="#D83931">乙</span></p>')
        second = ET.fromstring('<p><span text-color="rgb(216, 57, 49)">甲乙</span></p>')
        self.assertEqual(docx.effective_signature(first), docx.effective_signature(second))

    def test_v9_dark_default_has_no_explicit_foreground(self):
        for family, expected in (
            ('experience_claim', '<b><span background-color="light-yellow">原文</span></b>'),
            ('action_cta', '<b><u>原文</u></b>'),
        ):
            with self.subTest(family=family):
                rendered = docx.color_runs(runs([('原文', family, '测试默认文字样式')]))
                self.assertEqual(rendered, expected)
                tree = ET.fromstring('<p>' + rendered + '</p>')
                self.assertFalse(any('text-color' in node.attrib for node in tree.iter()))

    def test_live_green_foreground_drift_still_fails_strict_comparison(self):
        xml = docx.render(self.a, self.m, self.c, legacy.MATERIALS, legacy.SEGMENTS)
        # This records the actual failure shape reported by the live pilot;
        # it does not simulate support for custom RGB foreground input.
        mutations = (
            ('<span background-color="light-yellow">',
             '<span background-color="light-yellow" text-color="rgb(46,161,33)">'),
            ('<b><u>入口提示</u></b>',
             '<b><u><span text-color="rgb(46,161,33)">入口提示</span></u></b>'),
        )
        for before, after in mutations:
            with self.subTest(before=before):
                self.assertIn(before, xml)
                with self.assertRaisesRegex(ValueError, 'Block'):
                    docx.validate_layout(xml.replace(before, after, 1), self.a, self.m, self.c,
                                         legacy.MATERIALS, legacy.SEGMENTS)

    def test_html_embeds_original_evidence_and_reviewed_display(self):
        self.a['units'][0].update(display_kind='observation_note', reading_note='这是辨读状态说明。')
        self.a.update(audience_hypothesis='整片受众假设（非测量）：只作推测',
                      claim_boundaries=['不可直接照搬：主张未经核实'],
                      material_requirements=['复刻素材清单：原素材需另行确认'],
                      whole_video={'adaptation_checks': ['内部保留的复用边界检查'],
                                   'assets_checklist': ['内部保留的素材核验清单']})
        self.a['sections'][0]['audience'] = '保留段级受众反应假设（非测量）'
        original = copy.deepcopy(self.a)
        html = local_html.render(self.a)
        payload = json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S)[1])
        self.assertEqual(payload['units'][0]['source_text'], self.a['units'][0]['source_text'])
        self.assertEqual(payload['units'][0]['display_runs'], [])
        self.assertNotIn('display_runs', self.a['units'][0])
        self.assertEqual(payload['annotation_validation']['semantic_verdict'], 'not_assessed')

        def contains_key(value, key):
            if isinstance(value, dict):
                return key in value or any(contains_key(item, key) for item in value.values())
            if isinstance(value, list):
                return any(contains_key(item, key) for item in value)
            return False

        for key in local_html.OMITTED_DEFAULT_PRESENTATION_FIELDS:
            self.assertFalse(contains_key(payload, key), key)
        for label in ('整片受众假设（非测量）', '不可直接照搬', '复刻素材清单',
                      '复刻需要准备的事实与素材'):
            self.assertNotIn(label, html)
        self.assertEqual(payload['sections'][0]['audience'], '保留段级受众反应假设（非测量）')
        self.assertEqual(self.a, original)

    @unittest.skipUnless(shutil.which('node'), 'Node is needed to execute the actual copy callback')
    def test_copy_callback_matches_docx_scope_for_observation_and_crop(self):
        original = copy.deepcopy(self.a['units'][0])
        self.a['units'][0].update(display_kind='observation_note', reading_note='这是观察注释，不能作为口播复制。')
        plain = dict(original, id='plain-unit', source_shot_ids=['shot-0002'])
        cropped_text = '导读｜保留这段原文'
        cropped = dict(original, id='cropped-unit', source_text=cropped_text, source_shot_ids=['shot-0003'],
                       phrase_runs=runs([(cropped_text, 'unclassified', '原文与导读测试夹具')]),
                       reading_span={'char_start': 3, 'char_end': len(cropped_text)}, reading_note='测试移除导读前缀。',
                       reviewed_scope=dict(source_text_sha256=annotation.text_sha256(cropped_text),
                                           kept_text=cropped_text[3:], reason='已核对测试夹具前缀范围。', approved=True))
        self.a['units'].extend([plain, cropped])
        segment = self.m['segments'][0]
        segment['source_shot_ids'] = ['shot-0001', 'shot-0002', 'shot-0003']
        segment['source_excerpt']['items'] = [dict(shot_id=u['source_shot_ids'][0], text=u['source_text'])
                                               for u in self.a['units']]
        for group in ('source_formula', 'reusable_formula'):
            self.a[group][0]['source_unit_ids'] = ['plain-unit']
        html = local_html.render(self.a)
        payload = json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S)[1])
        xml = docx.render(self.a, self.m, self.c, legacy.MATERIALS, legacy.SEGMENTS)
        tree = ET.fromstring('<chapter>' + xml + '</chapter>')
        segment_index = next(i for i, n in enumerate(tree) if n.tag == 'h2' and n.text == '段01｜原段名')
        docx_source = ''.join(tree[segment_index + 2].itertext())
        expected_lines = [plain['source_text'], cropped_text[3:]]
        self.assertEqual(docx_source, '　'.join(expected_lines))
        script = local_html.SCRIPT
        helper = script[script.index('function visibleSourceText'):script.index('function pickFamily')]
        callback = next(line for line in script.splitlines() if "getElementById('copy-script').onclick=" in line)
        program = 'const D=' + json.dumps(payload, ensure_ascii=False) + ';\n'
        program += 'const button={};const document={getElementById(){return button}};let copied;function copy(text){copied=text}\n'
        program += helper + '\n' + callback + '\nbutton.onclick();console.log(JSON.stringify(copied));'
        result = subprocess.run(['node'], input=program, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), '\n'.join(expected_lines))
        self.assertNotIn('导读｜', json.loads(result.stdout))
        self.assertEqual(payload['units'][0]['display_runs'], [])

    @unittest.skipUnless(shutil.which('node'), 'Node is needed to execute the empty copy callback')
    def test_copy_all_observation_notes_is_empty(self):
        unit = self.a['units'][0]
        unit.update(display_kind='observation_note', reading_note='全部都是观察注释。')
        html = local_html.render(self.a)
        payload = json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S)[1])
        script = local_html.SCRIPT
        helper = script[script.index('function visibleSourceText'):script.index('function pickFamily')]
        program = 'const units=' + json.dumps(payload['units'], ensure_ascii=False) + ';\n'
        program += helper + '\nconsole.log(JSON.stringify(visibleSourceText(units)));'
        result = subprocess.run(['node'], input=program, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), '')

    @unittest.skipUnless(shutil.which('node'), 'Node is needed to exercise actual HTML style functions')
    def test_actual_html_javascript_formula_runs_and_style_values(self):
        script = local_html.SCRIPT
        functions = script[script.index('function applyFamilyStyle'):script.index('function pickFamily')]
        functions += script[script.index('function formula('):script.index("document.getElementById('title')")]
        program = """
const tax=Object.fromEntries(TAX.map(x=>[x.family_id,x]));
const sections={};
function el(tag,cls,text){return {tag,className:cls,textContent:text,style:{},dataset:{},children:[],append(...items){this.children.push(...items)}}}
const target=el('div');const document={getElementById(){return target}};
""".replace('TAX', json.dumps(annotation.canonical_taxonomy(), ensure_ascii=False))
        program += functions
        program += '\nformula("test",STEPS);console.log(JSON.stringify(target));'.replace(
            'STEPS', json.dumps(self.a['source_formula'], ensure_ascii=False))
        result = subprocess.run(['node'], input=program, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = json.loads(result.stdout)['children'][0]['children']
        self.assertEqual(''.join(x['textContent'] for x in rendered), self.a['source_formula'][0]['label'])
        self.assertEqual(rendered[0]['style']['backgroundColor'], 'rgba(255,246,122,0.8)')
        self.assertEqual(rendered[0]['style']['color'], '#1F2329')
        self.assertEqual(rendered[-1]['style']['textDecoration'], 'underline')



if __name__ == '__main__':
    unittest.main(verbosity=2)
