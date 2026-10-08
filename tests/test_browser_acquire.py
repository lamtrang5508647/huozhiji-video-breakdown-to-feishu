"""Synthetic identity-bound acquisition regressions; no platform/browser access."""
import pathlib
import shutil
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILL = ROOT / "skill" / "video-link-extractor"
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "Node.js unavailable; synthetic browser-provider checks skipped")
class BrowserAcquireSecurityTests(unittest.TestCase):
    def test_exact_identity_and_metadata_download_boundary(self):
        result = subprocess.run(
            [NODE, "--test", "test/security.test.mjs"],
            cwd=SKILL / "vendor" / "video-batch-download",
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_help_does_not_require_playwright_or_browser(self):
        result = subprocess.run(
            [NODE, str(SKILL / "scripts" / "browser_acquire.mjs"), "--help"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--metadata-only|--download", result.stdout)


if __name__ == "__main__":
    unittest.main()
