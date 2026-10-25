"""Synthetic-only contracts for measured feedback, provenance and safe exports."""
import copy
import csv
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skill/analyze-folder-videos-to-feishu/scripts/performance_feedback.py"


class PerformanceFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.exists(), "Measured feedback workflow is not implemented")
        spec = importlib.util.spec_from_file_location("performance_feedback", SCRIPT)
        self.feedback = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.feedback)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "state.json"
        self.mapping = {
            "schema_version": 1, "timezone": "+08:00", "synthetic": True,
            "columns": {key: key for key in ("platform", "creative_id", "date", "account", "goal", "attribution_window", "currency", "price", "entry", "placement")},
            "cohort_fields": ["price", "entry", "placement"],
            "numeric_fields": {key: {"column": key, "kind": "amount" if key in ("spend", "revenue") else "count"} for key in ("impressions", "clicks", "conversions", "spend", "revenue")},
            "metrics": {
                "ctr": {"label": "点击率", "numerator": "clicks", "denominator": "impressions", "stage": "click"},
                "cvr": {"label": "点击成交率", "numerator": "conversions", "denominator": "clicks", "stage": "conversion"},
                "roas": {"label": "成交金额/花费", "numerator": "revenue", "denominator": "spend", "stage": "conversion"}},
            "sample_criteria": {"impressions": 1000, "conversions": 20},
            "creative_map": [{"platform": "platform-1", "account": "account-1", "creative_id": "ad-1", "variant_id": "variant-1"}, {"platform": "platform-1", "account": "account-1", "creative_id": "ad-2", "variant_id": "variant-1"}, {"platform": "platform-1", "account": "account-1", "creative_id": "ad-3", "variant_id": "variant-2"}],
        }
        self.registry = {"variants": [self.variant("variant-1"), self.variant("variant-2")]}
        self.row = {"platform": "platform-1", "creative_id": "ad-1", "date": "2026-10-20", "account": "account-1", "goal": "purchase", "attribution_window": "click_7d", "currency": "CNY", "price": "99", "entry": "product-page", "placement": "feed", "impressions": "100", "clicks": "10", "conversions": "2", "spend": "20.50", "revenue": "198"}

    @staticmethod
    def variant(variant_id):
        return {"id": variant_id, "product_id": "product-1", "hypothesis": "展示使用过程后点击是否变化", "changed_variable": "开头演示", "control_notes": "价格入口保持一致；未随机分组", "parent_variant_id": None, "mode": "exploration"}

    def files(self, rows, name="export", mapping=None, registry=None):
        csv_path = self.root / (name + ".csv")
        columns = list(dict.fromkeys(key for row in rows for key in row))
        with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
        mapping_path, registry_path = self.root / "mapping.json", self.root / "registry.json"
        mapping_path.write_text(json.dumps(mapping or self.mapping, ensure_ascii=False), encoding="utf-8")
        registry_path.write_text(json.dumps(registry or self.registry, ensure_ascii=False), encoding="utf-8")
        return dict(state=self.state, csv_path=csv_path, mapping_path=mapping_path, registry_path=registry_path)

    def test_totals_derive_ratios_and_link_variant_hypothesis(self):
        second = dict(self.row, creative_id="ad-2", impressions="900", clicks="9", conversions="0")
        self.feedback.import_data(**self.files([self.row, second]))
        report = self.feedback.report_data(self.state)
        result = report["results"][0]
        self.assertEqual(result["totals"]["impressions"], "1000")
        self.assertEqual(result["metrics"]["ctr"]["value"], "0.019")
        self.assertEqual(result["sample_assessment"]["status"], "insufficient")
        self.assertEqual(result["variant"]["hypothesis"], self.registry["variants"][0]["hypothesis"])
        self.assertEqual(result["cohort"]["timezone"], "+08:00")
        self.assertEqual(result["cohort"]["date_start"], "2026-10-20")
        self.assertEqual(len(result["provenance"]), 2)
        self.assertEqual(report["unmeasured_variants"][0]["id"], "variant-2")

    def test_exact_import_is_idempotent_and_keeps_sha256(self):
        arguments = self.files([self.row])
        first = self.feedback.import_data(**arguments)
        before = self.state.read_bytes()
        second = self.feedback.import_data(**arguments)
        self.assertEqual(second["status"], "already_imported")
        self.assertEqual(self.state.read_bytes(), before)
        stored = json.loads(before)
        self.assertEqual(len(stored["observations"]), 1)
        self.assertEqual(len(stored["imports"][0]["csv_sha256"]), 64)
        self.assertEqual(first["import_id"], second["import_id"])

    def test_duplicate_or_changed_rows_require_explicit_upsert(self):
        self.feedback.import_data(**self.files([self.row]))
        before = self.state.read_bytes()
        changed = dict(self.row, conversions="4")
        args = self.files([changed], name="corrected")
        with self.assertRaisesRegex(ValueError, "upsert"):
            self.feedback.import_data(**args)
        self.assertEqual(self.state.read_bytes(), before)
        with self.assertRaisesRegex(ValueError, "reason"):
            self.feedback.import_data(**args, operation="upsert")
        self.feedback.import_data(**args, operation="upsert", reason="后台归因晚到，更新同一日期的累计数据")
        stored = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(len(stored["observations"]), 1)
        self.assertEqual(stored["imports"][-1]["replaced_count"], 1)
        self.assertEqual(len(stored["revisions"]), 1)
        self.assertEqual(self.feedback.report_data(self.state)["results"][0]["totals"]["conversions"], "4")

    def test_duplicate_mapping_and_duplicate_rows_are_rejected_atomically(self):
        mapping = copy.deepcopy(self.mapping)
        mapping["creative_map"].append(dict(mapping["creative_map"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.feedback.import_data(**self.files([self.row], mapping=mapping))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.feedback.import_data(**self.files([self.row, self.row]))
        self.assertFalse(self.state.exists())

    def test_unknown_creative_and_variant_ids_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown creative"):
            self.feedback.import_data(**self.files([dict(self.row, creative_id="unknown")]))
        mapping = copy.deepcopy(self.mapping)
        mapping["creative_map"][0]["variant_id"] = "missing"
        with self.assertRaisesRegex(ValueError, "unknown variant"):
            self.feedback.import_data(**self.files([self.row], mapping=mapping))

    def test_bad_numbers_and_ambiguous_dates_are_rejected(self):
        for value in ("NaN", "inf", "-1", "1.5", "1,000", "=1+1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.feedback.import_data(**self.files([dict(self.row, impressions=value)]))
        for value in ("10/11/2026", "2026-1-2", "2026-02-30"):
            with self.subTest(date=value), self.assertRaisesRegex(ValueError, "date"):
                self.feedback.import_data(**self.files([dict(self.row, date=value)]))
        self.assertFalse(self.state.exists())

    def test_missing_or_zero_denominator_remains_unknown(self):
        for denominator in ("0", ""):
            with self.subTest(denominator=denominator):
                state = self.root / ("state-" + (denominator or "blank") + ".json")
                args = self.files([dict(self.row, impressions=denominator, clicks="0", conversions="0")])
                args["state"] = state
                self.feedback.import_data(**args)
                value = self.feedback.report_data(state)["results"][0]["metrics"]["ctr"]["value"]
                self.assertIsNone(value)

    def test_one_missing_value_does_not_make_partial_sum_look_complete(self):
        self.feedback.import_data(**self.files([self.row, dict(self.row, creative_id="ad-2", impressions="")]))
        result = self.feedback.report_data(self.state)["results"][0]
        self.assertIsNone(result["totals"]["impressions"])
        self.assertIsNone(result["metrics"]["ctr"]["value"])

    def test_cohorts_currency_goal_context_and_dates_stay_separate(self):
        rows = [self.row]
        for field, value in (("currency", "USD"), ("goal", "live-entry"), ("price", "79"), ("entry", "live-room"), ("placement", "search"), ("date", "2026-10-21"), ("attribution_window", "click_1d")):
            rows.append(dict(self.row, **{field: value}))
        self.feedback.import_data(**self.files(rows))
        self.assertEqual(len(self.feedback.report_data(self.state)["results"]), 8)

    def test_sample_assessment_requires_explicit_criteria(self):
        mapping = copy.deepcopy(self.mapping)
        mapping.pop("sample_criteria")
        self.feedback.import_data(**self.files([self.row], mapping=mapping))
        result = self.feedback.report_data(self.state)["results"][0]
        self.assertEqual(result["sample_assessment"]["status"], "unassessed")
        self.assertEqual({check["stage"] for check in result["next_checks"]}, {"attention", "click", "conversion"})
        self.assertFalse(result["causal_claim"])

    def test_configured_sufficiency_is_not_causal_validation(self):
        mapping = copy.deepcopy(self.mapping)
        mapping["sample_criteria"] = {"impressions": 100}
        self.feedback.import_data(**self.files([self.row], mapping=mapping))
        result = self.feedback.report_data(self.state)["results"][0]
        self.assertEqual(result["sample_assessment"]["status"], "sufficient")
        self.assertIn("因果", result["sample_assessment"]["note"])
        self.assertFalse(result["causal_claim"])

    def test_overlapping_date_ranges_rejected(self):
        self.mapping["columns"]["date_end"] = "date_end"
        self.feedback.import_data(**self.files([dict(self.row, date_end="2026-10-25")]))
        with self.assertRaisesRegex(ValueError, "overlap"):
            self.feedback.import_data(**self.files([dict(self.row, date="2026-10-22", date_end="2026-10-26")], name="overlap"))

    def test_html_escapes_text_csv_defuses_formulas_and_refuses_collision(self):
        self.registry["variants"][0]["hypothesis"] = "<script>alert(1)</script>"
        self.registry["variants"][0]["changed_variable"] = "=HYPERLINK(\"https://invalid\")"
        self.feedback.import_data(**self.files([self.row]))
        output = self.root / "report"
        self.feedback.write_report(self.state, output)
        html = (output / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("合成演示", html)
        with (output / "results.csv").open(encoding="utf-8-sig", newline="") as handle:
            row = next(csv.DictReader(handle))
        self.assertTrue(row["changed_variable"].startswith("'="))
        with self.assertRaisesRegex(ValueError, "empty|exists"):
            self.feedback.write_report(self.state, output)

    def test_init_and_cli_demo_are_local_and_reproducible(self):
        output = self.root / "workspace"
        self.feedback.init_workspace(output)
        self.assertTrue((output / "feedback_mapping.json").exists())
        self.assertEqual(json.loads((output / "feedback_state.json").read_text(encoding="utf-8"))["observations"], [])
        with self.assertRaises(ValueError):
            self.feedback.init_workspace(output)
        demo = self.root / "demo"
        process = subprocess.run([sys.executable, str(SCRIPT), "demo", "--output", str(demo)], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(process.returncode, 0, process.stderr)
        data = json.loads((demo / "report/report.json").read_text(encoding="utf-8"))
        self.assertTrue(data["synthetic"])
        self.assertEqual({item["variant"]["id"] for item in data["results"]}, {"variant-explore-01", "variant-iterate-01"})

    def test_metric_definition_must_reference_numeric_fields(self):
        self.mapping["metrics"]["ctr"]["numerator"] = "rate_column"
        with self.assertRaisesRegex(ValueError, "numeric field"):
            self.feedback.import_data(**self.files([self.row]))

    def test_state_lock_prevents_lost_updates(self):
        with self.feedback.state_lock(self.state):
            with self.assertRaisesRegex(ValueError, "locked"):
                self.feedback.import_data(**self.files([self.row]))
        self.assertFalse(self.state.exists())

    def test_process_death_releases_state_lock_without_deleting_lock_file(self):
        command = (
            "import importlib.util,sys; from pathlib import Path; "
            "spec=importlib.util.spec_from_file_location('feedback',sys.argv[1]); "
            "module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); "
            "lock=module.state_lock(Path(sys.argv[2])); lock.__enter__(); "
            "print('locked',flush=True); sys.stdin.read(1)"
        )
        process = subprocess.Popen([sys.executable, "-u", "-c", command, str(SCRIPT), str(self.state)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        try:
            self.assertEqual(process.stdout.readline().strip(), "locked")
            with self.assertRaisesRegex(ValueError, "locked"):
                self.feedback.import_data(**self.files([self.row]))
            process.kill()
            process.wait(timeout=10)
            self.assertTrue(self.state.with_name(self.state.name + ".lock").exists())
            self.assertEqual(self.feedback.import_data(**self.files([self.row]))["status"], "imported")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()

    def test_synthetic_registry_cannot_be_imported_as_real(self):
        self.registry["synthetic"] = True
        self.mapping["synthetic"] = False
        with self.assertRaisesRegex(ValueError, "synthetic"):
            self.feedback.import_data(**self.files([self.row]))
        self.assertFalse(self.state.exists())

    def test_csv_export_defuses_untrusted_cohort_headers(self):
        self.mapping["cohort_fields"].append("=formula")
        self.mapping["columns"]["=formula"] = "extra"
        self.feedback.import_data(**self.files([dict(self.row, extra="context")]))
        output = self.root / "export"
        self.feedback.write_report(self.state, output)
        with (output / "results.csv").open(encoding="utf-8-sig", newline="") as handle:
            header = next(csv.reader(handle))
        self.assertIn("'=formula", header)
        self.assertNotIn("=formula", header)

    def test_semantic_mapping_correction_needs_upsert_and_replaces_old_result(self):
        self.feedback.import_data(**self.files([self.row]))
        mapping = copy.deepcopy(self.mapping)
        mapping["metrics"]["cvr"]["denominator"] = "impressions"
        args = self.files([self.row], mapping=mapping)
        with self.assertRaisesRegex(ValueError, "upsert"):
            self.feedback.import_data(**args)
        self.feedback.import_data(**args, operation="upsert", reason="纠正原来的指标分母映射")
        report = self.feedback.report_data(self.state)
        self.assertEqual(len(report["results"]), 1)
        self.assertEqual(report["results"][0]["metrics"]["cvr"]["value"], "0.02")

    def test_reserved_or_undeclared_columns_cannot_override_validated_dimensions(self):
        for key, value in (("timezone", "wrong-zone"), ("date_start", "2030-99-99"), ("unlisted_context", "missing-from-cohort-list")):
            mapping = copy.deepcopy(self.mapping)
            mapping["columns"][key] = "custom"
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "column|reserved"):
                self.feedback.import_data(**self.files([dict(self.row, custom=value)], mapping=mapping))
        self.assertFalse(self.state.exists())

    def test_registry_rejects_cross_product_parent_cycles_and_missing_iteration_parent(self):
        cross_product = copy.deepcopy(self.registry)
        cross_product["variants"][1].update(product_id="other-product", mode="iteration", parent_variant_id="variant-1")
        cycle = copy.deepcopy(self.registry)
        cycle["variants"][0]["parent_variant_id"] = "variant-2"
        cycle["variants"][1]["parent_variant_id"] = "variant-1"
        missing = copy.deepcopy(self.registry)
        missing["variants"][1].update(mode="iteration", parent_variant_id=None)
        absent = copy.deepcopy(self.registry)
        absent["variants"][1].pop("parent_variant_id")
        for registry in (cross_product, cycle, missing, absent):
            with self.subTest(registry=registry), self.assertRaisesRegex(ValueError, "parent|cycle|product"):
                self.feedback.import_data(**self.files([self.row], registry=registry))
        self.assertFalse(self.state.exists())

    def test_explicit_upsert_can_restore_an_earlier_import_and_then_is_idempotent(self):
        original = self.files([self.row], name="original")
        first = self.feedback.import_data(**original)
        corrected = self.files([dict(self.row, conversions="4")], name="corrected")
        self.feedback.import_data(**corrected, operation="upsert", reason="late conversions")
        reverted = self.feedback.import_data(**original, operation="upsert", reason="revert incorrect late attribution")
        self.assertEqual(reverted["status"], "imported")
        self.assertNotEqual(reverted["import_id"], first["import_id"])
        self.assertEqual(self.feedback.report_data(self.state)["results"][0]["totals"]["conversions"], "2")
        before = self.state.read_bytes()
        repeated = self.feedback.import_data(**original, operation="upsert", reason="same requested correction")
        self.assertEqual(repeated["status"], "already_imported")
        self.assertEqual(self.state.read_bytes(), before)
        state = json.loads(before)
        self.assertEqual(len(state["revisions"]), 2)
        self.assertEqual(state["observations"][0]["provenance"]["import_id"], reverted["import_id"])

    def test_same_platform_account_creative_tuple_maps_without_cross_platform_collision(self):
        self.mapping["creative_map"].append({"platform": "platform-2", "account": "account-1", "creative_id": "ad-1", "variant_id": "variant-2"})
        self.feedback.import_data(**self.files([self.row, dict(self.row, platform="platform-2")]))
        results = self.feedback.report_data(self.state)["results"]
        self.assertEqual({(row["cohort"]["platform"], row["variant"]["id"]) for row in results}, {("platform-1", "variant-1"), ("platform-2", "variant-2")})
        self.assertNotEqual(results[0]["comparison_group"], results[1]["comparison_group"])

    def test_platform_dimension_and_mapping_identity_are_required(self):
        for source in ("columns", "creative_map"):
            mapping = copy.deepcopy(self.mapping)
            if source == "columns":
                mapping["columns"].pop("platform")
            else:
                mapping["creative_map"][0].pop("platform")
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, "platform"):
                self.feedback.import_data(**self.files([self.row], mapping=mapping))

    def creative_registry(self):
        return {**copy.deepcopy(self.registry), "revision": 1, "products": [{"id": "product-1", "name": "Synthetic product"}],
                "cards": {"topic": [], "script": [{"id": "script-1", "variant_id": "variant-1", "blocks": [{"id": "block-1", "voiceover": "Original script", "fact_ids": ["fact-1"]}]}],
                          "shooting": [{"id": "shoot-1", "variant_id": "variant-1", "shots": [{"action": "Open lid"}]}],
                          "test": [{"id": "test-1", "variant_id": "variant-1", "metric_definition": "clicks / impressions"}]},
                "facts": [{"id": "fact-1", "product_id": "product-1", "statement": "Original fact", "source": {"kind": "local_file", "path": "facts.txt", "sha256": "a" * 64}}]}

    def test_creative_content_change_requires_new_variant_id_even_with_upsert(self):
        registry = self.creative_registry()
        self.feedback.import_data(**self.files([self.row], registry=registry))
        before = self.state.read_bytes()
        mutations = (
            lambda value: value["cards"]["script"][0]["blocks"][0].update(voiceover="Different script"),
            lambda value: value["cards"]["shooting"][0]["shots"][0].update(action="Different action"),
            lambda value: value["cards"]["test"][0].update(metric_definition="conversions / impressions"),
            lambda value: value["facts"][0].update(statement="Different product fact"),
        )
        for mutate in mutations:
            changed = copy.deepcopy(registry)
            mutate(changed)
            with self.subTest(mutation=mutate), self.assertRaisesRegex(ValueError, "new variant|new.*id"):
                self.feedback.import_data(**self.files([dict(self.row, date="2026-10-21")], registry=changed), operation="upsert", reason="same identifier but different content")
        self.assertEqual(self.state.read_bytes(), before)
        with self.assertRaisesRegex(ValueError, "new variant|new.*id"):
            self.feedback.import_data(**self.files([dict(self.row, date="2026-10-21")]), operation="upsert", reason="replace creative bundle with standalone registry")

    def test_bundle_revision_and_file_relocation_do_not_change_creative_identity(self):
        registry = self.creative_registry()
        self.feedback.import_data(**self.files([self.row], registry=registry))
        registry["revision"] = 2
        registry["facts"][0]["source"]["path"] = "somewhere-else/facts.txt"
        registry["facts"].append({"id": "unused-fact", "product_id": "product-1", "statement": "Unrelated fact"})
        self.feedback.import_data(**self.files([dict(self.row, date="2026-10-21")], name="next-day", registry=registry))
        state = json.loads(self.state.read_text(encoding="utf-8"))
        bindings = [row["variant"].get("content_binding", {}) for row in state["observations"]]
        self.assertEqual(bindings[0].get("kind"), "creative_bundle")
        self.assertEqual(len(bindings[0].get("sha256", "")), 64)
        self.assertEqual(bindings[0], bindings[1])

    def test_changed_creative_with_new_variant_and_parent_is_importable(self):
        registry = self.creative_registry()
        self.feedback.import_data(**self.files([self.row], registry=registry))
        variant = dict(registry["variants"][0], id="variant-3", mode="iteration", parent_variant_id="variant-1")
        registry["variants"].append(variant)
        for kind in ("script", "shooting", "test"):
            card = copy.deepcopy(registry["cards"][kind][0])
            card.update(id=card["id"] + "-new", variant_id="variant-3")
            if kind == "script":
                card["blocks"][0].update(id="block-new", voiceover="Changed script under a new variant")
            registry["cards"][kind].append(card)
        self.mapping["creative_map"].append({"platform": "platform-1", "account": "account-1", "creative_id": "ad-4", "variant_id": "variant-3"})
        self.feedback.import_data(**self.files([dict(self.row, creative_id="ad-4")], name="new-variant", registry=registry))
        results = self.feedback.report_data(self.state)["results"]
        self.assertEqual({row["variant"]["id"] for row in results}, {"variant-1", "variant-3"})


if __name__ == "__main__":
    unittest.main()
