"""Custom chapter registries must work and must stay inside the project."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "skill/analyze-folder-videos-to-feishu/scripts"
sys.path.insert(0, str(SCRIPTS))
from project_paths import registry_path
import publish_platform_chapter as publish
import repair_platform_chapter as repair
import render_platform_chapter as renderer


class ReachedRemoteBoundary(Exception):
    pass


class RegistryPathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        self.addCleanup(self.temp.cleanup)

    def test_default_and_custom_paths(self):
        self.assertEqual(registry_path(self.root, {}), (self.root / "state/platform-document-registry.json").resolve())
        self.assertEqual(registry_path(self.root, {"registry_path": "custom/chapter-map.json"}), (self.root / "custom/chapter-map.json").resolve())

    def test_escape_empty_and_root_are_rejected(self):
        outside = self.root.parent / "outside.json"
        for value in ("../outside.json", str(outside), "", None, "."):
            with self.subTest(value=value), self.assertRaises(ValueError):
                registry_path(self.root, {"registry_path": value})

    def test_symlink_escape_is_rejected(self):
        outside = self.root.parent / "external"
        outside.mkdir()
        try:
            (self.root / "linked").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Host does not permit directory symlink creation")
        with self.assertRaisesRegex(ValueError, "escapes"):
            registry_path(self.root, {"registry_path": "linked/registry.json"})

    def test_both_chapter_entrypoints_use_custom_registry_before_remote_calls(self):
        def save(relative, value):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value), encoding="utf-8")
        save("config/project.json", {"registry_path": "custom/chapters.json", "base_id": "fixture-base", "identity": "bot"})
        save("custom/chapters.json", {"base_id": "fixture-base", "groups": {"fixture-group": {
            "doc_token": "fixture-doc", "videos": {"sample": {}}}}})
        save("job/analysis.json", {})
        save("job/script_manifest.json", {"video_id": "sample", "title": "Sample"})
        save("job/sections-content.json", {})
        layout = self.root / "job/chapter.xml"
        layout.write_text("<h1>Sample</h1>", encoding="utf-8")
        common = ["--project", str(self.root), "--workspace", str(self.root), "--job", str(self.root / "job"),
                  "--layout", str(layout), "--materials-url", "https://example.invalid/materials", "--segments-url", "https://example.invalid/segments"]
        for module in (publish, repair):
            argv = [module.__file__, *common]
            if module is publish:
                argv.extend(["--group-key", "fixture-group"])
            # Stop at client construction: all config/registry reads are real,
            # while no executable lookup, authentication, or network call occurs.
            with self.subTest(module=module.__name__), patch.object(sys, "argv", argv), \
                    patch.object(publish, "validate_layout", return_value={}), \
                    patch.object(renderer, "validate_layout", return_value={}), \
                    patch.object(module, "Lark", side_effect=ReachedRemoteBoundary), \
                    self.assertRaises(ReachedRemoteBoundary):
                module.main()


if __name__ == "__main__":
    unittest.main()
