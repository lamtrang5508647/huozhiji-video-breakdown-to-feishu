import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'skill/analyze-folder-videos-to-feishu/scripts/check_shot_group_intersections.py'
spec = importlib.util.spec_from_file_location('shot_group_intersections', SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def example():
    return {'video_id': 'synthetic', 'source': {'sha256': 'a' * 64},
            'semantic_groups': [{'id': 'G01', 'start_ms': 0, 'end_ms': 1000},
                                {'id': 'G02', 'start_ms': 1000, 'end_ms': 2000}],
            'segments': [{'id': 'shot-001', 'start_ms': 0, 'end_ms': 1200, 'group_id': 'G01',
                          'source_group_spans': [
                              {'id': 'shot-001', 'script_section_id': 'G01', 'start_ms': 0, 'end_ms': 1000},
                              {'id': 'shot-001', 'script_section_id': 'G02', 'start_ms': 1000, 'end_ms': 1200}]},
                         {'id': 'shot-002', 'start_ms': 1200, 'end_ms': 2000, 'group_id': 'G02',
                          'source_group_spans': [
                              {'id': 'shot-002', 'script_section_id': 'G02', 'start_ms': 1200, 'end_ms': 2000}]}]}


class GroupIntersectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root / 'timeline.json'
        self.data = example()

    def tearDown(self):
        self.tmp.cleanup()

    def run_check(self, data=None):
        self.path.write_text(json.dumps(data if data is not None else self.data), encoding='utf-8')
        return mod.check(self.path)

    def test_cross_group_time_intersection_preserves_primary_group(self):
        result = self.run_check()
        self.assertEqual(result['status'], 'PASS')
        self.assertFalse(result['publication_ready'])
        self.assertFalse(result['source_file_verified'])
        first = result['results'][0]
        self.assertEqual(first['primary_group_id'], 'G01')
        self.assertEqual([x['script_section_id'] for x in first['expected']], ['G01', 'G02'])
        self.assertEqual(first['expected'][1]['start_ms'], 1000)
        self.assertEqual(first['expected'][1]['end_ms'], 1200)

    def test_wrong_full_primary_group_is_blocked(self):
        self.data['segments'][0]['source_group_spans'] = [
            {'id': 'shot-001', 'script_section_id': 'G01', 'start_ms': 0, 'end_ms': 1200}]
        result = self.run_check()
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertIn('shot-001', result['errors'][0])

    def test_missing_all_or_some_is_never_pass(self):
        for shot in self.data['segments']:
            del shot['source_group_spans']
        self.data.pop('semantic_groups')
        result = self.run_check()
        self.assertEqual(result['status'], 'NOT_PRESENT')
        self.assertFalse(result['publication_ready'])
        self.data = example()
        del self.data['segments'][1]['source_group_spans']
        result = self.run_check()
        self.assertEqual(result['status'], 'PARTIAL')
        self.assertEqual(result['shots_missing_spans'], ['shot-002'])
        self.assertEqual(mod.main(['--timeline', str(self.path)]), 2)

    def test_overlap_or_gap_in_semantic_groups_is_blocked(self):
        self.data['semantic_groups'][1]['start_ms'] = 900
        with self.assertRaisesRegex(ValueError, 'overlap'):
            self.run_check()
        self.data = example()
        self.data['semantic_groups'][1]['start_ms'] = 1100
        result = self.run_check()
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertTrue(any('do not cover' in e for e in result['errors']))

    def test_duplicate_unknown_or_wrong_shot_spans_block(self):
        original = example()
        for mutation in ('duplicate', 'unknown', 'wrong_shot', 'truncated'):
            data = copy.deepcopy(original)
            spans = data['segments'][0]['source_group_spans']
            if mutation == 'duplicate': spans.append(copy.deepcopy(spans[1]))
            if mutation == 'unknown': spans[1]['script_section_id'] = 'G99'
            if mutation == 'wrong_shot': spans[1]['id'] = 'shot-002'
            if mutation == 'truncated': spans[1]['end_ms'] = 1199
            with self.subTest(mutation=mutation):
                self.assertEqual(self.run_check(data)['status'], 'BLOCKED')

    def test_out_of_order_groups_and_spans_are_compared_by_time(self):
        self.data['semantic_groups'].reverse()
        self.data['segments'][0]['source_group_spans'].reverse()
        self.assertEqual(self.run_check()['status'], 'PASS')

    def test_no_overwrite_and_invalid_types(self):
        self.run_check()
        output = self.root / 'receipt.json'
        output.write_text('preserve me', encoding='utf-8')
        self.assertEqual(mod.main(['--timeline', str(self.path), '--output', str(output)]), 1)
        self.assertEqual(output.read_text(encoding='utf-8'), 'preserve me')
        self.data['segments'][0]['start_ms'] = False
        with self.assertRaisesRegex(ValueError, 'integer'):
            self.run_check()


if __name__ == '__main__':
    unittest.main()
