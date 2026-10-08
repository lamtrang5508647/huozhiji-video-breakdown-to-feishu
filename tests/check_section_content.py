"""Behavior checks for the actual authoring failure found in xZ's candidate."""
import copy
import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "skill/analyze-folder-videos-to-feishu/scripts/validate_section_content.py"
spec = importlib.util.spec_from_file_location("section_gate", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SectionGate(unittest.TestCase):
    def setUp(self):
        self.timeline = {"duration_sec": 8, "segments": [
            {"id": f"shot-{i}", "start_ms": (i-1)*2000, "end_ms": i*2000}
            for i in range(1, 5)]}
        self.content = {"timeline_sha256": "current", "visible_brand": "未确认",
                        "strategy": "提问后示范", "audience_hypothesis": "假设",
                        "replication_outline": "问题 → 示范", "sections": []}
        for i in range(2):
            self.content["sections"].append({
                **{key: f"段{i+1}的{key}" for key in ("name", "role", "structure", "question", "audience",
                      "formula", "slots", "conditions", "shoot")},
                "start_ms": i*4000, "end_ms": (i+1)*4000,
                "source_shot_ids": [f"shot-{i*2+1}", f"shot-{i*2+2}"],
                "arc": [{"time_ms": i*4000+k*900, "evidence_shot_id": f"shot-{(i*4000+k*900)//2000+1}",
                         "expression": f"可见动作{k}"} for k in range(4)]})

    def gate(self):
        return module.check(self.content, self.timeline, "current")

    def test_valid_schema_still_requires_content_review(self):
        result = self.gate()
        self.assertEqual(result["status"], "PASS")
        self.assertFalse(result["publication_ready"])

    def test_reject_stale_timeline_binding(self):
        self.content["timeline_sha256"] = "old"
        self.assertEqual(self.gate()["status"], "FAIL")

    def test_reject_extra_event_instead_of_silent_truncation(self):
        self.content["sections"][0]["arc"].append(copy.deepcopy(self.content["sections"][0]["arc"][-1]))
        self.assertEqual(self.gate()["status"], "FAIL")

    def test_reject_changed_shot_reference(self):
        self.content["sections"][0]["arc"][0]["evidence_shot_id"] = "shot-3"
        self.assertEqual(self.gate()["status"], "FAIL")

    def test_reject_shot_range_omission(self):
        self.content["sections"][0]["source_shot_ids"] = ["shot-1"]
        self.assertEqual(self.gate()["status"], "FAIL")

    def test_reject_repeated_shooting_boilerplate(self):
        self.content["sections"][1]["shoot"] = self.content["sections"][0]["shoot"]
        self.assertEqual(self.gate()["status"], "FAIL")

    def test_reject_array_as_reading_sentence(self):
        self.content["sections"][0]["slots"] = ["source", "evidence"]
        self.assertEqual(self.gate()["status"], "FAIL")

    def test_visual_boundary_is_not_audio_alignment(self):
        self.content["boundary_review"] = {"status": "full_source_reviewed"}
        self.assertEqual(self.gate()["status"], "PASS")
        self.content["machine_word_alignment_verified"] = True
        self.assertEqual(self.gate()["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
