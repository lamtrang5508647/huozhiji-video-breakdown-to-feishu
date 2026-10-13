"""Source-bound colored alignment view; synthetic input and executed DOM callbacks."""
import copy
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skill/analyze-folder-videos-to-feishu/scripts'
sys.path.insert(0, str(SCRIPTS))
import phrase_annotation as annotation
import render_script_analysis as renderer

spec = importlib.util.spec_from_file_location('analysis_alignment_fixture', ROOT / 'examples/synthetic/build.py')
synthetic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(synthetic)


def payload(document):
    return json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>', document, re.S)[1])


class AnalysisAlignmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'source.mp4').write_bytes(b'Synthetic contract fixture, not playable media')
        self.manifest = synthetic.fixture(self.root, annotation)
        self.analysis = json.loads((self.root / 'analysis.json').read_text(encoding='utf-8'))

    def cross_boundary(self):
        first, second = self.manifest['segments']
        first['source_shot_spans'][0]['source_shot_end_ms'] = 3000
        second['source_shot_spans'] = [
            {'shot_id': 'shot-0001', 'source_shot_start_ms': 0, 'source_shot_end_ms': 3000,
             'segment_intersection_start_ms': 2000, 'segment_intersection_end_ms': 3000},
            {'shot_id': 'shot-0002', 'source_shot_start_ms': 3000, 'source_shot_end_ms': 4000,
             'segment_intersection_start_ms': 3000, 'segment_intersection_end_ms': 4000}]
        second['source_shot_ids'] = ['shot-0001', 'shot-0002']
        item = second['source_excerpt']['items'][0]
        item.update(shot_id='shot-0001', intersection_end_ms=3000, context_only=True)
        self.analysis['units'][1]['source_shot_ids'] = ['shot-0001']

    def test_optional_manifest_keeps_default_output_and_inputs_unchanged(self):
        original = copy.deepcopy(self.analysis)
        ordinary = renderer.render(self.analysis)
        self.assertEqual(ordinary, renderer.render(self.analysis, manifest=None))
        self.assertNotIn('script_shot_alignment', payload(ordinary))
        aligned = payload(renderer.render(self.analysis, self.manifest))
        self.assertEqual(aligned['script_shot_alignment']['rows_count'], 2)
        self.assertEqual(self.analysis, original)

    def test_foreign_identity_title_segment_order_and_timing_are_rejected(self):
        for change in ('video', 'title', 'order', 'time', 'unit_order'):
            data = copy.deepcopy(self.analysis)
            if change == 'video': data['video_id'] = 'foreign'
            elif change == 'title': data['title'] += ' altered'
            elif change == 'order': data['sections'].reverse()
            elif change == 'time': data['sections'][1]['start_ms'] += 1
            else: data['units'].reverse()
            with self.subTest(change=change), self.assertRaises((ValueError, AssertionError)):
                renderer.render(data, self.manifest)

    def test_subtitle_shot_and_character_drift_are_rejected(self):
        for change in ('shot', 'text', 'missing'):
            source = copy.deepcopy(self.manifest)
            if change == 'shot':
                self.cross_boundary()
                source = copy.deepcopy(self.manifest)
                source['segments'][1]['source_excerpt']['items'][0].update(
                    shot_id='shot-0002', intersection_start_ms=3000, intersection_end_ms=4000)
            elif change == 'text': source['segments'][0]['source_excerpt']['items'][0]['text'] += '!'
            else: source['segments'][0]['source_excerpt']['items'] = []
            with self.subTest(change=change), self.assertRaises((ValueError, AssertionError)):
                renderer.render(self.analysis, source)

    def test_reviewed_reading_scope_and_colors_are_reused(self):
        first, second = self.analysis['units']
        first.update(display_kind='observation_note', reading_note='仅为观察注释；不能当口播。')
        second.update(reading_span={'char_start': 1, 'char_end': 4}, reading_note='保留经复核的 LUE 范围。',
                      reviewed_scope={'source_text_sha256': annotation.text_sha256('BLUE'),
                                      'kept_text': 'LUE', 'reason': '合成样例范围复核', 'approved': True})
        original_runs = copy.deepcopy(second['phrase_runs'])
        value = payload(renderer.render(self.analysis, self.manifest))
        self.assertEqual(value['units'][0]['display_runs'], [])
        self.assertEqual(value['units'][0]['reading_note'], first['reading_note'])
        self.assertEqual(''.join(r['text'] for r in value['units'][1]['display_runs']), 'LUE')
        self.assertEqual(value['units'][1]['phrase_runs'], original_runs)
        self.assertEqual(value['script_shot_alignment']['rows'][1]['unit_ids'], [second['id']])
        self.assertEqual(value['units'][1]['reviewed_scope'], second['reviewed_scope'])

    def test_cross_segment_and_empty_subtitle_rows_remain_visible(self):
        self.cross_boundary()
        rows = payload(renderer.render(self.analysis, self.manifest))['script_shot_alignment']['rows']
        self.assertEqual(len(rows), 3)
        self.assertEqual((rows[1]['source_start_ms'], rows[1]['segment_offset_start_ms'],
                          rows[1]['shot_offset_start_ms']), (2000, 0, 2000))
        self.assertTrue(rows[1]['context_only'])
        self.assertEqual(rows[2]['unit_ids'], [])
        self.assertEqual(rows[2]['segment_offset_start_ms'], 1000)
        self.assertEqual(rows[2]['sentence_audio_alignment'], 'not_verified')

    def test_cli_manifest_is_optional_and_does_not_infer_artifact_paths(self):
        destination = self.root / 'elsewhere/page.html'
        result = subprocess.run([sys.executable, str(SCRIPTS / 'render_script_analysis.py'),
            '--input', str(self.root / 'analysis.json'), '--manifest', str(self.root / 'script_manifest.json'),
            '--output', str(destination)], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        value = payload(destination.read_text(encoding='utf-8'))
        self.assertIn('script_shot_alignment', value)
        self.assertEqual(value.get('links', []), self.analysis.get('links', []))

    def test_optional_alignment_artifact_links_are_explicit(self):
        self.analysis['links'] = [
            {'label': '对齐 JSON', 'url': '../reviewed-export/script-shot-alignment.json'},
            {'label': '对齐 CSV', 'url': 'https://example.invalid/artifacts/alignment.csv'}]
        value = payload(renderer.render(self.analysis, self.manifest))
        self.assertEqual(value['links'], self.analysis['links'])

    def test_explicit_null_manifest_cannot_silently_disable_alignment(self):
        (self.root / 'script_manifest.json').write_text('null', encoding='utf-8')
        destination = self.root / 'existing.html'
        destination.write_text('Preserve existing output', encoding='utf-8')
        result = subprocess.run([sys.executable, str(SCRIPTS / 'render_script_analysis.py'),
            '--input', str(self.root / 'analysis.json'), '--manifest', str(self.root / 'script_manifest.json'),
            '--output', str(destination)], capture_output=True, text=True, encoding='utf-8')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('manifest must be an object', result.stderr)
        self.assertEqual(destination.read_text(encoding='utf-8'), 'Preserve existing output')

    @unittest.skipUnless(shutil.which('node'), 'Node is needed for actual DOM callback execution')
    def test_dom_preserves_styles_escapes_markup_and_seeks_relative_to_clip(self):
        self.cross_boundary()
        self.analysis['units'][0].update(display_kind='observation_note', reading_note='保留观察注释备注')
        self.analysis['units'][1].update(
            reading_span={'char_start': 1, 'char_end': 4}, reading_note='保留字幕裁切备注',
            reviewed_scope={'source_text_sha256': annotation.text_sha256('BLUE'),
                            'kept_text': 'LUE', 'reason': '合成样例范围复核', 'approved': True})
        attack = '</script><img src=x onerror=alert(1)>'
        segment = self.manifest['segments'][1]
        segment['reusable_formula']['example_sentence_slots'] = attack
        segment['shot_groups'][0]['source_observation'] = attack
        document = renderer.render(self.analysis, self.manifest)
        self.assertNotIn(attack, document)
        value = payload(document)
        script = re.findall(r'<script>(.*?)</script>', document, re.S)[0]
        program = r'''
const nodes=[];const elements={};
function node(tag){const n={tag,children:[],dataset:{},style:{setProperty(){}},_text:'',
 classList:{add(){},toggle(){return false}},append(...xs){this.children.push(...xs)},
 prepend(...xs){this.children.unshift(...xs)},setAttribute(){},load(){},
 addEventListener(name,fn){if(name==='loadedmetadata')fn()},scrollIntoView(){}};
 Object.defineProperty(n,'textContent',{get(){return this._text+this.children.map(x=>x.textContent||'').join('')},
 set(v){this._text=String(v);this.children=[]}});
 Object.defineProperty(n,'innerHTML',{set(){throw Error('unsafe HTML construction')}});
 nodes.push(n);return n}
const document={createElement:node,createTextNode(text){const n=node('#text');n.textContent=text;return n},
 getElementById(id){return elements[id]||(elements[id]=node('div'))},
 querySelector(){return document.getElementById('tools')},querySelectorAll(){return []}};
const window={};
document.getElementById('data').textContent=JSON.stringify(PAYLOAD);
'''.replace('PAYLOAD', json.dumps(value, ensure_ascii=False))
        program += script
        program += r'''
const tables=nodes.filter(x=>x.tag==='table');
const buttons=nodes.filter(x=>x.tag==='button'&&x.textContent==='定位画面');
buttons[buttons.length-1].onclick();
console.log(JSON.stringify({tables:tables.length,text:tables.map(x=>x.textContent).join('\n'),
 currentTime:elements.video.currentTime,section:elements.video.dataset.section,
 phrases:nodes.filter(x=>x.className==='phrase').map(x=>({text:x.textContent,color:x.style.color})),
 unsafeTags:nodes.filter(x=>['img','script'].includes(x.tag)).length}));
'''
        result = subprocess.run(['node'], input=program, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        observed = json.loads(result.stdout)
        self.assertEqual(observed['tables'], 2)
        self.assertEqual(observed['currentTime'], 1)
        self.assertEqual(observed['section'], 'S02')
        self.assertIn(attack, observed['text'])
        self.assertIn('边界上下文', observed['text'])
        self.assertIn('未提供字幕摘录', observed['text'])
        self.assertIn('保留观察注释备注', observed['text'])
        self.assertIn('保留字幕裁切备注', observed['text'])
        self.assertEqual(observed['unsafeTags'], 0)
        expected_color = next(t['color'] for t in self.analysis['taxonomy'] if t['family_id'] == 'unclassified')
        self.assertTrue(any(p == {'text': 'LUE', 'color': expected_color} for p in observed['phrases']))


if __name__ == '__main__':
    unittest.main()
