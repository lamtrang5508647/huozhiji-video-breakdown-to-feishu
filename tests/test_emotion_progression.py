"""Synthetic evidence regressions; no source interpretation or remote services."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

SCRIPTS = Path(__file__).resolve().parents[1] / 'skill/analyze-folder-videos-to-feishu/scripts'
sys.path.insert(0, str(SCRIPTS))
import emotion_progression as emotion
import render_platform_chapter as renderer
import validate_script_v3 as script_gate
import validate_section_content as section_gate
from build_video_base_plan import emotion_text
from test_render_platform_chapter import fixture, MATERIALS, SEGMENTS


def explicit_fixture(status='observed_changes', count=None):
    analysis, manifest, content = fixture()
    segment = manifest['segments'][0]
    segment['source_shot_spans'] = [
        dict(shot_id=f'shot-000{i+1}', source_shot_start_ms=i * 2000,
             source_shot_end_ms=(i+1) * 2000, segment_intersection_start_ms=i * 2000,
             segment_intersection_end_ms=(i+1) * 2000) for i in range(2)]
    count = {'observed_changes': 2, 'no_clear_change': 1, 'insufficient_evidence': 0}[status] if count is None else count
    events = [dict(observable=f'源片可见动作{i}', evidence_time_ms=i * 1000,
                   evidence_shot_ids=[f'shot-000{i // 2 + 1}']) for i in range(count)]
    segment['emotion'] = dict(progression_status=status, observable_timeline=events,
                             review_note='缺少可辨认表情与原声，需补看连续原片。' if status == 'insufficient_evidence' else '')
    section = content['sections'][0]
    section.update(emotion_progression_status=status, emotion_review_note=segment['emotion']['review_note'],
                   source_shot_ids=segment['source_shot_ids'],
                   arc=[dict(time_ms=e['evidence_time_ms'], expression=e['observable'],
                             evidence_shot_id=e['evidence_shot_ids'][0]) for e in events])
    return analysis, manifest, content


class EmotionContractTests(unittest.TestCase):
    def test_cardinality_accepts_actual_events_without_stages(self):
        for status, count in [('observed_changes', 2), ('observed_changes', 3), ('no_clear_change', 1),
                              ('no_clear_change', 3), ('insufficient_evidence', 0), ('insufficient_evidence', 1)]:
            with self.subTest(status=status, count=count):
                _, m, c = explicit_fixture(status, count)
                value = emotion.match_authoring(m['segments'][0], c['sections'][0])
                self.assertEqual(len(value['events']), count)

    def test_too_few_events_or_missing_review_note_are_rejected(self):
        for status, count in [('observed_changes', 0), ('observed_changes', 1), ('no_clear_change', 0)]:
            with self.subTest(status=status, count=count), self.assertRaisesRegex(ValueError, 'at least'):
                emotion.manifest_emotion(explicit_fixture(status, count)[1]['segments'][0])
        for note in ('', '   ', None):
            segment = explicit_fixture('insufficient_evidence')[1]['segments'][0]
            segment['emotion']['review_note'] = note
            with self.subTest(note=note), self.assertRaisesRegex(ValueError, 'review_note'):
                emotion.manifest_emotion(segment)

    def test_unknown_and_null_status_cannot_fall_back_to_legacy(self):
        for status in ('approved', 'legacy_four_stage', '', None, []):
            segment = explicit_fixture()[1]['segments'][0]
            segment['emotion']['progression_status'] = status
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, 'Unknown'):
                emotion.manifest_emotion(segment)

    def test_legacy_four_stages_remain_valid_and_cannot_be_truncated(self):
        _, m, c = fixture()
        self.assertEqual(emotion.match_authoring(m['segments'][0], c['sections'][0])['status'], 'legacy_four_stage')
        m['segments'][0]['emotion']['observable_timeline'].pop()
        with self.assertRaisesRegex(ValueError, 'exactly four'):
            emotion.manifest_emotion(m['segments'][0])

    def test_events_must_have_integer_in_segment_times_and_real_observations(self):
        for field, value in [('evidence_time_ms', -1), ('evidence_time_ms', 4000),
                             ('evidence_time_ms', 1.5), ('evidence_time_ms', True),
                             ('observable', ''), ('evidence_shot_ids', []),
                             ('evidence_shot_ids', ['shot-9999'])]:
            segment = explicit_fixture()[1]['segments'][0]
            segment['emotion']['observable_timeline'][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                emotion.manifest_emotion(segment)

    def test_same_segment_wrong_shot_time_is_rejected(self):
        segment = explicit_fixture()[1]['segments'][0]
        segment['emotion']['observable_timeline'][0]['evidence_shot_ids'] = ['shot-0002']
        with self.assertRaisesRegex(ValueError, 'shot/time mismatch'):
            emotion.manifest_emotion(segment)

    def test_duplicate_or_reversed_change_timestamps_are_rejected(self):
        for time in (0, -1):
            segment = explicit_fixture()[1]['segments'][0]
            segment['emotion']['observable_timeline'][1]['evidence_time_ms'] = time
            with self.subTest(time=time), self.assertRaises(ValueError):
                emotion.manifest_emotion(segment)

    def test_explicit_events_need_actual_shot_spans(self):
        segment = explicit_fixture()[1]['segments'][0]
        del segment['source_shot_spans']
        with self.assertRaisesRegex(ValueError, 'source shot spans'):
            emotion.manifest_emotion(segment)

    def test_authoring_disagreement_cannot_render(self):
        for change in ('status', 'note', 'time', 'expression', 'reference', 'stage'):
            a, m, c = explicit_fixture()
            section = c['sections'][0]
            if change == 'status':
                section['emotion_progression_status'] = 'no_clear_change'
            elif change == 'note':
                section['emotion_review_note'] = 'Different review'
            elif change == 'time':
                section['arc'][1]['time_ms'] += 1
            elif change == 'expression':
                section['arc'][0]['expression'] = 'Not the reviewed observation'
            elif change == 'reference':
                section['arc'][0]['evidence_shot_id'] = 'shot-0002'
            else:
                section['arc'][0]['stage'] = 'Unbound stage label'
            with self.subTest(change=change), self.assertRaises(ValueError):
                renderer.render(a, m, c, MATERIALS, SEGMENTS)

    def test_new_status_cannot_use_legacy_authoring_by_accident(self):
        a, m, c = explicit_fixture()
        del c['sections'][0]['emotion_progression_status']
        with self.assertRaisesRegex(ValueError, 'status differs from authoring'):
            renderer.render(a, m, c, MATERIALS, SEGMENTS)

    def test_status_and_actual_event_count_are_visible_without_extra_headings(self):
        for status in emotion.STATUSES:
            a, m, c = explicit_fixture(status)
            xml = renderer.render(a, m, c, MATERIALS, SEGMENTS)
            self.assertIn(emotion.STATUSES[status], xml)
            self.assertIn(f"{len(m['segments'][0]['emotion']['observable_timeline'])}条观察事件", xml)
            self.assertEqual(renderer.validate_layout(xml, a, m, c, MATERIALS, SEGMENTS)['h2_count'], 4)
            for omitted in ('不可直接照搬：', '整片受众假设（非测量）：', '复刻素材清单：'):
                self.assertNotIn(omitted, xml)
            self.assertIn('<span text-color="red">', xml)
            self.assertNotIn('触发 ', xml)
            if status == 'insufficient_evidence':
                self.assertIn('待复核', xml)
                self.assertIn('未明确，暂无可核对事件', xml)

    def test_base_plan_emotion_text_preserves_new_state_and_pending_note(self):
        for status in emotion.STATUSES:
            value = explicit_fixture(status)[1]['segments'][0]['emotion']
            text = emotion_text(value)
            self.assertIn(emotion.STATUSES[status], text)
            if value['review_note']:
                self.assertIn(value['review_note'], text)


class AuthoringGateTests(unittest.TestCase):
    def packet(self, status):
        _, manifest, content = explicit_fixture(status)
        content.update(timeline_sha256='fixture-sha', strategy='先提问再演示')
        detail = content['sections'][0]
        detail.update(role='回应具体疑问', question='怎样操作？')
        timeline = {'duration_sec': 4, 'segments': [dict(id=f'shot-000{i+1}', start_ms=i*2000, end_ms=(i+1)*2000) for i in range(2)]}
        return content, timeline

    def test_new_statuses_pass_schema_without_becoming_reviewed(self):
        for status in emotion.STATUSES:
            content, timeline = self.packet(status)
            result = section_gate.check(content, timeline, 'fixture-sha')
            with self.subTest(status=status):
                self.assertEqual(result['status'], 'PASS', result['errors'])
                self.assertFalse(result['publication_ready'])
                self.assertTrue(result['content_review_required'])
                self.assertEqual(result['emotion_evidence_incomplete'], [1] if status == 'insufficient_evidence' else [])

    def test_new_authoring_event_failures_are_not_skipped(self):
        for field, value in [('time_ms', 4000), ('time_ms', 2000), ('evidence_shot_id', 'another-segment'), ('expression', '')]:
            content, timeline = self.packet('no_clear_change')
            content['sections'][0]['arc'][0][field] = value
            with self.subTest(field=field, value=value):
                self.assertEqual(section_gate.check(content, timeline, 'fixture-sha')['status'], 'FAIL')

    def test_authoring_rejects_unknown_status_and_unexplained_evidence_gap(self):
        for status, note in [('assessed', ''), (None, ''), ('insufficient_evidence', '')]:
            content, timeline = self.packet('insufficient_evidence')
            content['sections'][0].update(emotion_progression_status=status, emotion_review_note=note)
            with self.subTest(status=status):
                self.assertEqual(section_gate.check(content, timeline, 'fixture-sha')['status'], 'FAIL')


class ManifestGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'source.mp4').write_bytes(b'synthetic source contract fixture, not decodable media')
        self.source_sha = hashlib.sha256((self.root / 'source.mp4').read_bytes()).hexdigest()
        self.timeline = dict(video_id='source-A', source={'sha256': self.source_sha}, duration_sec=4,
                             segments=[dict(id=f'shot-000{i+1}', start_ms=i*2000, end_ms=(i+1)*2000) for i in range(2)],
                             semantic_groups=[dict(id='D01')])
        self.path = self.root / 'manifest.json'
        self.timeline_path = self.root / 'timeline.json'
        self.timeline_path.write_text(json.dumps(self.timeline), encoding='utf-8')

    def data(self, status):
        _, manifest, _ = explicit_fixture(status)
        manifest.update(schema_version='script-v3.1', coverage='full', audio_review_status='not_reviewed',
                        script_function_options=['开场引入'], emotion_tag_options=['未明确'])
        manifest['source'].update(path='source.mp4', sha256=self.source_sha, timeline_manifest_path='timeline.json',
                                  timeline_manifest_sha256=hashlib.sha256(self.timeline_path.read_bytes()).hexdigest())
        manifest['segments'][0]['source_excerpt']['type'] = 'subtitle_excerpts_only'
        return manifest

    def validate(self, manifest):
        with patch.object(script_gate, 'ffprobe_duration', return_value=(4.0, {'streams': [{'codec_type': 'video', 'avg_frame_rate': '30/1'}]})):
            return script_gate.validate(manifest, self.path, None, 'ffprobe')

    def test_manifest_accepts_all_new_statuses_as_drafts_only(self):
        for status in emotion.STATUSES:
            result = self.validate(self.data(status))
            with self.subTest(status=status):
                self.assertEqual(result['status'], 'valid_draft')
                self.assertEqual(result['emotion_evidence_incomplete'], ['test-01'] if status == 'insufficient_evidence' else [])

    def test_manifest_rejects_event_bound_to_wrong_shot_even_inside_segment(self):
        manifest = self.data('no_clear_change')
        manifest['segments'][0]['emotion']['observable_timeline'][0]['evidence_shot_ids'] = ['shot-0002']
        with self.assertRaisesRegex(script_gate.InvalidManifest, 'shot/time mismatch'):
            self.validate(manifest)

    def test_legacy_manifest_with_four_source_bound_stages_passes(self):
        manifest = self.data('observed_changes')
        segment = manifest['segments'][0]
        segment['emotion'] = {'observable_timeline': [dict(stage=stage, observable=f'原片表达{i}',
            evidence_time_ms=i*1000, evidence_shot_ids=[f'shot-000{i//2+1}']) for i, stage in enumerate(emotion.LEGACY_STAGES)]}
        self.assertEqual(self.validate(manifest)['status'], 'valid_draft')


class DeepAnalysisLayoutTests(unittest.TestCase):
    def data(self):
        a, m, c = explicit_fixture('no_clear_change')
        c.update(analysis_mode='qianchuan_deep', strategy='以实际演示回应操作疑问')
        segment, detail = m['segments'][0], c['sections'][0]
        segment.update(core_question='如何操作？', role='回应操作疑问，不代表产品效果已获验证',
                       source_structure='口播讲步骤，画面同步演示；效果主张未提供证据')
        detail.update(question=segment['core_question'], role=segment['role'], structure=segment['source_structure'])
        return a, m, c

    def test_deep_mode_shows_specific_fields_and_keeps_fixed_headings_and_omissions(self):
        a, m, c = self.data()
        xml = renderer.render(a, m, c, MATERIALS, SEGMENTS)
        for text in ('原片表达策略：', c['strategy'], '本段回应的问题：', '本段表达作用（非效果验证）：',
                     '论证与声画关系：', '复用边界：', c['sections'][0]['conditions']):
            self.assertIn(text, xml)
        tree = ET.fromstring('<chapter>' + xml + '</chapter>')
        self.assertEqual([n.tag for n in tree if n.tag.startswith('h')], ['h1', 'h2', 'h2', 'h2', 'h2'])
        for omitted in ('不可直接照搬：', '整片受众假设（非测量）：', '复刻素材清单：'):
            self.assertNotIn(omitted, xml)
        self.assertEqual(renderer.validate_layout(xml, a, m, c, MATERIALS, SEGMENTS)['status'], 'PASS')

    def test_default_mode_does_not_change_existing_presentation(self):
        a, m, c = fixture()
        before = renderer.render(a, m, c, MATERIALS, SEGMENTS)
        c['strategy'] = 'Optional text not enabled for legacy presentation'
        c['sections'][0].update(question='Optional', role='Optional')
        self.assertEqual(renderer.render(a, m, c, MATERIALS, SEGMENTS), before)

    def test_deep_mode_rejects_missing_unreadable_or_wrong_segment_fields(self):
        for key in ('question', 'role', 'structure', 'conditions'):
            for value in (None, '', ['not readable text']):
                a, m, c = self.data()
                c['sections'][0][key] = value
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    renderer.render(a, m, c, MATERIALS, SEGMENTS)
        for key in ('question', 'role', 'structure'):
            a, m, c = self.data()
            c['sections'][0][key] = 'Text copied from another segment'
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'differs from manifest'):
                renderer.render(a, m, c, MATERIALS, SEGMENTS)
        a, m, c = self.data()
        del c['strategy']
        with self.assertRaisesRegex(ValueError, 'strategy'):
            renderer.render(a, m, c, MATERIALS, SEGMENTS)


if __name__ == '__main__':
    unittest.main()
