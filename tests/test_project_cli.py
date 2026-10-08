"""Portable project initialization, overwrite guards, and real synthetic exports."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("project_cli", ROOT / "scripts/project.py")
project = importlib.util.module_from_spec(spec)
spec.loader.exec_module(project)


class ProjectCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def args(self, destination=None, **changes):
        values = dict(project=destination or self.root / "new-project", feishu_config=None,
                      base_url=None, app_id=None, identity=None)
        values.update(changes)
        return argparse.Namespace(**values)

    def test_local_init_needs_no_cli_and_creates_no_fake_ids(self):
        with patch.object(project.shutil, "which", return_value=None):
            receipt = project.init_project(self.args())
        path = Path(receipt["project"])
        config = json.loads((path / "config/project.json").read_text(encoding="utf-8"))
        registry = json.loads((path / config["registry_path"]).read_text(encoding="utf-8"))
        index = json.loads((path / config["video_index_path"]).read_text(encoding="utf-8"))
        self.assertEqual(config["mode"], "local")
        self.assertIsNone(config["base_id"])
        self.assertEqual(config["tables"], {})
        self.assertEqual(registry["groups"], {})
        self.assertEqual(index["videos"], {})
        self.assertFalse(receipt["remote_access_verified"])

    def test_reinitialization_preserves_user_edits(self):
        project.init_project(self.args())
        file = self.root / "new-project/config/project.json"
        file.write_text("User edited this", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "existing files are preserved"):
            project.init_project(self.args())
        self.assertEqual(file.read_text(encoding="utf-8"), "User edited this")

    def test_explicit_profile_is_copied_without_live_claim(self):
        profile = self.root / "profile.json"
        profile.write_text(json.dumps({"schema_version": 1, "identity": "user", "base_id": "user-supplied-base",
                                      "tables": {"videos": {"id": "user-supplied-table", "fields": {}}}}), encoding="utf-8")
        receipt = project.init_project(self.args(feishu_config=profile))
        config = json.loads((Path(receipt["project"]) / "config/project.json").read_text(encoding="utf-8"))
        self.assertEqual(config["mode"], "feishu")
        self.assertEqual(config["identity"], "user")
        self.assertEqual(config["tables"]["videos"]["id"], "user-supplied-table")
        self.assertFalse(receipt["remote_created"])

    def test_init_rejects_credentials_before_creating_project(self):
        profile = self.root / "profile.json"
        profile.write_text(json.dumps({"nested": {"app_secret": "must-not-copy"}}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Do not store credentials"):
            project.init_project(self.args(feishu_config=profile))
        self.assertFalse((self.root / "new-project").exists())

    def test_init_rejects_state_path_escape_and_collision(self):
        for value in ("../outside.json", "config/project.json", "state/video-index.json"):
            profile = self.root / "profile.json"
            profile.write_text(json.dumps({"registry_path": value}), encoding="utf-8")
            with self.subTest(value=value), self.assertRaises(ValueError):
                project.init_project(self.args(feishu_config=profile))
        self.assertFalse((self.root / "outside.json").exists())

    def test_doctor_base_layer_does_not_require_remote_cli(self):
        args = argparse.Namespace(feature=[], project=None, browser=None)
        with patch.object(project.shutil, "which", return_value=None):
            receipt = project.doctor(args)
        self.assertEqual(receipt["status"], "PASS")
        self.assertFalse(receipt["commands"]["lark-cli"]["available"])
        args.feature = ["feishu"]
        with patch.object(project.shutil, "which", return_value=None):
            self.assertEqual(project.doctor(args)["status"], "MISSING_DEPENDENCIES")

    def test_installer_excludes_local_locator_and_refuses_overwrite(self):
        source = self.root / "source"
        (source / "agents").mkdir(parents=True)
        (source / "__pycache__").mkdir()
        (source / "SKILL.md").write_text("fixture skill", encoding="utf-8")
        (source / "agents/local-project.json").write_text("private locator", encoding="utf-8")
        (source / "__pycache__/cached.pyc").write_bytes(b"cache")
        args = argparse.Namespace(dest=self.root / "installed")
        with patch.object(project, "SKILL", source):
            receipt = project.install_skill(args)
            self.assertEqual(receipt["status"], "installed_and_hash_verified")
            self.assertEqual(receipt["files"], 1)
            self.assertFalse((args.dest / "agents/local-project.json").exists())
            self.assertFalse((args.dest / "__pycache__").exists())
            with self.assertRaisesRegex(ValueError, "already exists"):
                project.install_skill(args)

    def test_each_command_has_help(self):
        for name in ("init", "doctor", "demo", "install-skill"):
            with self.subTest(command=name):
                result = subprocess.run([sys.executable, "-B", ROOT / "scripts/project.py", name, "--help"],
                                        capture_output=True, text=True, encoding="utf-8")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("usage:", result.stdout)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg and FFprobe are needed for real media integration")
    def test_real_synthetic_demo_and_artifact_hashes(self):
        output = self.root / "demo"
        result = subprocess.run([sys.executable, "-B", ROOT / "scripts/project.py", "demo", "--output", output],
                                capture_output=True, text=True, encoding="utf-8", timeout=180)
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads((output / "verification.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(receipt["shot_clips"], 2)
        self.assertEqual(receipt["semantic_clips"], 2)
        self.assertFalse(receipt["remote_access_attempted"])
        self.assertFalse(receipt["human_review_performed"])
        self.assertIn("complete_idempotent_reuse", receipt["repeat_export_result"])
        self.assertTrue((output / "analysis.html").is_file())
        self.assertTrue((output / "chapter.xml").is_file())
        for relative, expected in receipt["artifact_sha256"].items():
            self.assertEqual(hashlib.sha256((output / relative).read_bytes()).hexdigest(), expected, relative)
        before = (output / "verification.json").read_bytes()
        repeat = subprocess.run([sys.executable, "-B", ROOT / "scripts/project.py", "demo", "--output", output],
                                capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertNotEqual(repeat.returncode, 0)
        self.assertEqual((output / "verification.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
