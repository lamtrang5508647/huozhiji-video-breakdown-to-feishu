"""Alignment presentation and export binding; synthetic fixtures, no remote writes."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skill/analyze-folder-videos-to-feishu/scripts'))
import export_script_v3 as exporter
import phrase_annotation
import render_platform_chapter as renderer
import validate_script_v3 as validator

spec = importlib.util.spec_from_file_location('alignment_synthetic', ROOT / 'examples/synthetic/build.py')
synthetic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(synthetic)


class AlignmentDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'source.mp4').write_bytes(b'Synthetic bytes for metadata tests only')
        self.manifest = synthetic.fixture(self.root, phrase_annotation)
        self.analysis = json.loads((self.root / 'analysis.json').read_text(encoding='utf-8'))
        self.content = json.loads((self.root / 'sections-content.json').read_text(encoding='utf-8'))
        self.args = (self.analysis, self.manifest, self.content, 'https://example.invalid/materials', 'https://example.invalid/segments')

    def test_docx_optin_preserves_headings_and_phrase_styles_in_alignment(self):
        xml = renderer.render(*self.args)
        tree = ET.fromstring('<chapter>' + xml + '</chapter>')
        self.assertEqual(len(tree.findall('h1')), 1)
        self.assertEqual(len(tree.findall('h2')), 5)
        subtitles = [n for n in tree if ''.join(n.itertext()).startswith('原字幕摘录：')]
        self.assertEqual([''.join(n.itertext()) for n in subtitles], ['原字幕摘录：RED', '原字幕摘录：BLUE'])
        self.assertEqual(subtitles[0].find('span').get('text-color'), 'gray')
        self.assertIn('源片 00:02.000–00:04.000；段内 00:00.000–00:02.000', xml)
        self.assertIn('镜头处理（组级建议）', xml)
        self.assertEqual(renderer.validate_layout(xml, *self.args)['status'], 'PASS')
        with self.assertRaises(ValueError):
            renderer.validate_layout(xml.replace('源片 00:02.000', '源片 00:03.000'), *self.args)

    def test_legacy_docx_unchanged_without_flag_and_nonboolean_rejected(self):
        self.content.pop('script_shot_alignment')
        legacy = renderer.render(*self.args)
        self.assertNotIn('脚本与镜头对齐', legacy)
        self.content['script_shot_alignment'] = False
        self.assertEqual(renderer.render(*self.args), legacy)
        self.content['script_shot_alignment'] = 'true'
        with self.assertRaisesRegex(ValueError, 'boolean'):
            renderer.render(*self.args)

    def test_docx_observation_note_is_not_relabelled_as_subtitle(self):
        self.analysis['units'][0]['display_kind'] = 'observation_note'
        self.analysis['units'][0]['reading_note'] = 'Synthetic label is an observation for this test'
        xml = renderer.render(*self.args)
        tree = ET.fromstring('<chapter>' + xml + '</chapter>')
        subtitles = [''.join(n.itertext()) for n in tree if ''.join(n.itertext()).startswith('原字幕摘录：')]
        self.assertNotIn('RED', subtitles[0])
        self.assertIn('待核对', subtitles[0])
        self.assertIn('Synthetic label is an observation for this test', xml)

    def test_export_html_buttons_use_clip_offsets_and_bind_both_reports(self):
        html = exporter.render_html(self.manifest)
        self.assertIn('id="player-S02"', html)
        self.assertIn('data-player="player-S02" data-start="0.000" data-end="2.000"', html)
        self.assertIn('script-shot-alignment.csv', html)
        self.assertIn('script-shot-alignment.json', html)
        self.assertIn('player.currentTime = latestStart', html)
        self.assertTrue({'script-shot-alignment.json', 'script-shot-alignment.csv'} <= set(exporter.REPORT_FILES))

    def test_export_preflight_rejects_foreign_excerpt_or_invented_timing(self):
        for change in ({'shot_id': 'shot-9999'}, {'intersection_start_ms': 1}):
            data = copy.deepcopy(self.manifest)
            data['segments'][0]['source_excerpt']['items'][0].update(change)
            with self.subTest(change=change), patch.object(validator, 'ffprobe_duration', return_value=(4.0, {
                    'streams': [{'codec_type': 'video', 'avg_frame_rate': '30/1'}]})), self.assertRaises(ValueError):
                validator.validate(data, self.root / 'script_manifest.json', None, 'ffprobe', None, False)

    def test_legacy_missing_revision_is_explicitly_preserved_in_preflight_and_report(self):
        from script_shot_alignment import build_alignment
        data = copy.deepcopy(self.manifest)
        data.pop('revision')
        with patch.object(validator, 'ffprobe_duration', return_value=(4.0, {
                'streams': [{'codec_type': 'video', 'avg_frame_rate': '30/1'}]})):
            self.assertEqual(validator.validate(data, self.root / 'script_manifest.json', None, 'ffprobe', None, False)['status'], 'valid_draft')
        self.assertEqual(build_alignment(data)['revision'], 'not_provided')

    def test_export_preflight_rejects_shot_guidance_from_wrong_reviewed_group(self):
        data = copy.deepcopy(self.manifest)
        segment = data['segments'][0]
        segment['source_group_ids'].append('G02')
        segment['shot_groups'][0]['source_group_id'] = 'G02'
        with patch.object(validator, 'ffprobe_duration', return_value=(4.0, {
                'streams': [{'codec_type': 'video', 'avg_frame_rate': '30/1'}]})), self.assertRaisesRegex(ValueError, 'shot_group members differ'):
            validator.validate(data, self.root / 'script_manifest.json', None, 'ffprobe', None, False)


if __name__ == '__main__':
    unittest.main()
