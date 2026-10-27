"""Behavioral tests for the story-review record boundary; no semantic classifier."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import story_logic_review as s


class StoryReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.draft_path = self.folder / 'draft.json'
        self.review_path = self.folder / 'review.json'
        self.draft = {'groups': [
            {'section_id': '01', 'first_shot': 1, 'last_shot': 2,
             'C': {'parts': [{'text': '我做烘焙测评。'}]},
             'D': {'parts': [{'text': '我做直播选品。'}]}},
            {'section_id': '02', 'first_shot': 3, 'last_shot': 5,
             'C': {'parts': [{'text': '我和做烘焙内容的团队一起实测。'}, {'text': '去年在自己的直播间招了十位粉丝。'}]},
             'D': {'parts': [{'text': '我和直播选品团队一起实测。'}, {'text': '去年在自己的直播间招了十位粉丝。'}]}}
        ]}
        self.write(self.draft_path, self.draft)
        self.review = self.make_review(self.draft)
        self.write(self.review_path, self.review)

    def write(self, path, data):
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    def make_review(self, draft):
        raw_hash = hashlib.sha256(self.draft_path.read_bytes()).hexdigest()
        versions = s.draft_versions(draft)
        results = {}
        for version, groups in versions.items():
            checks = {name: {'verdict': 'pass', 'reason': '审阅了本稿中角色行动的实际承接关系。',
                             'quotes': [{'text': groups[0], 'group_index': 1}]}
                      for name in s.REQUIRED_CHECKS}
            results[version] = {
                'version': version, 'draft_sha256': raw_hash,
                'spoken_text_sha256': s.spoken_sha256(''.join(groups)),
                'author_id': 'author-agent', 'reviewer_id': 'independent-reviewer',
                'review_mode': 'independent', 'checks': checks, 'unresolved_findings': []}
        return {'profile': s.PROFILE, 'draft_sha256': raw_hash, 'versions': results}

    def validate(self, draft=None, review=None, versions=None):
        if draft is not None:
            self.write(self.draft_path, draft)
        if review is not None:
            self.write(self.review_path, review)
        return s.validate_story_review(self.draft_path, self.review_path, versions)

    def test_reviewed_two_version_script_passes_with_limited_scope(self):
        result = self.validate()
        self.assertEqual('PASS', result['status'])
        self.assertEqual('not_assessed', result['semantic_truth'])
        self.assertEqual({'C', 'D'}, {item['version'] for item in result['versions']})
        self.assertEqual({'declared_independent'}, {item['independence'] for item in result['versions']})

    def test_human_record_of_missing_team_relation_blocks_even_if_unresolved_list_empty(self):
        review = copy.deepcopy(self.review)
        check = review['versions']['C']['checks']['relationships_and_channel']
        check.update(verdict='needs_revision', reason='角色与团队和直播间关系缺少引出，无法理解为何招募粉丝。')
        with self.assertRaisesRegex(ValueError, 'needs_revision'):
            self.validate(review=review)

    def test_outstanding_finding_blocks_even_when_all_checks_say_pass(self):
        review = copy.deepcopy(self.review)
        review['versions']['C']['unresolved_findings'] = ['团队所属关系尚未确认']
        with self.assertRaisesRegex(ValueError, 'unresolved findings'):
            self.validate(review=review)

    def test_any_draft_edit_after_review_invalidates_entire_binding(self):
        changed = copy.deepcopy(self.draft)
        changed['groups'][1]['C']['parts'][0]['text'] += '新的一句。'
        with self.assertRaisesRegex(ValueError, 'draft file hash'):
            self.validate(draft=changed)

    def test_nonspoken_shot_plan_edit_also_invalidates_entire_draft_hash(self):
        changed = copy.deepcopy(self.draft)
        changed['groups'][0]['C']['shot_plan'] = '新场景重拍'
        with self.assertRaisesRegex(ValueError, 'draft file hash'):
            self.validate(draft=changed)

    def test_missing_literal_quote_blocks(self):
        review = copy.deepcopy(self.review)
        review['versions']['C']['checks']['role_and_function']['quotes'] = [{'text': '本人是不存在的医生'}]
        with self.assertRaisesRegex(ValueError, 'absent from this version'):
            self.validate(review=review)

    def test_quote_must_match_cited_group_when_one_is_provided(self):
        review = copy.deepcopy(self.review)
        review['versions']['C']['checks']['actions_and_causality']['quotes'] = [
            {'text': '我和做烘焙内容的团队一起实测。', 'group_index': 1}]
        with self.assertRaisesRegex(ValueError, 'absent from cited group'):
            self.validate(review=review)

    def test_quote_from_other_version_and_cross_version_hash_are_rejected(self):
        review = copy.deepcopy(self.review)
        review['versions']['C']['checks']['role_and_function']['quotes'] = [{'text': '我做直播选品。'}]
        with self.assertRaisesRegex(ValueError, 'absent from this version'):
            self.validate(review=review)
        review = copy.deepcopy(self.review)
        review['versions']['C']['spoken_text_sha256'] = review['versions']['D']['spoken_text_sha256']
        with self.assertRaisesRegex(ValueError, 'spoken text hash mismatch'):
            self.validate(review=review)

    def test_reviewers_must_have_an_explicit_honest_mode(self):
        review = copy.deepcopy(self.review)
        del review['versions']['C']['review_mode']
        with self.assertRaisesRegex(ValueError, 'review_mode'):
            self.validate(review=review)
        review = copy.deepcopy(self.review)
        review['versions']['C']['reviewer_id'] = ' AUTHOR-AGENT '
        with self.assertRaisesRegex(ValueError, 'author and reviewer must differ'):
            self.validate(review=review)

    def test_same_agent_second_pass_is_allowed_but_not_independent(self):
        review = copy.deepcopy(self.review)
        item = review['versions']['C']
        item.update(review_mode='same_agent_second_pass', reviewer_id='author-agent',
                    second_pass_description='不看外部人设说明，重新通读正文并逐项引用检查人物与动作。')
        result = self.validate(review=review)
        c = next(x for x in result['versions'] if x['version'] == 'C')
        self.assertEqual('same_agent_second_pass', c['review_mode'])
        self.assertEqual('not_independent', c['independence'])
        self.assertEqual('not_assessed', result['semantic_truth'])

    def test_same_agent_second_pass_requires_same_identity_and_specific_process(self):
        review = copy.deepcopy(self.review)
        item = review['versions']['C']
        item.update(review_mode='same_agent_second_pass', reviewer_id='another-person',
                    second_pass_description='再次独立于人物说明阅读了完整正文。')
        with self.assertRaisesRegex(ValueError, 'must identify the same'):
            self.validate(review=review)
        item['reviewer_id'] = item['author_id']
        item['second_pass_description'] = '通过'
        with self.assertRaisesRegex(ValueError, 'concrete reason'):
            self.validate(review=review)

    def test_solo_script_may_mark_relationship_check_not_applicable(self):
        draft = {'versions': {'C': '我自己尝试了新方法，过了一段时间再观察。'}}
        self.write(self.draft_path, draft)
        review = self.make_review(draft)
        review['versions']['C']['checks']['relationships_and_channel'] = {
            'verdict': 'not_applicable', 'reason': '整稿为个人体验，没有团队、粉丝和直播间情节。', 'quotes': []}
        self.write(self.review_path, review)
        self.assertEqual('PASS', self.validate()['status'])

    def test_store_director_with_explicit_team_and_channel_is_allowed(self):
        draft = {'versions': {'C': '我是门店督导，兼管门店直播账号。我和直播选品团队测试这款产品，并在自己的直播间邀请粉丝反馈。'}}
        self.write(self.draft_path, draft)
        review = self.make_review(draft)
        item = review['versions']['C']['checks']['relationships_and_channel']
        item.update(reason='正文交代门店直播职责、选品团队以及本人直播间的归属。',
                    quotes=[{'text': '我和直播选品团队测试这款产品'}])
        self.write(self.review_path, review)
        self.assertEqual('PASS', self.validate()['status'])

    def test_full_draft_requires_both_versions_unless_subset_explicit(self):
        review = copy.deepcopy(self.review)
        del review['versions']['D']
        with self.assertRaisesRegex(ValueError, 'exactly the versions'):
            self.validate(review=review)
        self.assertEqual('PASS', self.validate(versions=['C'])['status'])
        with self.assertRaisesRegex(ValueError, 'missing from draft'):
            self.validate(versions=['C', 'E'])

    def test_missing_check_or_nonapplicable_overall_is_rejected(self):
        review = copy.deepcopy(self.review)
        del review['versions']['C']['checks']['shots_and_timing']
        with self.assertRaisesRegex(ValueError, 'seven named checks'):
            self.validate(review=review)
        review = copy.deepcopy(self.review)
        review['versions']['C']['checks']['overall_comprehension'] = {
            'verdict': 'not_applicable', 'reason': '尚未执行整片阅读，不能确认上下文。', 'quotes': []}
        with self.assertRaisesRegex(ValueError, 'whole-script judgment must pass'):
            self.validate(review=review)

    def test_wrong_version_identity_cannot_be_swapped_even_with_matching_root(self):
        review = copy.deepcopy(self.review)
        review['versions']['C']['version'] = 'D'
        with self.assertRaisesRegex(ValueError, 'version label mismatch'):
            self.validate(review=review)

    def test_generic_text_and_group_versions_cannot_appear_together(self):
        draft = copy.deepcopy(self.draft)
        draft['versions'] = {'C': '不同的口播'}
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            s.draft_versions(draft)

    def test_inconsistent_group_versions_fail(self):
        draft = copy.deepcopy(self.draft)
        del draft['groups'][1]['D']
        with self.assertRaisesRegex(ValueError, 'inconsistent version set'):
            s.draft_versions(draft)

    def test_cli_reports_success_and_block_without_pretending_semantic_verification(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = s.main(['--draft', str(self.draft_path), '--review', str(self.review_path)])
        self.assertEqual(0, code)
        result = json.loads(output.getvalue())
        self.assertEqual('not_assessed', result['semantic_truth'])
        bad = copy.deepcopy(self.review)
        bad['versions']['C']['checks']['role_and_function']['verdict'] = 'needs_revision'
        self.write(self.review_path, bad)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = s.main(['--draft', str(self.draft_path), '--review', str(self.review_path)])
        self.assertNotEqual(0, code)
        self.assertEqual('BLOCKED', json.loads(output.getvalue())['status'])

    def test_complete_but_wrong_human_judgment_is_not_magic_semantic_proof(self):
        # The validator must not claim to detect an unsupported team by keywords.
        # A reviewer who incorrectly passes a mechanically complete review can
        # still be wrong; the output must explicitly retain that limitation.
        draft = {'versions': {'C': '我跑门店。我们团队测了半年，我在直播间招募粉丝。'}}
        self.write(self.draft_path, draft)
        review = self.make_review(draft)
        self.write(self.review_path, review)
        result = self.validate()
        self.assertEqual('PASS', result['status'])
        self.assertEqual('not_assessed', result['semantic_truth'])
        self.assertIn('no automatic judgment', result['scope'])


if __name__ == '__main__':
    unittest.main()
