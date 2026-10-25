"""Creative sidecars preserve source identities; synthetic tests assert no real results."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skill/analyze-folder-videos-to-feishu/scripts/creative_workflow.py"


class CreativeWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), "creative workbench CLI is not implemented yet")
        sys.path.insert(0, str(SCRIPT.parent))
        spec = importlib.util.spec_from_file_location("creative_workflow_tested", SCRIPT)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def cli(self, *args, ok=True):
        result = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                                capture_output=True, text=True, encoding="utf-8")
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        else:
            self.assertNotEqual(result.returncode, 0, result.stderr + result.stdout)
        return result

    def demo(self):
        self.cli("demo", "--output", self.root / "demo")
        self.bundle_path = self.root / "demo/creative-workflow.json"
        return json.loads(self.bundle_path.read_text(encoding="utf-8"))

    def validate(self, bundle):
        return self.module.validate_bundle(bundle, self.bundle_path.parent)

    def test_init_is_editable_explicit_draft_with_four_cards(self):
        self.cli("init", "--output", self.root / "draft", "--product-id", "our-product", "--product-name", "待填商品")
        path = self.root / "draft/creative-workflow.json"
        bundle = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(bundle["source_status"], "not_provided")
        self.assertEqual(bundle["facts"], [])
        self.assertEqual(set(bundle["cards"]), {"topic", "script", "shooting", "test"})
        self.assertTrue(all(bundle["cards"].values()))
        result = json.loads(self.cli("validate", "--bundle", path).stdout)
        self.assertEqual(result["status"], "DRAFT")
        self.assertFalse(result["factual_verification"])
        self.assertTrue(result["review_gaps"])
        self.cli("validate", "--bundle", path, "--require-ready", ok=False)

    def test_demo_full_cards_remain_explicitly_synthetic_and_untested(self):
        bundle = self.demo()
        result = self.validate(bundle)
        self.assertEqual(result["status"], "READY_FOR_HUMAN_REVIEW")
        self.assertTrue(bundle["synthetic"])
        self.assertFalse(result["listening_verification"])
        self.assertFalse(result["factual_verification"])
        self.assertEqual({v["mode"] for v in bundle["variants"]}, {"exploration", "iteration"})
        self.assertTrue(all(c["status"] == "untested" for c in bundle["cards"]["test"]))
        self.assertTrue((self.bundle_path.parent / "report/report.html").is_file())

    def test_source_hash_change_and_wrong_segment_shot_or_interval_rejected(self):
        original = self.demo()
        changes = [dict(source_sha256="f" * 64), dict(manifest_sha256="f" * 64),
                   dict(segment_id="missing"), dict(source_shot_ids=["other-shot"]),
                   dict(start_ms=-1), dict(end_ms=3000), dict(start_ms=True)]
        for fields in changes:
            with self.subTest(fields=fields):
                value = copy.deepcopy(original)
                value["evidence"][0]["source_ref"].update(fields)
                with self.assertRaises(ValueError):
                    self.validate(value)
        manifest_path = self.bundle_path.parent / original["sources"][0]["manifest_path"]
        manifest_path.write_text(manifest_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "manifest.*hash"):
            self.validate(original)

    def test_source_claim_exact_text_cannot_be_rewritten_or_promoted(self):
        original = self.demo()
        for fields in ({"exact_text": "作者从没说过的话"}, {"verification_status": "verified"},
                       {"source_pointer": "/title"}, {"origin": "product_fact"}):
            with self.subTest(fields=fields):
                value = copy.deepcopy(original)
                value["claims"][0].update(fields)
                with self.assertRaises(ValueError):
                    self.validate(value)

    def test_claim_evidence_wrong_product_missing_binding_and_generated_proof_rejected(self):
        original = self.demo()
        for mutate in (
            lambda b: b["claims"][0].update(evidence_ids=["missing"]),
            lambda b: b["evidence"][0].update(kind="generated_visual"),
            lambda b: b["claims"][0].update(product_id="other"),
            lambda b: b["evidence"][0].update(verification_status="verified"),
            lambda b: b["facts"][0].update(source={"kind": "source_claim", "locator": "claim-001"}),
        ):
            with self.subTest(mutate=mutate):
                value = copy.deepcopy(original)
                value["products"].append({"id": "other", "name": "不同商品"})
                mutate(value)
                with self.assertRaises(ValueError):
                    self.validate(value)

    def test_duplicate_ids_and_lineage_cycles_rejected(self):
        original = self.demo()
        for mutate in (
            lambda b: b["variants"].append(copy.deepcopy(b["variants"][0])),
            lambda b: b["variants"][0].update(parent_variant_id=b["variants"][1]["id"]),
            lambda b: b["variants"][1].update(parent_variant_id=None),
            lambda b: b["cards"]["script"][0].update(variant_id="missing"),
        ):
            with self.subTest(mutate=mutate):
                value = copy.deepcopy(original)
                mutate(value)
                with self.assertRaises(ValueError):
                    self.validate(value)

    def test_real_comments_require_actual_archive_hash_and_exact_text(self):
        bundle = self.demo()
        archive = self.bundle_path.parent / "real-comment.txt"
        archive.write_text("这是一条由测试构造的评论。", encoding="utf-8")
        bundle["comments"] = [{"id": "real-001", "product_id": "demo-product",
            "text": "这是一条由测试构造的评论。", "interpretation": "仅来源校验测试，不宣称真实评论",
            "provenance": {"kind": "real_user", "platform": "test", "source_url": "https://example.invalid/comment/1",
                "comment_id": "comment-1", "collected_at": "2026-10-25T10:00:00+08:00",
                "archive_path": "real-comment.txt", "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}}]
        self.validate(bundle)
        bundle["comments"][0]["text"] = "编造了另一条"
        with self.assertRaisesRegex(ValueError, "comment.*exact"):
            self.validate(bundle)
        bundle["comments"][0]["text"] = "这是一条由测试构造的评论。"
        archive.write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "hash"):
            self.validate(bundle)

    def test_visual_and_test_card_completeness_and_source_attribution_enforced(self):
        original = self.demo()
        for mutate in (
            lambda b: b["cards"]["shooting"][0]["shots"][0].update(acceptance_checks=[]),
            lambda b: b["cards"]["shooting"][0]["shots"][0].update(angle=""),
            lambda b: b["cards"]["test"][0].update(metric_definition=""),
            lambda b: b["cards"]["test"][0].update(status="proven_winner"),
            lambda b: b["cards"]["script"][0]["blocks"][0].update(claim_ids=[b["claims"][0]["id"]], attribution="target_product_fact"),
        ):
            with self.subTest(mutate=mutate):
                value = copy.deepcopy(original)
                mutate(value)
                with self.assertRaises(ValueError):
                    self.validate(value)

    def test_active_html_rejected_and_inert_text_escaped(self):
        original = self.demo()
        for unsafe in ('<script>alert(1)</script>', '<img src=x onerror="x">', 'javascript:alert(1)', '<iframe src="x">'):
            value = copy.deepcopy(original)
            value["products"][0]["name"] = unsafe
            with self.subTest(unsafe=unsafe), self.assertRaisesRegex(ValueError, "active HTML"):
                self.validate(value)
        original["products"][0]["name"] = '<商品>&"细节"'
        report = self.module.render_html(original, self.validate(original))
        self.assertIn('&lt;商品&gt;&amp;', report)
        self.assertNotIn('<商品>', report)
        self.assertIn("script-src 'none'", report)

    def test_render_and_search_are_deterministic_and_do_not_change_sources(self):
        original = self.demo()
        source_path = self.bundle_path.parent / original["sources"][0]["manifest_path"]
        before = source_path.read_bytes()
        self.cli("render", "--bundle", self.bundle_path, "--output", self.root / "render")
        self.assertEqual(source_path.read_bytes(), before)
        for name in ("report.html", "bundle.json", "cards.csv", "search-index.json", "validation.json"):
            self.assertTrue((self.root / "render" / name).is_file())
        exported = json.loads((self.root / "render/bundle.json").read_text(encoding="utf-8"))
        expected = copy.deepcopy(original)
        self.module.rebase_paths(expected, self.bundle_path.parent)
        self.assertEqual(exported, expected)
        result = json.loads(self.cli("search", "--root", self.root / "demo", "--query", "单手").stdout)
        self.assertGreater(len(result["results"]), 0)
        self.assertEqual(result["method"], "literal_keyword_all_terms")
        absent = json.loads(self.cli("search", "--root", self.root / "demo", "--query", "绝不出现词").stdout)
        self.assertEqual(absent["results"], [])

    def test_outputs_and_versioned_copy_never_overwrite_original_bundle(self):
        original = self.demo()
        before = self.bundle_path.read_bytes()
        self.cli("demo", "--output", self.root / "demo", ok=False)
        self.cli("render", "--bundle", self.bundle_path, "--output", self.root / "demo/report", ok=False)
        self.cli("init", "--from-bundle", self.bundle_path, "--output", self.root / "v2")
        newer = json.loads((self.root / "v2/creative-workflow.json").read_text(encoding="utf-8"))
        self.assertEqual(newer["revision"], original["revision"] + 1)
        self.assertEqual(newer["previous_bundle"]["sha256"], hashlib.sha256(before).hexdigest())
        self.assertEqual(self.bundle_path.read_bytes(), before)
        self.cli("validate", "--bundle", self.root / "v2/creative-workflow.json")

    def test_installable_assets_have_editable_template_and_complete_generation_example(self):
        assets = SCRIPT.parent.parent / "assets"
        for name in ("creative-workflow.template.json", "creative-workflow.example.json"):
            value = json.loads((assets / name).read_text(encoding="utf-8"))
            result = self.module.validate_bundle(value, assets)
            self.assertTrue(result["structural_valid"])
            self.assertEqual(set(value["cards"]), {"topic", "script", "shooting", "test"})
        self.assertEqual(result["status"], "READY_FOR_HUMAN_REVIEW")
        self.assertTrue(value["synthetic"])
        self.assertEqual(value["source_status"], "not_provided")

    def test_report_bundle_is_reusable_with_rebased_read_only_sources(self):
        original = self.demo()
        exported_path = self.bundle_path.parent / "report/bundle.json"
        exported = json.loads(exported_path.read_text(encoding="utf-8"))
        result = self.module.validate_bundle(exported, exported_path.parent)
        self.assertTrue(result["structural_valid"])
        self.assertEqual(exported["claims"], original["claims"])

    def test_source_import_and_actual_media_timeline_tampering(self):
        bundle = self.demo()
        manifest_path = self.bundle_path.parent / bundle["sources"][0]["manifest_path"]
        self.cli("init", "--output", self.root / "import", "--source-manifest", manifest_path)
        imported = json.loads((self.root / "import/creative-workflow.json").read_text(encoding="utf-8"))
        self.assertEqual(imported["source_status"], "provided")
        self.assertEqual(imported["claims"], [])  # Never automatically reinterpret source claims.
        timeline_path = manifest_path.parent / "timeline.json"
        old_timeline = timeline_path.read_bytes()
        timeline = json.loads(old_timeline)
        timeline["segments"][0]["end_ms"] = 1999
        timeline_path.write_text(json.dumps(timeline, ensure_ascii=False), encoding="utf-8")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["source"]["timeline_manifest_sha256"] = hashlib.sha256(timeline_path.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        bundle["sources"][0]["manifest_sha256"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, "actual timeline"):
            self.validate(bundle)

    def test_empty_ready_workbench_and_unfilled_template_do_not_claim_readiness(self):
        bundle = self.module.make_bundle()
        bundle["state"] = "ready_for_review"
        result = self.module.validate_bundle(bundle, self.root)
        self.assertEqual(result["status"], "DRAFT")
        bundle = self.module.make_bundle("demo-product", "Synthetic", True)
        bundle["topics"] = []
        bundle["decisions"] = []
        bundle["variants"] = []
        bundle["cards"] = {key: [] for key in self.module.CARD_TYPES}
        result = self.module.validate_bundle(bundle, self.root)
        self.assertEqual(result["status"], "DRAFT")

    def test_claim_excerpt_pointer_cannot_use_another_shot_or_malformed_path(self):
        bundle = self.demo()
        manifest_path = self.bundle_path.parent / bundle["sources"][0]["manifest_path"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["segments"][0]["source_claims_extra"] = [bundle["claims"][0]["exact_text"]]
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        bundle["sources"][0]["manifest_sha256"] = digest
        for item in bundle["claims"] + bundle["evidence"]:
            item["source_ref"]["manifest_sha256"] = digest
        for pointer in ("/segments/0/source_claims_extra/0", "/segments/0/source_claims/-1"):
            bundle["claims"][0]["source_pointer"] = pointer
            with self.subTest(pointer=pointer), self.assertRaises(ValueError):
                self.validate(bundle)

    def test_observed_brand_cannot_be_changed_without_changing_bound_manifest(self):
        bundle = self.demo()
        bundle["sources"][0]["observed_brand"] = "错误改为本品品牌"
        with self.assertRaisesRegex(ValueError, "observed_brand"):
            self.validate(bundle)

    def test_duplicate_script_cards_for_variant_rejected_regardless_of_order(self):
        original = self.demo()
        for prepend in (True, False):
            bundle = copy.deepcopy(original)
            duplicate = copy.deepcopy(bundle["cards"]["script"][0])
            duplicate["id"] = "another-script-card"
            duplicate["blocks"][0]["id"] = "another-script-block"
            if prepend:
                bundle["cards"]["script"].insert(0, duplicate)
            else:
                bundle["cards"]["script"].append(duplicate)
            with self.subTest(prepend=prepend), self.assertRaisesRegex(ValueError, "one script card"):
                self.validate(bundle)

    def test_one_viable_visual_option_is_valid_when_selection_constraints_are_explicit(self):
        bundle = self.demo()
        shot = bundle["cards"]["shooting"][0]["shots"][0]
        shot["alternatives"] = shot["alternatives"][:1]
        shot["selection_reason"] = "本次仅能安排一个固定机位；连续动作实拍可回答操作问题，其他方案尚未有可用素材。"
        self.assertTrue(self.validate(bundle)["structural_valid"])
        del shot["selection_reason"]
        with self.assertRaisesRegex(ValueError, "selection_reason"):
            self.validate(bundle)
        shot["alternatives"] = []
        with self.assertRaises(ValueError):
            self.validate(bundle)


if __name__ == "__main__":
    unittest.main()
