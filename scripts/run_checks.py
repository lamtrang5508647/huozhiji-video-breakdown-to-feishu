#!/usr/bin/env python3
"""Run portable regression tests, with an optional native-decoder evidence check."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from versioning import check as check_version

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skill/analyze-folder-videos-to-feishu/scripts"


def file_suite(path: Path) -> unittest.TestSuite:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    return unittest.defaultTestLoader.loadTestsFromModule(module)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--media", action="store_true", help="Also require PyAV/Pillow and run actual PTS decoder regression")
    parser.add_argument("--output", type=Path, help="Optional local JSON test receipt")
    args = parser.parse_args()
    try:
        release = check_version(ROOT)
    except (ValueError, OSError) as exc:
        receipt = {"status": "FAIL", "stage": "version_consistency", "error": str(exc)}
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        return 1
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(SCRIPTS))
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_*.py")
    for relative in ("tests/check_section_content.py", "tests/check_material_gallery.py",
                     "skill/analyze-folder-videos-to-feishu/scripts/test_render_platform_chapter.py"):
        suite.addTests(file_suite(ROOT / relative))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    media = "not_requested"
    if args.media:
        command = subprocess.run([sys.executable, "-B", str(ROOT / "tests/check_interval_evidence.py")], cwd=ROOT)
        media = "PASS" if command.returncode == 0 else "FAIL"
    passed = result.wasSuccessful() and media != "FAIL"
    receipt = {
        "release": release,
        "status": "PASS" if passed else "FAIL", "tests_run": result.testsRun,
        "failures": len(result.failures), "errors": len(result.errors),
        "skipped": [{"test": str(test), "reason": reason} for test, reason in result.skipped],
        "pts_decoder_regression": media,
        "scope": "Local program contracts and synthetic media only; source semantics, remote Feishu access and platform playback are not validated.",
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
