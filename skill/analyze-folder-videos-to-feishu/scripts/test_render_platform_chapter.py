"""Offline layout/provenance regression tests; no live services or credentials."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

SCRIPT = Path(__file__).with_name('render_platform_chapter.py')
spec = importlib.util.spec_from_file_location('chapter_renderer', SCRIPT)
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
MATERIALS = 'https://example.invalid/base/test?table=materials&view=video'
SEGMENTS = 'https://example.invalid/base/test?table=segments&view=script'


def fixture():
    text = '我说<&>“原文”'
    runs = [dict(text=text[:2], char_start=0, char_end=2, category_family='hook_pain'),
            dict(text=text[2:], char_start=2, char_end=len(text), category_family='experience_claim')]
    arc = [dict(stage=s, observable=f'原始证据{s}', evidence_time_ms=i * 1000,
                evidence_shot_ids=['shot-0001']) for i, s in enumerate(r.STAGES)]
    seg = dict(id='test-01', ordinal=1, name='原段名', start_ms=0, end_ms=4000,
               source_group_ids=['D01'], source_shot_ids=['shot-0001', 'shot-0002'],
               script_function='开场引入', emotion=dict(observable_timeline=arc),
               source_excerpt=dict(items=[dict(shot_id='shot-0001', text=text)]))
    manifest = dict(video_id='source-A', title='原题<&>', duration_sec=4,
                    source=dict(url='https://example.invalid/video/A'), segments=[seg])
    analysis = dict(video_id='source-A', title='原题<&>', disclosure='仅字幕摘录，未听音；源作者主张未核实。',
                    taxonomy=[dict(family_id=f, label=f) for f in r.FAMILIES],
                    sections=[dict(id='test-01', name='原段名')],
                    source_formula=[dict(label='原始顺序', category_family='hook_pain', section_id='test-01')],
                    reusable_formula=[dict(label='[变量]', category_family='unclassified', section_id='test-01')],
                    units=[dict(id='unit-01', section_id='test-01', source_text=text,
                                source_shot_ids=['shot-0001'], phrase_runs=runs)])
    detail = dict(name='原段名', start_ms=0, end_ms=4000, function='开场引入', tags=['未明确'],
                  structure='原始顺序', formula='[变量]', slots='[填空]', conditions='有证据才使用',
                  shoot='近景拍摄', audience='假设而非测量', materials=['原始素材'], family='hook_pain')
    content = dict(visible_brand='源品牌', sections=[detail], replication_outline='复刻方向',
                   audience_hypothesis='整片假设', claim_boundaries=['原主张未核实'])
    return analysis, manifest, content


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.a, self.m, self.c = fixture()
        self.args = (self.a, self.m, self.c, MATERIALS, SEGMENTS)
        self.xml = r.render(*self.args)

    def validate(self, xml):
        return r.validate_layout(xml, *self.args)

    def mutated_tree(self, mutate):
        tree = ET.fromstring('<chapter>' + self.xml + '</chapter>')
        mutate(tree)
        return ''.join(ET.tostring(n, encoding='unicode') for n in tree)

    def test_reference_layout_and_literal_source(self):
        tree = ET.fromstring('<chapter>' + self.xml + '</chapter>')
        self.assertEqual([''.join(n.itertext()) for n in tree.findall('h2')],
                         ['原片结构公式', '可复用变量公式', '句内功能颜色图例', '段01｜原段名'])
        start = next(i for i, n in enumerate(tree) if n.tag == 'h2' and n.text == '段01｜原段名')
        self.assertEqual(''.join(tree[start + 2].itertext()), self.a['units'][0]['source_text'])
        self.assertNotIn('shot-', self.xml)
        self.assertIn('画面001–画面002（2个相交镜头）', self.xml)
        self.assertNotIn('完整映射', self.xml)
        for omitted in ('复刻素材清单：', '整片受众假设（非测量）：', '不可直接照搬：'):
            self.assertNotIn(omitted, self.xml)
        self.assertIn('受众反应假设（非测量）', self.xml)
        self.assertIn('假设而非测量', self.xml)
        self.assertIn('<b>复刻方向：</b>复刻方向', self.xml)
        self.assertIn('<b>复刻镜头建议：</b>近景拍摄', self.xml)
        self.assertEqual(self.validate(self.xml)['status'], 'PASS')

    def test_color_normalization_and_server_ids(self):
        def normalize(tree):
            for i, node in enumerate(tree.iter()):
                node.set('id', 'server-' + str(i))
                for attr in ('text-color', 'background-color'):
                    if attr in node.attrib:
                        node.set(attr, r.NATIVE_COLORS.get(node.get(attr), node.get(attr)))
        self.assertEqual(self.validate(self.mutated_tree(normalize))['status'], 'PASS')

    def test_reject_heading_reorder(self):
        with self.assertRaisesRegex(ValueError, 'H2'):
            self.validate(self.xml.replace('<h2>原片结构公式</h2>', '<h2>可复用变量公式</h2>', 1))

    def test_reject_original_title_change(self):
        with self.assertRaisesRegex(ValueError, 'H1'):
            self.validate(self.xml.replace('<h1>原题&lt;&amp;&gt;</h1>', '<h1>改过的标题</h1>'))

    def test_reject_missing_field(self):
        def remove_range(tree):
            node = next(n for n in tree if ''.join(n.itertext()).startswith('源画面范围：'))
            tree.remove(node)
        with self.assertRaisesRegex(ValueError, 'block count'):
            self.validate(self.mutated_tree(remove_range))

    def test_reject_source_character_change(self):
        with self.assertRaisesRegex(ValueError, 'Block'):
            self.validate(self.xml.replace('“原文”', '“改写”', 1))

    def test_reject_color_change_even_when_text_same(self):
        with self.assertRaisesRegex(ValueError, 'Block'):
            self.validate(self.xml.replace('text-color="red"', 'text-color="blue"', 1))

    def test_reject_missing_highlight_bold_or_underline(self):
        for original, replacement in (
            ('background-color="light-yellow"', 'background-color="white"'),
            ('<b><span background-color="light-yellow">', '<b><span>'),
            ('<b><u>action_cta</u></b>', '<b>action_cta</b>')):
            with self.subTest(original=original), self.assertRaises(ValueError):
                self.validate(self.xml.replace(original, replacement, 1))

    def test_reject_missing_or_wrong_navigation(self):
        for destination in ('', 'https://example.invalid/other'):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                self.validate(self.xml.replace(r.esc(MATERIALS), destination, 1))

    def test_reject_missing_emotion_stage(self):
        with self.assertRaises(ValueError):
            self.validate(self.xml.replace('转折 00:02.000：原始证据转折', '证据遗漏', 1))

    def test_reject_source_input_drift(self):
        self.a['units'][0]['source_text'] += '伪造'
        with self.assertRaisesRegex(ValueError, 'Source text mismatch'):
            r.render(*self.args)

    def test_reject_phrase_gaps_and_family_drift(self):
        original = deepcopy(self.a['units'][0]['phrase_runs'])
        for key, value in (('char_start', 1), ('category_family', 'invented'), ('text', 'changed')):
            self.a['units'][0]['phrase_runs'] = deepcopy(original)
            self.a['units'][0]['phrase_runs'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                r.render(*self.args)

    def test_reject_cross_source_identity_or_interval(self):
        self.a['video_id'] = 'source-B'
        with self.assertRaisesRegex(ValueError, 'identity'):
            r.render(*self.args)
        self.a['video_id'] = self.m['video_id']
        self.c['sections'][0]['start_ms'] = 1
        with self.assertRaisesRegex(ValueError, 'interval'):
            r.render(*self.args)

    def test_reject_unproven_emotion_timestamp(self):
        self.m['segments'][0]['emotion']['observable_timeline'][0]['evidence_time_ms'] = 4500
        with self.assertRaisesRegex(ValueError, 'outside segment'):
            r.render(*self.args)

    def test_noncontiguous_shots_are_explicit_not_hidden_by_range(self):
        self.assertEqual(r.shot_range(['shot-0001', 'shot-0003']),
                         '画面001–画面003（2个相交镜头）；不连续，实际清单：画面001、画面003')

    def test_invalid_url_fails_closed(self):
        for url in ('javascript:alert(1)', 'file:///private', 'https://name:pass@example.invalid'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                r.render(self.a, self.m, self.c, url, SEGMENTS)

    def test_cli_roundtrip_failure_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            argv = [sys.executable, '-X', 'utf8', str(SCRIPT)]
            for name, value in (('analysis', self.a), ('manifest', self.m), ('content', self.c)):
                path = directory / (name + '.json')
                path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
                argv.extend(['--' + name, str(path)])
            output = directory / 'chapter.xml'
            argv.extend(['--materials-url', MATERIALS, '--segments-url', SEGMENTS, '--output', str(output)])
            result = subprocess.run(argv, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['status'], 'PASS')
            before = output.read_bytes()
            check = subprocess.run(argv + ['--validate-only'], capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(check.returncode, 0, check.stderr)
            self.a['title'] = 'wrong source'
            (directory / 'analysis.json').write_text(json.dumps(self.a), encoding='utf-8')
            failed = subprocess.run(argv, capture_output=True, text=True, encoding='utf-8')
            self.assertNotEqual(failed.returncode, 0)
            self.assertEqual(output.read_bytes(), before)

    def test_renderer_has_no_network_dependency(self):
        with patch('socket.socket.connect', side_effect=AssertionError('Network forbidden')):
            self.assertEqual(self.validate(r.render(*self.args))['status'], 'PASS')

    def test_feishu_adjacent_equal_styles_can_merge(self):
        self.a['units'][0]['phrase_runs'] = [
            dict(text=self.a['units'][0]['source_text'][:2], char_start=0, char_end=2, category_family='experience_claim'),
            dict(text=self.a['units'][0]['source_text'][2:], char_start=2,
                 char_end=len(self.a['units'][0]['source_text']), category_family='experience_claim')]
        xml = r.render(*self.args)
        merged = xml.replace('</span></b><b><span background-color="light-yellow">', '')
        self.assertNotEqual(xml, merged)
        self.assertEqual(self.validate(merged)['status'], 'PASS')
        with self.assertRaises(ValueError):
            self.validate(merged.replace('background-color="light-yellow"', 'background-color="light-blue"', 1))

    def test_explicit_observation_note_is_separate_from_source(self):
        unit = self.a['units'][0]
        unit['display_kind'] = 'observation_note'
        unit['reading_note'] = '原数据是辨读状态说明，不是视频原文。'
        xml = r.render(*self.args)
        self.assertIn('字幕辨读备注（分析注释）', xml)
        self.assertNotIn(r.esc(unit['source_text']), xml)
        self.assertEqual(self.validate(xml)['status'], 'PASS')

    def test_reading_span_cannot_invent_source_text(self):
        unit = self.a['units'][0]
        unit['reading_span'] = {'char_start': 2, 'char_end': len(unit['source_text'])}
        unit['reading_note'] = '前两个字是旧格式标签，单独保留说明。'
        xml = r.render(*self.args)
        self.assertEqual(self.validate(xml)['status'], 'PASS')
        unit['reading_span']['char_end'] += 1
        with self.assertRaisesRegex(ValueError, 'exact source characters'):
            r.render(*self.args)


class SourceReadingTests(unittest.TestCase):
    def setUp(self):
        self.a = dict(annotation_profile=r.PROFILE, text_kind='subtitle_excerpts',
                      disclosure='字幕摘录；不是已校对的完整原声。',
                      sections=[dict(id='s1', name='分析段名不可混入'), dict(id='s2', name='另一分析段')],
                      units=[], source_formula=[dict(label='公式不应混入')])
        pieces = [
            ('s1', [('如果你也喜欢面包，', 'hook_pain'), ('不是说人人都有效。', 'unclassified')]),
            ('s1', [('不是说人人都有效。', 'unclassified'), ('先看面粉，再看条件。', 'evidence_trust')]),
            ('s2', [('别只看结果，', 'scene_audience'), ('这次我满意。', 'experience_claim'),
                    ('想了解再问。', 'action_cta'), ('<&>“原话”🙂\t\r\n', 'unclassified')])]
        for ordinal, (section_id, phrases) in enumerate(pieces, 1):
            cursor, runs = 0, []
            for text, family in phrases:
                runs.append(dict(text=text, char_start=cursor, char_end=cursor + len(text),
                                 category_family=family, function_tag='已审具体功能', reason='上下文人工判断。'))
                cursor += len(text)
            self.a['units'].append(dict(id=f'u{ordinal}', section_id=section_id,
                                        source_text=''.join(t for t, _ in phrases), phrase_runs=runs,
                                        source_shot_ids=[f'shot-{ordinal:04d}'], locate_ms=ordinal * 1000,
                                        locator_note='时间画面定位不应混入', reading_note='普通辨读备注不应混入正文'))

    def body(self, xml):
        tree = ET.fromstring('<fragment>' + xml + '</fragment>')
        start = next(i for i, n in enumerate(tree) if n.tag == 'h2' and n.text == '原稿｜标色阅读')
        blocks = list(tree)[start + 1:]
        self.assertTrue(all(n.tag == 'p' for n in blocks))
        return blocks

    def test_exact_section_paragraphs_repetition_negation_and_no_metadata(self):
        before = deepcopy(self.a)
        xml = r.render_source_reading(self.a)
        body = self.body(xml)
        self.assertEqual(len(body), 2)
        expected = ['　'.join(u['source_text'] for u in self.a['units'] if u['section_id'] == sid)
                    for sid in ('s1', 's2')]
        actual = [''.join(n.itertext()) for n in body]
        self.assertEqual(actual, expected)
        self.assertEqual(actual[0].count('不是说人人都有效。'), 2)
        self.assertEqual(actual[0].count('　'), 1)  # Only between nonempty units, never between phrases.
        joined = ''.join(actual)
        for marker in ('分析段名', '公式', 'shot-', '00:01', '时间画面定位', '辨读备注', 's1', 'u1'):
            self.assertNotIn(marker, joined)
        self.assertEqual(self.a, before)
        self.assertIn('现有字幕摘录连续排版；原音未逐句听校', xml)

    def test_every_character_keeps_reviewed_style_including_conditions(self):
        actual = [char for p in self.body(r.render_source_reading(self.a))
                  for char in r.effective_signature(p)[2]]
        expected = []
        previous_section = None
        for unit in self.a['units']:
            if previous_section == unit['section_id']:
                expected.append(('　', ()))  # The existing detailed layout uses an unstyled separator.
            for run in unit['phrase_runs']:
                family = run['category_family']
                if family == 'experience_claim':
                    style = {'b': True, 'background-color': 'rgba(255,246,122,0.8)'}
                elif family == 'action_cta':
                    style = {'b': True, 'u': True}
                else:
                    style = {'text-color': r.NATIVE_COLORS[r.FAMILIES[family][0]]}
                expected.extend((ch, tuple(sorted(style.items()))) for ch in run['text'])
            previous_section = unit['section_id']
        self.assertEqual(actual, expected)
        # “面粉” deliberately has the reviewed purple style, not a keyword-derived green.
        text = ''.join(ch for ch, _ in actual)
        for index in range(text.index('面粉'), text.index('面粉') + 2):
            self.assertEqual(dict(actual[index][1])['text-color'], 'rgb(100,37,208)')

    def test_reviewed_scope_and_observation_notes_stay_outside_body(self):
        first, second = self.a['units'][:2]
        first['reading_span'] = dict(char_start=2, char_end=len(first['source_text']))
        first['reading_note'] = '已审范围说明，不是口播。'
        first['reviewed_scope'] = dict(approved=True, source_text_sha256=r.hashlib.sha256(
            first['source_text'].encode('utf-8')).hexdigest(),
            kept_text=first['source_text'][2:], reason='已有明确范围审阅。')
        second['display_kind'] = 'observation_note'
        second['reading_note'] = '这条仅为不可辨读状态，不是口播。'
        xml = r.render_source_reading(self.a)
        body = self.body(xml)
        self.assertEqual(''.join(body[0].itertext()), first['source_text'][2:])
        self.assertIn('阅读范围/缺口说明：', xml)
        self.assertNotIn('范围说明', ''.join(''.join(n.itertext()) for n in body))
        self.assertEqual(''.join(body[1].itertext()), self.a['units'][2]['source_text'])
        first['reviewed_scope']['source_text_sha256'] = 'stale'
        with self.assertRaisesRegex(ValueError, 'hash is stale'):
            r.render_source_reading(self.a)

    def test_no_placeholder_body_when_all_units_are_notes(self):
        for unit in self.a['units']:
            unit['display_kind'] = 'observation_note'
        xml = r.render_source_reading(self.a)
        self.assertEqual(self.body(xml), [])
        self.assertIn('暂无可读原文', xml)

    def test_invalid_scope_or_source_order_fails_closed(self):
        cases = []
        a = deepcopy(self.a)
        a['units'][0]['phrase_runs'][0]['char_start'] = 1
        cases.append(a)
        a = deepcopy(self.a)
        a['units'][1]['id'] = a['units'][0]['id']
        cases.append(a)
        a = deepcopy(self.a)
        a['units'][0]['section_id'] = 'unknown'
        cases.append(a)
        a = deepcopy(self.a)
        a['units'] = [a['units'][2], a['units'][0], a['units'][1]]
        cases.append(a)
        for a in cases:
            with self.subTest(units=a['units']), self.assertRaises(ValueError):
                r.render_source_reading(a)

    def test_source_kind_and_existing_status_are_explicit_and_escaped(self):
        self.a.update(text_kind='user_provided_copy', audio_review_status='not_reviewed',
                      transcript_status='asr_draft<&>', source_text_status='疑点未决')
        xml = r.render_source_reading(self.a)
        self.assertIn('用户提供文案连续排版', xml)
        self.assertIn('原音校对状态：not_reviewed', xml)
        self.assertIn('asr_draft&lt;&amp;&gt;', xml)
        self.assertNotIn('已完成听校', xml)
        self.assertNotIn('asr_draft', ''.join(''.join(n.itertext()) for n in self.body(xml)))

    def test_authored_copy_is_rejected_but_user_and_unknown_sources_are_not_invented(self):
        for kind in ('authored_whole_reference_imitation', 'authored_new_copy'):
            self.a['text_kind'] = kind
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'Authored adaptations'):
                r.render_source_reading(self.a)
        for kind in ('user_provided_copy', 'unknown_source_kind'):
            self.a['text_kind'] = kind
            xml = r.render_source_reading(self.a)
            self.assertEqual(''.join(''.join(n.itertext()) for n in self.body(xml)),
                             ''.join('　'.join(u['source_text'] for u in self.a['units']
                                              if u['section_id'] == section['id'])
                                     for section in self.a['sections']))
            self.assertNotIn('已完成听校', xml)
        self.assertIn('文字来源类型：unknown_source_kind', xml)
        self.assertIn('原音逐句听校状态：未提供', xml)

    def test_default_inserts_fragment_without_changing_existing_chapter(self):
        a, m, c = fixture()
        old = r.render(a, m, c, MATERIALS, SEGMENTS, layout_profile=r.LEGACY_LAYOUT_PROFILE)
        new = r.render(a, m, c, MATERIALS, SEGMENTS, layout_profile=r.SOURCE_READING_PROFILE)
        fragment = r.render_source_reading(a).rstrip('\n') + '\n'
        self.assertEqual(new.replace(fragment, '', 1), old)
        tree = ET.fromstring('<chapter>' + new + '</chapter>')
        headings = [(i, n.text) for i, n in enumerate(tree) if n.tag == 'h2']
        reading_index = next(i for i, text in headings if text == '原稿｜标色阅读')
        formula_index = next(i for i, text in headings if text == '原片结构公式')
        self.assertIn('素材资产库', ''.join(tree[reading_index - 2].itertext()))
        self.assertEqual(formula_index, reading_index + 2)
        receipt = r.validate_layout(new, a, m, c, MATERIALS, SEGMENTS, layout_profile=r.SOURCE_READING_PROFILE)
        self.assertEqual(receipt['layout_profile'], r.SOURCE_READING_PROFILE)
        self.assertEqual(r.validate_layout(old, a, m, c, MATERIALS, SEGMENTS,
                                          layout_profile=r.LEGACY_LAYOUT_PROFILE)['status'], 'PASS')
        with self.assertRaisesRegex(ValueError, 'H2'):
            r.validate_layout(old, a, m, c, MATERIALS, SEGMENTS, layout_profile=r.SOURCE_READING_PROFILE)
        self.assertEqual(r.render(a, m, c, MATERIALS, SEGMENTS), old)
        a['layout_profile'] = r.LEGACY_LAYOUT_PROFILE
        self.assertEqual(r.render(a, m, c, MATERIALS, SEGMENTS), old)
        with self.assertRaisesRegex(ValueError, 'layout_profile'):
            r.render(a, m, c, MATERIALS, SEGMENTS, layout_profile='unknown')

    def test_cli_can_explicitly_render_and_validate_a_legacy_snapshot(self):
        a, m, c = fixture()
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            argv = [sys.executable, '-X', 'utf8', str(SCRIPT)]
            for name, value in (('analysis', a), ('manifest', m), ('content', c)):
                path = directory / (name + '.json')
                path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
                argv.extend(['--' + name, str(path)])
            output = directory / 'legacy.xml'
            argv.extend(['--materials-url', MATERIALS, '--segments-url', SEGMENTS,
                         '--output', str(output), '--layout-profile', r.LEGACY_LAYOUT_PROFILE])
            result = subprocess.run(argv, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['layout_profile'], r.LEGACY_LAYOUT_PROFILE)
            before = output.read_bytes()
            self.assertNotIn('原稿｜标色阅读', output.read_text(encoding='utf-8'))
            result = subprocess.run(argv + ['--validate-only'], capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.read_bytes(), before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
