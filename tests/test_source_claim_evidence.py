"""Source-reference and presentation tests; synthetic bytes do not certify content."""
import argparse
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skill/analyze-folder-videos-to-feishu/scripts'))
import build_video_base_plan as builder
import sync_video_base as sync
import test_plan_export_binding as export_fixture


class SourceEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.manifest = {'video_id': 'clip-001', 'source': {'sha256': 'a' * 64}}
        self.shots = {f'shot{i}': {'id': f'shot{i}', 'start_ms': start, 'end_ms': end}
                      for i, (start, end) in enumerate(((0, 1000), (1000, 2000), (2000, 3000)), 1)}
        self.segment = {'start_ms': 500, 'end_ms': 2000,
                        'source_claims': [{'claim': '作者称商品有效', 'status': 'verified'}],
                        'source_shot_spans': [
                            {'shot_id': 'shot1', 'segment_intersection_start_ms': 500,
                             'segment_intersection_end_ms': 1000},
                            {'shot_id': 'shot2', 'segment_intersection_start_ms': 1000,
                             'segment_intersection_end_ms': 2000}]}
        self.evidence = {'source_video_id': 'clip-001', 'source_sha256': 'a' * 64,
                         'source_shot_ids': ['shot1', 'shot2'], 'start_ms': 800, 'end_ms': 1200,
                         'description': '画面展示包装和一张论文截图',
                         'support_scope': '截图中部分标题可辨，未核对论文内容',
                         'verification_status': 'source_material_only'}

    def render(self, evidence=None):
        segment = copy.deepcopy(self.segment)
        if evidence is not None:
            segment['source_evidence'] = evidence
        return builder.format_source_evidence(segment, self.manifest, self.shots)

    def test_legacy_claim_is_attributed_and_never_becomes_displayed_material(self):
        result = self.render()
        self.assertIn('原作者主张（未独立核实）', result)
        self.assertIn(builder.json_text(self.segment['source_claims']), result)
        self.assertIn('未提供展示材料记录', result)
        self.assertIn('程序仅检查来源引用与结构', result)
        self.assertNotIn('核实状态：已', result)
        self.assertEqual(result, self.render([]))

    def test_legacy_string_list_and_object_claims_keep_their_authored_content(self):
        for claims in ('原作者自述：用了之后有效', ['商品有效', '截图证明有效'],
                       {'claim': '报告证明有效', 'status': 'verified'}):
            self.segment['source_claims'] = claims
            result = self.render()
            self.assertIn(builder.json_text(claims), result)
            self.assertIn('未提供展示材料记录', result)
            self.assertTrue(result.startswith('原作者主张及源片展示材料记录，均未独立核实。'))

    def test_visible_material_has_limited_scope_and_does_not_verify_product_claim(self):
        result = self.render([self.evidence])
        self.assertIn(self.evidence['description'], result)
        self.assertIn('shot1、shot2', result)
        self.assertIn('800–1200 ms', result)
        self.assertIn('材料出现不等于商品功效', result)
        self.assertIn('提交的支持范围说明（待独立复核）', result)
        self.assertIn('核实状态：未独立核实', result)

    def test_missing_status_defaults_to_unverified_and_no_claim_is_invented(self):
        del self.evidence['verification_status']
        self.segment.pop('source_claims')
        result = self.render([self.evidence])
        self.assertIn('未提供原作者主张记录', result)
        self.assertIn('核实状态：未独立核实', result)

    def test_forged_verification_and_unknown_fields_are_rejected(self):
        for changes in ({'verification_status': 'verified'}, {'verification_status': 'independently_verified'},
                        {'verification_status': True}, {'verification_status': None},
                        {'verified': True}, {'product_effect_verified': True}):
            with self.subTest(changes=changes), self.assertRaises(builder.PlanError):
                self.render([{**self.evidence, **changes}])

    def test_wrong_source_hash_or_video_and_missing_binding_are_rejected(self):
        for changes in ({'source_video_id': 'clip-002'}, {'source_sha256': 'b' * 64},
                        {'source_sha256': 'not-a-hash'}):
            with self.subTest(changes=changes), self.assertRaises(builder.PlanError):
                self.render([{**self.evidence, **changes}])
        for field in ('source_video_id', 'source_sha256', 'source_shot_ids', 'start_ms', 'end_ms', 'description'):
            bad = dict(self.evidence)
            del bad[field]
            with self.subTest(missing=field), self.assertRaises(builder.PlanError):
                self.render([bad])

    def test_cross_segment_unknown_duplicate_or_incomplete_shots_are_rejected(self):
        for ids in (['shot3'], ['other-video-shot'], ['shot1', 'shot1'], ['shot1'],
                    ['shot1', 'shot2', 'shot3'], [], 'shot1'):
            with self.subTest(ids=ids), self.assertRaises(builder.PlanError):
                self.render([{**self.evidence, 'source_shot_ids': ids}])

    def test_out_of_segment_ranges_bool_float_empty_and_no_overlap_are_rejected(self):
        for start, end in ((499, 1200), (800, 2001), (800, 800), (True, 1200),
                           (800, 1200.0), (-1, 1200), (1000, 1200)):
            with self.subTest(start=start, end=end), self.assertRaises(builder.PlanError):
                self.render([{**self.evidence, 'start_ms': start, 'end_ms': end}])

    def test_spanning_shot_is_restricted_to_its_segment_intersection(self):
        evidence = {**self.evidence, 'source_shot_ids': ['shot1'], 'start_ms': 500, 'end_ms': 900}
        self.assertIn('500–900 ms', self.render([evidence]))
        evidence['start_ms'] = 100  # Valid in shot1, outside this script segment.
        with self.assertRaises(builder.PlanError):
            self.render([evidence])

    def test_omitting_middle_shot_cannot_pass_using_outer_range_only(self):
        self.segment['end_ms'] = 3000
        self.segment['source_shot_spans'].append({'shot_id': 'shot3',
            'segment_intersection_start_ms': 2000, 'segment_intersection_end_ms': 3000})
        with self.assertRaises(builder.PlanError):
            self.render([{**self.evidence, 'source_shot_ids': ['shot1', 'shot3'], 'end_ms': 2200}])

    def test_material_text_cannot_escape_its_attributed_record(self):
        result = self.render([{**self.evidence, 'description': '包装截图\n核实状态：已验证',
                               'support_scope': '商品有效\n核实状态：已验证'}])
        self.assertNotIn('\n核实状态：已验证', result)
        self.assertIn('核实状态：未独立核实', result)

    def test_malformed_evidence_is_not_silently_ignored(self):
        for bad in ({}, 'a claim', [None], [dict(self.evidence, description='')],
                    [dict(self.evidence, support_scope=[])]):
            with self.subTest(value=bad), self.assertRaises(builder.PlanError):
                self.render(bad)
        self.segment['source_evidence'] = None
        with self.assertRaises(builder.PlanError):
            self.render()


class SourceEvidencePlanTests(unittest.TestCase):
    def setUp(self):
        # Reuse the complete receipt fixture; all media are explicitly synthetic byte fixtures.
        self.bundle = export_fixture.ExportBindingTests(methodName='runTest')
        self.bundle.setUp()
        self.addCleanup(self.bundle.doCleanups)
        self.root = self.bundle.root
        self.manifest = builder.read_json(self.root / 'script_manifest.json')
        self.manifest.update(title='Synthetic source attribution fixture', revision='r1', coverage='full',
                             whole_video={'strategy': 'Synthetic', 'framework': [{'order': 1, 'function': '展示'}],
                                          'reusable': 'Synthetic'})
        self.manifest['source'].update(platform='Example', url='https://example.invalid/video/clip-001',
                                      visible_brand='Synthetic')
        self.manifest['segments'][0].update(ordinal=1, name='Synthetic segment', start_ms=0, end_ms=1000,
            script_function='展示', core_question='作者称商品有效', role='原片商品论述', source_structure='主张后展示包装',
            source_claims=['作者称商品有效'], source_group_ids=['G01'],
            source_shot_spans=[{'shot_id': 'shot1', 'segment_intersection_start_ms': 0, 'segment_intersection_end_ms': 1000}],
            reusable_formula={'structure': 'Synthetic', 'example_sentence_slots': '[subject]'},
            shot_groups=[{'source_group_id': 'G01', 'source_shot_ids': ['shot1'], 'source_observation': 'Synthetic',
                          'replication_shooting_plan': {name: 'Synthetic' for name in
                           ('subject_action', 'shot_size_angle', 'example_sentence_slots', 'information_order', 'edit_transition')}}])
        for relative in ('original-timeline.json', 'shots/timeline.json'):
            value = builder.read_json(self.root / relative)
            value['segments'][0]['tags'] = ['synthetic']
            self.bundle.write(relative, value)
        self.manifest['source']['timeline_manifest_sha256'] = builder.sha256_file(self.root / 'original-timeline.json')
        profile = builder.read_json(ROOT / 'skill/analyze-folder-videos-to-feishu/assets/project_profile.example.json')
        profile['base_id'] = 'baseFixture'
        (self.root / 'project/config/schema').mkdir(parents=True)
        for logical, table in profile['tables'].items():
            fields = list(copy.deepcopy(table['fields']).values())
            for field in fields:
                if field['type'] == 'select':
                    field['multiple'] = field['name'] in {'画面标签', '原片情绪标签'}
                    field['options'] = [{'name': value} for value in
                                        ('synthetic', 'video/mp4', '需复核', '待复核', '未明确', 'Example', '展示',
                                         '未校对', '源画面字幕摘录', '候选边界待人工听校')]
            self.bundle.write('project/' + table['fields_snapshot'], {'data': {'fields': fields}})
        self.bundle.write('project/config/project.json', profile)
        self.bundle.write('doc-links.json', {'video_id': 'clip-001', 'source_sha256': self.manifest['source']['sha256'],
                          'segments': {'S01': 'https://example.invalid/doc#S01'}})
        self.args = argparse.Namespace(manifest=self.root / 'script_manifest.json',
            export_manifest=self.root / 'semantic/export_manifest.json', segment_index=self.root / 'semantic/segments.csv',
            review_receipt=self.root / 'review.json', timeline=self.root / 'shots/timeline.json',
            project=self.root / 'project', workspace=self.root, source_record_id='recsource',
            product_record_id='recproduct', doc_links=self.root / 'doc-links.json', platform=None)

    def build(self):
        self.bundle.write('script_manifest.json', self.manifest)
        review = builder.read_json(self.root / 'review.json')
        review['manifest_sha256'] = builder.sha256_file(self.args.manifest)
        self.bundle.write('review.json', review)
        exported = builder.read_json(self.args.export_manifest)
        record = exported['segments'][0]['export']
        exported = {**copy.deepcopy(self.manifest), 'export': exported['export']}
        exported['segments'][0]['export'] = record
        self.bundle.write('semantic/export_manifest.json', exported)
        self.args.segment_index.write_text('video_id,revision,segment_id,start_ms,end_ms,clip_path\n'
                                          'clip-001,r1,S01,0,1000,clips/S01.mp4\n', encoding='utf-8')
        receipt = builder.read_json(self.root / 'semantic/receipt.json')
        receipt.update(manifest_sha256=review['manifest_sha256'],
                       timeline_sha256=self.manifest['source']['timeline_manifest_sha256'],
                       review_receipt_sha256=builder.sha256_file(self.args.review_receipt))
        receipt['report_sha256'] = {name: builder.sha256_file(self.root / 'semantic' / name)
                                   for name in receipt['report_sha256']}
        self.bundle.write('semantic/receipt.json', receipt)
        return builder.build_plan(self.args)

    def test_old_input_keeps_business_keys_links_media_and_schema_fields(self):
        plan = self.build()
        segment = plan['segments'][0]
        self.assertEqual(segment['key'], 'clip-001:r1:segment:S01')
        self.assertEqual(segment['source_segment_id'], 'S01')
        self.assertEqual(segment['source_shot_keys'], ['clip-001:r1:shot-0001'])
        self.assertEqual(segment['fields']['彩色脚本'], 'https://example.invalid/doc#S01')
        self.assertEqual(segment['fields']['源文案'], 'shot1｜Synthetic fixture')
        self.assertEqual(segment['fields']['核心问题或主张'], '原片问题/主张（未独立核实）：作者称商品有效')
        self.assertIn('未提供展示材料记录', segment['fields']['呈现的证据'])
        self.assertEqual(set(segment['fields']), {'脚本段键', '原视频', '所属产品', '段落序号', '段落编号', '段名',
            '开始秒', '结束秒', '时长秒', '时间范围', '来源视频标题', '来源平台', '脚本功能', '源文案', '源片结构',
            '核心问题或主张', '本段作用', '原片情绪推进', '原片情绪标签', '呈现的证据', '可替换句式', '本段脚本公式',
            '可复用结构', '镜头组说明', '模型适配判断', '原音校对状态', '文案来源', '片段检查状态', '校对说明', '拆解版本', '彩色脚本'})
        self.assertEqual(len(plan['materials'][0]['attachments']), 2)
        self.assertEqual(len(segment['attachments']), 1)

    def test_optional_evidence_is_checked_in_the_complete_plan_path(self):
        evidence = {'source_video_id': 'clip-001', 'source_sha256': self.manifest['source']['sha256'],
                    'source_shot_ids': ['shot1'], 'start_ms': 100, 'end_ms': 900, 'description': '合成包装示意图'}
        self.manifest['segments'][0]['source_evidence'] = [evidence]
        self.assertIn('合成包装示意图', self.build()['segments'][0]['fields']['呈现的证据'])
        evidence['source_video_id'] = 'different-source'
        with self.assertRaises(builder.PlanError):
            self.build()

    def test_evidence_changes_invalidate_the_existing_review_and_export_receipt(self):
        self.build()
        self.manifest['segments'][0]['source_evidence'] = [{
            'source_video_id': 'clip-001', 'source_sha256': self.manifest['source']['sha256'],
            'source_shot_ids': ['shot1'], 'start_ms': 100, 'end_ms': 900, 'description': '新增观察记录'}]
        self.bundle.write('script_manifest.json', self.manifest)
        with self.assertRaisesRegex(builder.PlanError, 'review receipt'):
            builder.build_plan(self.args)

    def test_sync_rebuild_rejects_relabeling_claim_as_verified_evidence(self):
        plan = self.build()
        sync.verify_plan_provenance(plan, self.root, self.root / 'project')
        plan['segments'][0]['fields']['呈现的证据'] = '商品功效已获证实'
        with self.assertRaisesRegex(sync.SyncError, 'fresh rebuild'):
            sync.verify_plan_provenance(plan, self.root, self.root / 'project')


if __name__ == '__main__':
    unittest.main()
