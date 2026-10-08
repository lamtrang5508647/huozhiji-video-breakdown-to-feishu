"""No network/model inference: verify the ASR launch and evidence boundaries."""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skill" / "analyze-folder-videos-to-feishu" / "scripts"))
import offline_asr  # noqa: E402


class OfflineAsrTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.audio = self.root / "audio.wav"
        self.audio.write_bytes(b"fixture; no inference is run in these tests")
        self.output = self.root / "asr"
        self.cache = self.root / "cache"

    def tearDown(self):
        self.temp.cleanup()

    def model(self, name="small.pt", directory=None):
        destination = (directory or self.cache / "whisper") / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"test checkpoint placeholder")
        return destination

    def invoke(self, *options, runner=None, engine="openai-whisper"):
        argv = ["offline_asr.py", str(self.audio), "--engine", engine,
                "--whisper", "fixture-whisper", "--whisper-cpp", "fixture-cpp",
                "--output-dir", str(self.output), *options]
        stream = io.StringIO()
        with patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.cache)}), \
                patch.object(sys, "argv", argv), \
                patch.object(offline_asr, "run", side_effect=runner) as command, \
                contextlib.redirect_stdout(stream):
            code = offline_asr.main()
        return code, json.loads(stream.getvalue()), command

    def write_openai_result(self, command):
        destination = Path(command[command.index("--output_dir") + 1]) / "audio.json"
        destination.write_text(json.dumps({"text": "源音频草稿", "language": "zh",
                                          "segments": [{"start": 0, "end": 1, "text": "源音频草稿"}]}),
                               encoding="utf-8")

    def test_missing_default_model_blocks_launch_and_transcript(self):
        code, result, command = self.invoke()
        self.assertEqual(code, 1)
        self.assertFalse(result["ok"])
        self.assertIn("No download attempted", result["error"])
        command.assert_not_called()
        self.assertFalse((self.output / "transcript.json").exists())

    def test_cached_model_is_passed_as_absolute_file_not_downloadable_name(self):
        model = self.model()
        code, result, command = self.invoke(runner=self.write_openai_result)
        self.assertEqual(code, 0)
        argv = command.call_args.args[0]
        self.assertEqual(argv[argv.index("--model") + 1], str(model.resolve()))
        self.assertEqual(result["transcript_status"], "asr_draft")
        self.assertFalse(result["human_verified"])
        self.assertEqual(json.loads((self.output / "transcript.json").read_text(encoding="utf-8"))["text"], "源音频草稿")

    def test_custom_checkpoint_is_accepted_without_cached_model(self):
        model = self.model("custom.pt", self.root / "models with spaces")
        code, _, command = self.invoke("--model", str(model), runner=self.write_openai_result)
        self.assertEqual(code, 0)
        argv = command.call_args.args[0]
        self.assertEqual(argv[argv.index("--model") + 1], str(model.resolve()))

    def test_explicit_cache_directory_and_alias(self):
        model = self.model("large-v3-turbo.pt", self.root / "other cache")
        code, _, command = self.invoke("--model", "turbo", "--model-dir", str(model.parent), runner=self.write_openai_result)
        self.assertEqual(code, 0)
        argv = command.call_args.args[0]
        self.assertEqual(argv[argv.index("--model") + 1], str(model.resolve()))

    def test_named_model_download_requires_explicit_opt_in(self):
        code, _, command = self.invoke("--allow-download", "--model", "small", runner=self.write_openai_result)
        self.assertEqual(code, 0)
        argv = command.call_args.args[0]
        self.assertEqual(argv[argv.index("--model") + 1], "small")

    def test_empty_cached_checkpoint_blocks_launch(self):
        model = self.model()
        model.write_bytes(b"")
        code, result, command = self.invoke()
        self.assertEqual(code, 1)
        self.assertIn("No download attempted", result["error"])
        command.assert_not_called()

    def test_missing_explicit_path_is_not_treated_as_downloadable_model(self):
        code, _, command = self.invoke("--model", str(self.root / "missing.pt"), "--allow-download")
        self.assertEqual(code, 1)
        command.assert_not_called()

    def test_success_exit_without_output_cannot_reuse_stale_raw_json(self):
        self.model()
        self.output.mkdir()
        stale = self.output / "audio.json"
        stale.write_text('{"text":"old transcript"}', encoding="utf-8")
        code, result, command = self.invoke()
        self.assertEqual(code, 1)
        self.assertFalse(result["ok"])
        command.assert_called_once()
        self.assertFalse((self.output / "transcript.json").exists())
        self.assertEqual(json.loads(stale.read_text())["text"], "old transcript")

    def test_null_text_cannot_become_a_literal_none_transcript(self):
        self.model()
        def empty_result(command):
            destination = Path(command[command.index("--output_dir") + 1]) / "audio.json"
            destination.write_text('{"text":null,"segments":[{"text":null}]}', encoding="utf-8")
        code, result, _ = self.invoke(runner=empty_result)
        self.assertEqual(code, 1)
        self.assertIn("no transcript text", result["error"])
        self.assertFalse((self.output / "transcript.json").exists())

    def test_cpp_requires_existing_local_model_even_with_allow_download(self):
        code, _, command = self.invoke("--allow-download", engine="whisper-cpp")
        self.assertEqual(code, 1)
        command.assert_not_called()

    def test_cpp_preserves_timestamp_normalization(self):
        model = self.model("ggml-model.bin")
        def cpp_result(command):
            prefix = Path(command[command.index("-of") + 1])
            prefix.with_suffix(".json").write_text(json.dumps({"transcription": [
                {"text": "样例", "timestamps": {"from": "00:00:01,250", "to": "00:00:02,500"}}
            ]}), encoding="utf-8")
        code, result, _ = self.invoke("--model", str(model), engine="whisper-cpp", runner=cpp_result)
        self.assertEqual(code, 0)
        self.assertEqual(result["segments"], [{"start": 1.25, "end": 2.5, "text": "样例"}])
        self.assertFalse(result["human_verified"])


if __name__ == "__main__":
    unittest.main()
