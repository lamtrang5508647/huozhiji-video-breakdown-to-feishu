"""Deterministic alignment retains evidence and never fabricates spoken sentence timing."""
import copy
import csv
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "skill/analyze-folder-videos-to-feishu/scripts/script_shot_alignment.py"
SPEC = importlib.util.spec_from_file_location("script_shot_alignment_tested", SCRIPT)
alignment = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(alignment)


def segment(sid="S01", start=0, end=2000, shots=None):
    shots = shots or [("shot-001", 0, 1000), ("shot-002", 1000, 2000)]
    return {"id": sid, "name": "脚本段<一>", "start_ms": start, "end_ms": end,
            "script_function": "问题引入", "source_shot_ids": [shot[0] for shot in shots],
            "source_shot_spans": [{"shot_id": shot, "source_shot_start_ms": left,
                "source_shot_end_ms": right, "segment_intersection_start_ms": max(start, left),
                "segment_intersection_end_ms": min(end, right)} for shot, left, right in shots],
            "source_excerpt": {"type": "subtitle_excerpts_only", "items": [
                {"shot_id": shot, "text": '原字幕<&>，"不是结论"\n继续。',
                 "intersection_start_ms": max(start, left), "intersection_end_ms": min(end, right)}
                for shot, left, right in shots]},
            "reusable_formula": {"structure": "[问题] → [观察]", "example_sentence_slots": "为什么[现象]？",
                                 "conditions": "有原始观察", "extra": ["原始对象"]},
            "shot_groups": [{"source_group_id": "G01", "source_shot_ids": [shots[0][0]],
                "source_observation": "<旁白>&画面", "replication_shooting_plan": {
                    "subject_action": "拍摄<物品>", "shot_size_angle": "近景", "edit_transition": "保持完整动作"}}]}


def manifest(segments=None):
    return {"video_id": "source-001", "revision": "review-2", "source": {
        "sha256": "a" * 64, "timeline_manifest_sha256": "b" * 64},
        "transcript_status": "subtitle_excerpts_only", "audio_review_status": "not_reviewed",
        "segments": segments or [segment()]}


class ScriptShotAlignmentTests(unittest.TestCase):
    def test_single_shot_crosses_segments_with_distinct_offsets(self):
        shot = [("shot-001", 0, 2000)]
        result = alignment.build_alignment(manifest([segment("S01", 0, 800, shot), segment("S02", 800, 2000, shot)]))
        first, second = result["rows"]
        self.assertEqual((first["source_start_ms"], first["source_end_ms"]), (0, 800))
        self.assertEqual((second["segment_offset_start_ms"], second["segment_offset_end_ms"]), (0, 1200))
        self.assertEqual((second["shot_offset_start_ms"], second["shot_offset_end_ms"]), (800, 2000))
        self.assertEqual(second["sentence_audio_alignment"], "not_verified")

    def test_empty_subtitle_retains_shot_and_explicit_state(self):
        value = segment()
        value["source_excerpt"]["items"] = value["source_excerpt"]["items"][:1]
        rows = alignment.segment_alignment(value)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["source_text"], "")
        self.assertEqual(rows[1]["source_text_status"], "no_subtitle_provided")
        self.assertIn(alignment.EMPTY_SUBTITLE, alignment.render_alignment_table(rows))

    def test_repeated_text_and_item_positions_are_not_deduplicated(self):
        value = segment()
        value["source_excerpt"]["items"].insert(1, copy.deepcopy(value["source_excerpt"]["items"][0]))
        rows = alignment.segment_alignment(value)
        self.assertEqual([i["item_index"] for i in rows[0]["excerpt_items"]], [0, 1])
        self.assertEqual(rows[1]["excerpt_items"][0]["item_index"], 2)
        text = value["source_excerpt"]["items"][0]["text"]
        self.assertEqual(rows[0]["source_text"], text + "\n" + text)
        self.assertEqual(rows[1]["source_text"], text)

    def test_missing_item_times_are_not_inferred_as_sentence_times(self):
        value = segment()
        for item in value["source_excerpt"]["items"]:
            del item["intersection_start_ms"]
            del item["intersection_end_ms"]
        row = alignment.segment_alignment(value)[0]
        self.assertEqual(row["timing_granularity"], "shot_segment_intersection")
        self.assertNotIn("intersection_start_ms", row["excerpt_items"][0])
        self.assertEqual(row["sentence_audio_alignment"], "not_verified")

    def test_wrong_excerpt_time_or_foreign_shot_rejected(self):
        for key, wrong in (("intersection_start_ms", 1), ("intersection_end_ms", 999),
                           ("intersection_start_ms", False), ("shot_id", "other-video-shot")):
            with self.subTest(key=key, wrong=wrong):
                value = segment()
                value["source_excerpt"]["items"][0][key] = wrong
                with self.assertRaises(ValueError):
                    alignment.segment_alignment(value)

    def test_span_order_duplicates_bounds_and_coverage_rejected(self):
        mutations = [lambda s: s["source_shot_spans"].reverse(),
                     lambda s: s["source_shot_spans"].append(copy.deepcopy(s["source_shot_spans"][0])),
                     lambda s: s["source_shot_spans"].pop(),
                     lambda s: s["source_shot_spans"][0].update(segment_intersection_end_ms=999),
                     lambda s: s["source_shot_spans"][0].update(source_shot_start_ms=-1),
                     lambda s: s["source_shot_spans"][0].update(source_shot_start_ms=False),
                     lambda s: s["source_shot_spans"][0].update(source_shot_end_ms=0),
                     lambda s: s["source_shot_ids"].reverse()]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                value = segment()
                mutation(value)
                with self.assertRaises(ValueError):
                    alignment.segment_alignment(value)

    def test_guidance_is_only_selected_by_explicit_shot_membership(self):
        value = segment()
        foreign = {"source_group_id": "foreign", "source_shot_ids": ["foreign-shot"],
                   "source_observation": "MUST NOT INHERIT"}
        value["shot_groups"].append(foreign)
        rows = alignment.segment_alignment(value)
        self.assertEqual(rows[0]["shot_guidance"], [value["shot_groups"][0]])
        self.assertEqual(rows[1]["shot_guidance"], [])
        html = alignment.render_alignment_table(rows)
        self.assertNotIn("MUST NOT INHERIT", html)
        self.assertIn(alignment.MISSING_GUIDANCE, html)
        self.assertEqual(rows[0]["formula_scope"], "segment")
        self.assertEqual(rows[0]["shot_guidance_scope"], "shot_group")

    def test_context_is_explicit_and_mixed_items_are_preserved(self):
        value = segment()
        value["source_excerpt"]["items"][0]["context_only"] = True
        rows = alignment.segment_alignment(value)
        self.assertTrue(rows[0]["context_only"])
        self.assertFalse(rows[1]["context_only"])
        mixed = copy.deepcopy(value["source_excerpt"]["items"][0])
        mixed["context_only"] = False
        value["source_excerpt"]["items"].insert(1, mixed)
        self.assertFalse(alignment.segment_alignment(value)[0]["context_only"])
        value["boundary_context"] = [{"source_shot_ids": ["shot-001"]}]
        self.assertTrue(alignment.segment_alignment(value)[0]["context_only"])
        value["boundary_context"] = [{"source_shot_spans": [{"shot_id": "shot-002"}]}]
        self.assertTrue(alignment.segment_alignment(value)[1]["context_only"])

    def test_foreign_boundary_context_is_rejected(self):
        value = segment()
        value["boundary_context"] = [{"source_shot_ids": ["foreign-shot"]}]
        with self.assertRaises(ValueError):
            alignment.segment_alignment(value)

    def test_html_escapes_content_and_player_attributes(self):
        rows = alignment.segment_alignment(segment())
        rendered = alignment.render_alignment_table(rows, player_id='p" onmouseover="x')
        self.assertNotIn('<旁白>', rendered)
        self.assertNotIn('<物品>', rendered)
        self.assertIn('&lt;旁白&gt;&amp;画面', rendered)
        self.assertIn('data-player="p&quot; onmouseover=&quot;x"', rendered)
        self.assertIn('data-start="1.000" data-end="2.000"', rendered)
        self.assertNotIn('<button', alignment.render_alignment_table(rows))

    def test_json_csv_preserve_original_characters_nested_objects_and_identity(self):
        value = manifest()
        before = copy.deepcopy(value)
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            self.assertEqual(alignment.write_alignment_files(output, value), {"rows_count": 2})
            raw = (output / "script-shot-alignment.csv").read_bytes()
            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
            with (output / "script-shot-alignment.csv").open(encoding="utf-8-sig", newline="") as handle:
                records = list(csv.DictReader(handle))
            saved = json.loads((output / "script-shot-alignment.json").read_text(encoding="utf-8"))
            for key in ("video_id", "revision", "transcript_status", "audio_review_status"):
                self.assertEqual(saved[key], value[key])
                self.assertEqual(records[0][key], value[key])
            self.assertEqual(saved["source_sha256"], value["source"]["sha256"])
            self.assertEqual(saved["timeline_sha256"], value["source"]["timeline_manifest_sha256"])
            self.assertEqual(records[0]["source_text"], value["segments"][0]["source_excerpt"]["items"][0]["text"])
            self.assertEqual(json.loads(records[0]["reusable_formula"]), value["segments"][0]["reusable_formula"])
            self.assertEqual(json.loads(records[0]["shot_guidance"]), value["segments"][0]["shot_groups"])
        self.assertEqual(value, before)

    def test_return_values_do_not_share_mutable_input_objects(self):
        value = segment()
        before = copy.deepcopy(value)
        rows = alignment.segment_alignment(value)
        rows[0]["reusable_formula"]["extra"].append("changed")
        rows[0]["shot_guidance"][0]["source_observation"] = "changed"
        rows[0]["excerpt_items"][0]["text"] = "changed"
        self.assertEqual(value, before)

    def test_invalid_identity_duplicate_segments_and_inconsistent_shot_times_rejected(self):
        for field in ("sha256", "timeline_manifest_sha256"):
            value = manifest()
            value["source"][field] = "not-a-hash"
            with self.assertRaises(ValueError):
                alignment.build_alignment(value)
        with self.assertRaises(ValueError):
            alignment.build_alignment(manifest([segment(), segment()]))
        value = manifest([segment("S01", 0, 1000, [("shot-001", 0, 1500)]),
                          segment("S02", 1000, 2000, [("shot-001", 0, 2000)])])
        with self.assertRaisesRegex(ValueError, "inconsistent original times"):
            alignment.build_alignment(value)


if __name__ == "__main__":
    unittest.main()
