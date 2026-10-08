"""Local v9 review-application contract checks; no media or network access."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_SCRIPTS = ROOT / "skill" / "analyze-folder-videos-to-feishu" / "scripts"
sys.path.insert(0, str(SKILL_SCRIPTS))

from apply_phrase_review import ReviewError, apply_review  # noqa: E402
from phrase_annotation import canonical_taxonomy  # noqa: E402


def run(text: str, family: str = "unclassified", tag: str = "测试连接短语", reason: str = "验证精确来源覆盖。"):
    return [{"text": text, "char_start": 0, "char_end": len(text), "category_family": family,
             "function_tag": tag, "reason": reason}]


def fixture():
    analysis = {
        "video_id": "BVfixture",
        "title": "source title",
        "taxonomy": [],
        "sections": [{"id": "s1", "formula": "全段公式"}],
        "units": [{"id": "u1", "section_id": "s1", "source_text": "源字幕", "phrase_runs": []}],
        "source_formula": [{"section_id": "s1", "label": "源公式", "category_family": "hook_pain", "note": "preserve"}],
        "reusable_formula": [{"section_id": "s1", "label": "复用公式", "category_family": "hook_pain", "note": "preserve"}],
    }
    review = {"BVfixture": {
        "analysis_sha256": "",
        "unit_annotations": [{"id": "u1", "source_text": "源字幕", "reviewed": True,
                              "phrase_runs": run("源字幕", "experience_claim", "个人字幕观察", "保留原句边界。")}],
        "source_formula": [{"section_id": "s1", "label": "源公式", "category_family": "unclassified",
                            "source_unit_ids": ["u1"], "phrase_runs": run("源公式", tag="源公式功能", reason="仅作公式步骤描述。")}],
        "reusable_formula": [{"section_id": "s1", "label": "复用公式", "category_family": "unclassified",
                              "source_unit_ids": ["u1"], "phrase_runs": run("复用公式", tag="复用步骤功能", reason="仅作复用公式描述。")}],
        "sections_formula_runs": [{"section_id": "s1", "formula": "全段公式",
                                   "formula_runs": run("全段公式", tag="完整段公式功能", reason="覆盖原段公式全文。")}],
    }}
    return analysis, review


class ApplyPhraseReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.analysis, self.review = fixture()
        self.analysis_path = self.root / "analysis.json"
        self.review_path = self.root / "review.json"
        self.write_analysis()

    def tearDown(self):
        self.temp.cleanup()

    def write_analysis(self):
        self.analysis_path.write_text(json.dumps(self.analysis, ensure_ascii=False), encoding="utf-8")
        self.review["BVfixture"]["analysis_sha256"] = hashlib.sha256(self.analysis_path.read_bytes()).hexdigest()
        self.review_path.write_text(json.dumps(self.review, ensure_ascii=False), encoding="utf-8")

    def apply_or_error(self, mutate=None):
        if mutate:
            mutate(self.review["BVfixture"])
            self.review_path.write_text(json.dumps(self.review, ensure_ascii=False), encoding="utf-8")
        return apply_review(self.analysis_path, self.review_path)

    def test_valid_review_preserves_source_and_uses_canonical_v9(self):
        result = self.apply_or_error()
        self.assertEqual(result["annotation_profile"], "phrase-functions/v9")
        self.assertEqual(result["taxonomy"], canonical_taxonomy())
        self.assertEqual(result["units"][0]["source_text"], "源字幕")
        self.assertEqual(result["units"][0]["phrase_runs"][0]["category_family"], "experience_claim")
        self.assertEqual(result["sections"][0]["formula_runs"][0]["text"], "全段公式")
        self.assertEqual(result["source_formula"][0]["note"], "preserve")
        self.assertEqual(result["phrase_review_metadata"]["semantic_status"], "not_asserted")

    def test_missing_formula_groups_block_v9_creation(self):
        def remove_formula_groups(payload):
            payload.pop("source_formula")
            payload.pop("reusable_formula")
            payload.pop("sections_formula_runs")
        with self.assertRaisesRegex(ReviewError, "source_formula.*reusable_formula.*sections_formula_runs"):
            self.apply_or_error(remove_formula_groups)

    def test_rejects_stale_analysis_sha(self):
        self.review["BVfixture"]["analysis_sha256"] = "0" * 64
        self.review_path.write_text(json.dumps(self.review, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(ReviewError, "analysis_sha256 mismatch"):
            apply_review(self.analysis_path, self.review_path)

    def test_rejects_missing_unit(self):
        with self.assertRaisesRegex(ReviewError, r"unit coverage mismatch.*missing=\['u1'\]"):
            self.apply_or_error(lambda payload: payload.update(unit_annotations=[]))

    def test_rejects_duplicate_unit(self):
        with self.assertRaisesRegex(ReviewError, "duplicate unit id"):
            self.apply_or_error(lambda payload: payload["unit_annotations"].append(copy.deepcopy(payload["unit_annotations"][0])))

    def test_rejects_extra_unit(self):
        def add_extra(payload):
            extra = copy.deepcopy(payload["unit_annotations"][0])
            extra["id"] = "not-in-source"
            payload["unit_annotations"].append(extra)
        with self.assertRaisesRegex(ReviewError, r"unit coverage mismatch.*extra=\['not-in-source'\]"):
            self.apply_or_error(add_extra)

    def test_rejects_source_text_mismatch(self):
        def change(payload): payload["unit_annotations"][0].update(source_text="改过的字幕")
        with self.assertRaisesRegex(ReviewError, "source_text mismatch"):
            self.apply_or_error(change)

    def test_rejects_bad_character_offset(self):
        def change(payload): payload["unit_annotations"][0]["phrase_runs"][0]["char_end"] = 2
        with self.assertRaisesRegex(ReviewError, "offsets"):
            self.apply_or_error(change)

    def test_rejects_formula_section_cross_reference(self):
        def change(payload): payload["reusable_formula"][0]["source_unit_ids"] = ["unknown"]
        with self.assertRaisesRegex(ReviewError, "cross section boundary"):
            self.apply_or_error(change)

    def test_selected_reading_span_requires_explicit_fresh_scope_and_is_preserved(self):
        source_text = "图示DHT字幕DHC"
        self.analysis["units"][0].update({
            "source_text": source_text,
            "reading_span": {"char_start": 0, "char_end": 5},
            "reading_note": "仅显示图示术语，与底部字幕存在冲突。",
        })
        self.review["BVfixture"]["unit_annotations"][0].update({
            "source_text": source_text,
            "phrase_runs": run(source_text, "experience_claim", "图示与字幕术语冲突", "两种原文读法并列保留。"),
        })
        self.write_analysis()
        with self.assertRaisesRegex(ReviewError, "explicit reviewed_scope"):
            apply_review(self.analysis_path, self.review_path)
        scope = {
            "approved": True,
            "source_text_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
            "kept_text": source_text[:5],
            "reason": "为并列核验图示和字幕，保留图示侧原文范围。",
        }
        self.review["BVfixture"]["unit_annotations"][0]["reviewed_scope"] = scope
        self.review_path.write_text(json.dumps(self.review, ensure_ascii=False), encoding="utf-8")
        result = apply_review(self.analysis_path, self.review_path)
        self.assertEqual(result["units"][0]["reading_span"], {"char_start": 0, "char_end": 5})
        self.assertEqual(result["units"][0]["reviewed_scope"], scope)
        self.assertEqual(result["units"][0]["source_text"], source_text)

    def test_observation_note_is_preserved(self):
        unit = self.analysis["units"][0]
        unit["display_kind"] = "observation_note"
        unit["reading_note"] = "无可读口播字幕摘录。"
        self.write_analysis()
        result = apply_review(self.analysis_path, self.review_path)
        self.assertEqual(result["units"][0]["display_kind"], "observation_note")
        self.assertEqual(result["units"][0]["reading_note"], "无可读口播字幕摘录。")

    def test_original_formula_step_order_allows_multiple_steps_per_section(self):
        section_ids = [f"s{i}" for i in range(1, 8)]
        self.analysis["sections"] = [{"id": sid, "formula": f"整段公式{sid}"} for sid in section_ids]
        self.analysis["units"] = [
            {"id": f"u{i}", "section_id": sid, "source_text": f"原文{i}", "phrase_runs": []}
            for i, sid in enumerate(section_ids, 1)
        ]
        source_counts = [2, 2, 2, 2, 1, 1, 1]
        reusable_counts = [2, 2, 2, 1, 1, 1, 1]
        self.analysis["source_formula"] = []
        self.analysis["reusable_formula"] = []
        payload = self.review["BVfixture"]
        payload["unit_annotations"] = [
            {"id": f"u{i}", "source_text": f"原文{i}", "reviewed": True,
             "phrase_runs": run(f"原文{i}", "experience_claim", "个人原文记录", "保留逐字字幕。")}
            for i in range(1, 8)
        ]
        for group, counts in (("source_formula", source_counts), ("reusable_formula", reusable_counts)):
            rows=[]
            for sec_index, (sid, count) in enumerate(zip(section_ids, counts), 1):
                for step_index in range(1, count + 1):
                    label = f"{group}-{sid}-step{step_index}"
                    step = {"section_id": sid, "label": label, "category_family": "hook_pain", "note": "preserve"}
                    self.analysis[group].append(copy.deepcopy(step))
                    rows.append({**step, "category_family": "unclassified", "source_unit_ids": [f"u{sec_index}"],
                                 "phrase_runs": run(label, tag="公式步骤功能", reason="对应当前段落的原子公式步骤。")})
            payload[group] = rows
        payload["sections_formula_runs"] = [
            {"section_id": sid, "formula": f"整段公式{sid}",
             "formula_runs": run(f"整段公式{sid}", tag="整段公式角色", reason="完整覆盖这一段原有公式。")}
            for sid in section_ids
        ]
        self.write_analysis()
        result = apply_review(self.analysis_path, self.review_path)
        self.assertEqual(len(result["source_formula"]), 11)
        self.assertEqual(len(result["reusable_formula"]), 10)
        self.assertEqual([step["label"] for step in result["source_formula"]],
                         [step["label"] for step in self.analysis["source_formula"]])

if __name__ == "__main__":
    unittest.main(verbosity=2)
