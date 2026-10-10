"""Release identity must survive packaging, and invalid bumps cannot split it."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import versioning
spec = importlib.util.spec_from_file_location("version_project", ROOT / "scripts/project.py")
project = importlib.util.module_from_spec(spec)
spec.loader.exec_module(project)


class VersioningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.release = {"schema_version": 1, "version": "1.2.3", "iteration": 4, "released_on": "2026-10-10"}
        for path in versioning.version_paths(self.root):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(self.release), encoding="utf-8")
        (self.root / "README.md").write_text("# fixture\n\n" + versioning.readme_block(self.release) + "\nkeep README\n", encoding="utf-8")
        (self.root / "CHANGELOG.md").write_text("# 更新日志\n\n" + versioning.heading(self.release) + "\n\nkeep old notes\n", encoding="utf-8")

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

    def test_patch_minor_major_keep_history_and_update_every_identity(self):
        for part, expected, iteration in (("patch", "1.2.4", 5), ("minor", "1.3.0", 6), ("major", "2.0.0", 7)):
            with self.subTest(part=part):
                release = versioning.bump(self.root, part, "2026-10-11", "- 修复真实问题")
                self.assertEqual(release["version"], expected)
                self.assertEqual(release["iteration"], iteration)
                self.assertEqual(versioning.check(self.root, "v" + expected), release)
                self.assertIn("keep old notes", (self.root / "CHANGELOG.md").read_text(encoding="utf-8"))
                self.assertIn("keep README", (self.root / "README.md").read_text(encoding="utf-8"))

    def test_mismatched_copies_readme_changelog_or_tag_are_rejected(self):
        cases = [("skill/video-link-extractor/VERSION.json", json.dumps(dict(self.release, iteration=5))),
                 ("README.md", "stale README"), ("CHANGELOG.md", "# no version")]
        for relative, value in cases:
            path = self.root / relative
            original = path.read_bytes()
            with self.subTest(relative=relative):
                path.write_text(value, encoding="utf-8")
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    versioning.bump(self.root, "patch", "2026-10-11", "- fixes")
                self.assertEqual(self.snapshot(), before)
            path.write_bytes(original)
        with self.assertRaisesRegex(ValueError, "Tag"):
            versioning.check(self.root, "v9.9.9")

    def test_invalid_metadata_and_notes_cannot_change_release(self):
        for part, day, notes in (("patch", "2026-10-09", "fix"), ("patch", "2026-02-30", "fix"),
                                 ("patch", "2026-10-11", ""), ("patch", "2026-10-11", "## forged release"),
                                 ("unknown", "2026-10-11", "fix")):
            with self.subTest(part=part, day=day, notes=notes):
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    versioning.bump(self.root, part, day, notes)
                self.assertEqual(self.snapshot(), before)
        path = self.root / "VERSION.json"
        for changes in ({"version": "01.2.3"}, {"version": "1.2"}, {"iteration": True}, {"iteration": 0},
                        {"schema_version": True}, {"released_on": "20261010"}, {"extra": "secret"}):
            with self.subTest(changes=changes):
                path.write_text(json.dumps(dict(self.release, **changes)), encoding="utf-8")
                with self.assertRaises(ValueError):
                    versioning.read_release(self.root)

    def test_write_failure_rolls_back_all_changed_files(self):
        original_write = Path.write_text
        def failing_write(path, *args, **kwargs):
            if path.name == "README.md":
                raise OSError("simulated write failure")
            return original_write(path, *args, **kwargs)
        before = self.snapshot()
        with patch.object(Path, "write_text", failing_write), self.assertRaisesRegex(OSError, "simulated"):
            versioning.bump(self.root, "patch", "2026-10-11", "- fixes")
        self.assertEqual(self.snapshot(), before)

    def test_persistent_readonly_failure_does_not_interrupt_other_rollbacks(self):
        original_write = Path.write_text
        original_bytes = Path.write_bytes
        def readonly_text(path, *args, **kwargs):
            if path.name == "README.md":
                raise PermissionError("read-only README")
            return original_write(path, *args, **kwargs)
        def readonly_bytes(path, *args, **kwargs):
            if path.name == "README.md":
                raise PermissionError("read-only README")
            return original_bytes(path, *args, **kwargs)
        before = self.snapshot()
        with patch.object(Path, "write_text", readonly_text), patch.object(Path, "write_bytes", readonly_bytes), self.assertRaises(PermissionError):
            versioning.bump(self.root, "patch", "2026-10-11", "- fixes")
        self.assertEqual(self.snapshot(), before)

    def test_installer_rejects_mismatched_or_missing_version_before_copying(self):
        source = self.root / "source"
        source.mkdir()
        (source / "SKILL.md").write_text("fixture", encoding="utf-8")
        args = argparse.Namespace(skill="video-link-extractor", dest=self.root / "installed")
        with patch.object(project, "ACQUIRE_SKILL", source):
            with self.assertRaises(OSError):
                project.install_skill(args)
            self.assertFalse(args.dest.exists())
            mismatched = dict(versioning.read_release(ROOT), iteration=999999)
            (source / "VERSION.json").write_text(json.dumps(mismatched), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "version mismatch"):
                project.install_skill(args)
            self.assertFalse(args.dest.exists())

    def test_installed_skill_keeps_standalone_identity_and_cli_reports_it(self):
        release = versioning.read_release(ROOT)
        result = subprocess.run([sys.executable, "-B", ROOT / "scripts/project.py", "version"], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["release"], release)
        short = subprocess.run([sys.executable, "-B", ROOT / "scripts/project.py", "--version"], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(short.returncode, 0, short.stderr)
        self.assertIn("v" + release["version"], short.stdout)
        self.assertIn(f"iteration {release['iteration']:03d}", short.stdout)
        for name in versioning.SKILLS:
            args = argparse.Namespace(skill=name, dest=self.root / name)
            receipt = project.install_skill(args)
            self.assertEqual(receipt["release"], release)
            self.assertEqual(versioning.read_release(args.dest), release)

    def test_version_cli_mismatch_is_nonzero_and_never_publishes(self):
        result = subprocess.run([sys.executable, "-B", ROOT / "scripts/versioning.py", "check", "--tag", "wrong"], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stderr)["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
