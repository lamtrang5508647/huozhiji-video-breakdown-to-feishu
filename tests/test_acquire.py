"""Source provenance checks and actual media decoding, with no platform/network requests."""
import importlib.util
import base64
import copy
import ctypes
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("link_acquire", ROOT / "skill/video-link-extractor/scripts/acquire.py")
acquire = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acquire)


def wechat_export_fixture():
    url = "https://weixin.qq.com/sph/syntheticShare"
    export_id = "synthetic+/platform-export=="
    canonical = "https://channels.weixin.qq.com/finder-preview/pages/feed?" + urlencode({"eid": export_id, "token": "synthetic"})
    return {"status": "metadata_ready", "platform": "wechat_channels", "input_url": url,
            "canonical_url": canonical, "native_id": export_id, "native_id_kind": "platform_export_id",
            "platform_export_id": export_id,
            "identity_stability_verified": False, "title": "实际来源文案", "title_kind": "caption",
            "identity_verified": True, "media_path": None,
            "identity_evidence": {"kind": "tencent_same_origin_request_chain", "parse_input_url": url,
                                  "parse_playable_url": canonical, "play_eid": export_id,
                                  "feed_export_id": export_id, "dynamic_export_id": export_id,
                                  "parse_endpoint": "https://yuanbao.tencent.com/api/weixin/get_parse_result",
                                  "feed_endpoint": "https://channels.weixin.qq.com/finder-preview/api/feed/get_feed_info",
                                  "play_token_matched": True, "parse_http_status": 200, "feed_http_status": 201,
                                  "parse_code": 0, "feed_err_code": 0, "business_error_type": 0}}


class SourceContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.url = "https://www.douyin.com/video/1234567890123456789"
        self.data = {"status": "metadata_ready", "platform": "douyin", "input_url": self.url,
                     "canonical_url": self.url, "native_id": "1234567890123456789", "title": "真实文案",
                     "title_kind": "caption", "identity_verified": True, "media_path": None}

    def test_routes_full_domains_and_preserves_share_query(self):
        cases = {"https://b23.tv/example": "bilibili", "https://v.douyin.com/example/": "douyin",
                 "https://xhslink.cn/example": "xiaohongshu", "https://v.kuaishou.com/example": "kuaishou",
                 "https://channels.weixin.qq.com/web/pages/feed": "wechat_channels",
                 "https://www.youtube.com/watch?v=example": "generic",
                 "https://mp.weixin.qq.com/s/example": "generic",
                 "https://douyin.com.attacker.invalid/video/123": "generic"}
        for url, platform in cases.items():
            with self.subTest(url=url):
                self.assertEqual(acquire.detect_platform(url), platform)
        shared = "复制打开小红书： https://www.xiaohongshu.com/explore/abc123?xsec_token=fixture_token&xsec_source=pc_share 。"
        self.assertIn("xsec_token=fixture_token&xsec_source=pc_share", acquire.input_url(shared))

    def test_rejects_multiple_links_and_credentials(self):
        for text in ("https://a.invalid/ https://b.invalid/", "http://a.invalid/",
                     "https://user:password@a.invalid/", "https://a.invalid:444/", "no link"):
            with self.subTest(text=text), self.assertRaises(acquire.AcquisitionError):
                acquire.input_url(text)

    def test_exact_id_and_input_provenance_required(self):
        for changes in ({"native_id": "123456789012345678"}, {"input_url": "https://v.douyin.com/other/"},
                        {"identity_verified": False}, {"canonical_url": "https://www.kuaishou.com/short-video/123"},
                        {"native_id": ""}, {"title": ""}, {"title_kind": "filename"}):
            with self.subTest(changes=changes), self.assertRaises(acquire.AcquisitionError):
                acquire.validate_provider({**self.data, **changes}, self.url, "douyin", self.root, False)

    def test_bilibili_part_must_not_change(self):
        url = "https://www.bilibili.com/video/BV1234567890?p=2"
        data = {**self.data, "platform": "bilibili", "input_url": url, "native_id": "BV1234567890",
                "canonical_url": "https://www.bilibili.com/video/BV1234567890"}
        with self.assertRaisesRegex(acquire.AcquisitionError, "page changed"):
            acquire.validate_provider(data, url, "bilibili", self.root, False)

    def test_invalid_bilibili_part_is_rejected_before_provider(self):
        for query in ("p=0", "p=2abc", "p=", "p=2&p=3", "p=-1"):
            args = acquire.parser().parse_args(["--url", "https://www.bilibili.com/video/BV1234567890?" + query,
                                                "--output", str(self.root / "unused")])
            with self.subTest(query=query), patch.object(acquire, "provider") as provider:
                with self.assertRaises(acquire.AcquisitionError):
                    acquire.acquire(args)
                provider.assert_not_called()

    def test_bilibili_short_link_may_resolve_to_actual_second_part(self):
        url = "https://b23.tv/example"
        data = {**self.data, "platform": "bilibili", "input_url": url, "native_id": "BV1234567890",
                "canonical_url": "https://www.bilibili.com/video/BV1234567890?p=2"}
        self.assertEqual(acquire.validate_provider(data, url, "bilibili", self.root, False)["native_id"], "BV1234567890")

    def test_media_path_cannot_escape_output(self):
        with self.assertRaisesRegex(acquire.AcquisitionError, "inside this acquisition"):
            acquire.validate_provider({**self.data, "status": "downloaded", "media_path": str(self.root.parent / "foreign.mp4")},
                                      self.url, "douyin", self.root, True)

    def test_metadata_only_does_not_decode_or_invent_source_file(self):
        output = self.root / "probe"
        args = acquire.parser().parse_args(["--url", self.url, "--output", str(output)])
        with patch.object(acquire, "provider", return_value=self.data), patch.object(acquire, "verify_media") as decode:
            receipt = acquire.acquire(args)
        self.assertEqual(receipt["status"], "metadata_ready")
        self.assertFalse(receipt["content_reviewed"])
        self.assertFalse((output / "source.json").exists())
        decode.assert_not_called()

    def test_failure_keeps_receipt_and_preserves_existing_files(self):
        output = self.root / "failed"
        args = acquire.parser().parse_args(["--url", self.url, "--output", str(output), "--download"])
        with patch.object(acquire, "provider", return_value={**self.data, "status": "blocked", "error_code": "access_required"}):
            with self.assertRaises(acquire.AcquisitionError):
                acquire.acquire(args)
        original = (output / "acquisition.json").read_bytes()
        self.assertEqual(json.loads(original)["error_code"], "access_required")
        with self.assertRaisesRegex(acquire.AcquisitionError, "new or empty"):
            acquire.acquire(args)
        self.assertEqual((output / "acquisition.json").read_bytes(), original)

    def test_known_site_cannot_be_forced_to_generic(self):
        args = acquire.parser().parse_args(["--url", self.url, "--output", str(self.root / "target"), "--platform", "generic"])
        with patch.object(acquire, "provider") as provider, self.assertRaisesRegex(acquire.AcquisitionError, "Explicit platform"):
            acquire.acquire(args)
        provider.assert_not_called()

    def test_non_object_provider_json_is_saved_as_blocked_without_traceback(self):
        for route, url in (("browser", self.url), ("ytdlp", "https://other.invalid/video/1")):
            for index, payload in enumerate((None, [], False, "synthetic-private-value")):
                output = self.root / f"{route}-{index}"
                def provider_result(*unused):
                    if route == "browser":
                        acquire.write_json(output / "provider-result.json", payload)
                    return subprocess.CompletedProcess([], 0, json.dumps(payload), "")
                with self.subTest(route=route, payload=payload), \
                     patch.object(acquire, "run", side_effect=provider_result), \
                     patch.object(acquire, "run_owned_browser", side_effect=provider_result), \
                     patch("sys.stderr", new_callable=io.StringIO) as stderr:
                    code = acquire.main(["--url", url, "--output", str(output)])
                    self.assertEqual(code, 1)
                    receipt = json.loads((output / "acquisition.json").read_text(encoding="utf-8"))
                    self.assertEqual(receipt["status"], "blocked")
                    self.assertEqual(receipt["error_code"], "provider_contract")
                    self.assertFalse(receipt["media_verified"])
                    self.assertFalse((output / "source.json").exists())
                    self.assertNotIn("synthetic-private-value", stderr.getvalue())

    def test_invalid_provider_url_and_media_path_keep_failure_receipts(self):
        cases = ({"canonical_url": None}, {"canonical_url": []},
                 {"status": "downloaded", "media_path": None},
                 {"status": "downloaded", "media_path": []})
        for index, changes in enumerate(cases):
            output = self.root / f"invalid-field-{index}"
            with self.subTest(changes=changes), \
                 patch.object(acquire, "provider", return_value={**self.data, **changes}), \
                 patch("sys.stderr", new_callable=io.StringIO):
                code = acquire.main(["--url", self.url, "--output", str(output), "--download"])
                self.assertEqual(code, 1)
                receipt = json.loads((output / "acquisition.json").read_text(encoding="utf-8"))
                self.assertEqual(receipt["status"], "blocked")
                self.assertFalse((output / "source.json").exists())

    def test_generic_provider_rejects_fallback_and_playlists(self):
        args = acquire.parser().parse_args(["--url", "https://other.invalid/video/1", "--output", str(self.root)])
        for data in ({"id": "invented", "title": "filename", "extractor_key": "Generic"},
                     {"id": "invented", "title": "filename"},
                     {"id": "wrong-id", "title": "title", "extractor_key": "Dedicated", "webpage_url": args.url},
                     {"id": "1", "title": "title", "extractor_key": "Dedicated", "webpage_url": args.url,
                      "original_url": "https://other.invalid/video/2"},
                     {"_type": "playlist", "entries": [{"id": "1"}]}):
            with patch.object(acquire, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(data), "")):
                with self.assertRaises(acquire.AcquisitionError):
                    acquire.ytdlp_provider(args, args.url, self.root)

    def test_dedicated_extractor_source_binding(self):
        url = "https://www.youtube.com/watch?v=source123"
        args = acquire.parser().parse_args(["--url", url, "--output", str(self.root)])
        data = {"id": "source123", "title": "Source title", "extractor_key": "Youtube",
                "webpage_url": url, "original_url": url}
        with patch.object(acquire, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(data), "")):
            result = acquire.ytdlp_provider(args, url, self.root)
        self.assertEqual(result["native_id"], "source123")
        self.assertTrue(result["identity_verified"])


class WechatBrowserContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = wechat_export_fixture()
        self.url = self.data["input_url"]

    def options(self, *flags, url=None):
        return acquire.parser().parse_args(["--url", url or self.url, "--output", str(self.root / "out"), *flags])

    def test_exact_tencent_export_chain_is_accepted_without_numeric_id_claim(self):
        result = acquire.validate_provider(self.data, self.url, "wechat_channels", self.root, False)
        self.assertEqual(result["native_id_kind"], "platform_export_id")
        self.assertEqual(result["native_id"], self.data["native_id"])
        self.assertFalse(result["identity_stability_verified"])

    def test_missing_or_conflicting_chain_cannot_fall_back_to_url_echo(self):
        for key in self.data["identity_evidence"]:
            for mode in ("missing", "different"):
                value = copy.deepcopy(self.data)
                if mode == "missing":
                    del value["identity_evidence"][key]
                else:
                    value["identity_evidence"][key] = "unrelated"
                with self.subTest(key=key, mode=mode), self.assertRaises(acquire.AcquisitionError):
                    acquire.validate_provider(value, self.url, "wechat_channels", self.root, False)
        for changes in ({"identity_evidence": "native_query_match"}, {"identity_evidence": None},
                        {"native_id_kind": "numeric_object_id"}, {"native_id_kind": None},
                        {"identity_stability_verified": True}, {"identity_stability_verified": 0},
                        {"platform_export_id": "unrelated"}, {"platform_export_id": None}):
            with self.subTest(changes=changes), self.assertRaises(acquire.AcquisitionError):
                acquire.validate_provider({**self.data, **changes}, self.url, "wechat_channels", self.root, False)

    def test_tencent_status_values_must_be_actual_typed_successes(self):
        cases = {"parse_http_status": (True, "200", 200.0, 199, 300, None),
                 "feed_http_status": (False, "201", 201.0, 401, None),
                 "parse_code": (False, "0", 0.0, 1, None),
                 "feed_err_code": (False, "0", 0.0, -1, None),
                 "business_error_type": (False, "0", 0.0, 1, None),
                 "play_token_matched": (1, "true", False, None)}
        for key, values in cases.items():
            for bad in values:
                value = copy.deepcopy(self.data)
                value["identity_evidence"][key] = bad
                with self.subTest(key=key, value=bad), self.assertRaises(acquire.AcquisitionError):
                    acquire.validate_provider(value, self.url, "wechat_channels", self.root, False)

    def test_export_canonical_requires_exact_origin_path_and_unique_eid(self):
        canonical = self.data["canonical_url"]
        for url in (canonical.replace("channels.weixin.qq.com", "sub.channels.weixin.qq.com"),
                    canonical.replace("/finder-preview/pages/feed", "/web/pages/feed"),
                    canonical + "&eid=unrelated", canonical + "#fragment",
                    canonical.replace("eid=", "other="), canonical.replace("https://", "https://user@"),
                    canonical + "&token=synthetic", canonical.replace("token=synthetic", "token="),
                    canonical.replace("&token=synthetic", "")):
            value = copy.deepcopy(self.data)
            value["canonical_url"] = url
            value["identity_evidence"]["parse_playable_url"] = url
            with self.subTest(url=url), self.assertRaises(acquire.AcquisitionError):
                acquire.validate_provider(value, self.url, "wechat_channels", self.root, False)
        value = copy.deepcopy(self.data)
        value.update(platform="douyin", input_url="https://www.douyin.com/video/source", canonical_url="https://www.douyin.com/video/source")
        with self.assertRaises(acquire.AcquisitionError):
            acquire.validate_provider(value, value["input_url"], "douyin", self.root, False)

    def test_wechat_browser_options_block_before_any_provider_execution(self):
        cases = ((self.url, ["--wechat-browser"]),
                 (self.url, ["--allow-tencent-parse"]), (self.url, ["--manual-login"]),
                 (self.url, ["--wechat-browser", "--allow-tencent-parse", "--backend-url", "http://127.0.0.1:2022"]),
                 (self.url, ["--wechat-browser", "--allow-tencent-parse", "--online-endpoint", "https://example.invalid/parse"]),
                 (self.url, ["--wechat-browser", "--allow-tencent-parse", "--allow-online-parser"]),
                 ("https://www.douyin.com/video/source", ["--wechat-browser", "--allow-tencent-parse"]),
                 ("https://www.douyin.com/video/source", ["--manual-login"]),
                 ("https://www.douyin.com/video/source", ["--allow-tencent-parse"]))
        for url, flags in cases:
            args = self.options(*flags, url=url)
            with self.subTest(flags=flags, url=url), patch.object(acquire, "provider") as provider:
                with self.assertRaises(acquire.AcquisitionError):
                    acquire.acquire(args)
                provider.assert_not_called()
                self.assertFalse(Path(args.output).exists())

    def test_browser_provider_routes_explicit_flags_and_preserves_exit_failure(self):
        args = self.options("--wechat-browser", "--allow-tencent-parse", "--manual-login", "--headed",
                            "--browser", "/synthetic/browser", "--storage-state", "/synthetic/caller-state.json",
                            "--ffmpeg", "synthetic-ffmpeg")
        acquire.write_json(self.root / "provider-result.json", self.data)
        with patch.object(acquire, "run_owned_browser", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            result = acquire.provider(args, self.url, "wechat_channels", self.root)
        command = run.call_args.args[0]
        self.assertEqual(command[0], "node")
        self.assertEqual(Path(command[1]).name, "wechat_browser_acquire.mjs")
        for flag in ("--allow-tencent-parse", "--manual-login", "--headed", "--metadata-only"):
            self.assertIn(flag, command)
        for flag, expected in (("--browser", args.browser), ("--storage-state", args.storage_state), ("--ffmpeg", args.ffmpeg)):
            self.assertEqual(command[command.index(flag) + 1], expected)
        self.assertEqual(command[command.index("--timeout-ms") + 1], str(args.timeout * 1000))
        self.assertEqual(result["native_id_kind"], "platform_export_id")
        with patch.object(acquire, "run_owned_browser", return_value=subprocess.CompletedProcess([], 2, "", "")):
            with self.assertRaises(acquire.AcquisitionError):
                acquire.provider(args, self.url, "wechat_channels", self.root)

    def test_browser_receipt_cannot_drop_export_kind_to_bypass_new_validation(self):
        args = self.options("--wechat-browser", "--allow-tencent-parse")
        value = {**self.data, "native_id": "1234567890123456789", "canonical_url": self.url}
        value.pop("native_id_kind")
        value.pop("platform_export_id")
        value.pop("identity_evidence")
        acquire.write_json(self.root / "provider-result.json", value)
        with patch.object(acquire, "run_owned_browser", return_value=subprocess.CompletedProcess([], 0, "", "")):
            with self.assertRaises(acquire.AcquisitionError):
                acquire.provider(args, self.url, "wechat_channels", self.root)

    def test_existing_backend_still_uses_python_without_browser_options(self):
        args = self.options("--backend-url", "http://127.0.0.1:2022")
        value = {**self.data, "native_id": "1234567890123456789", "canonical_url": self.url}
        value.pop("native_id_kind")
        value.pop("platform_export_id")
        value.pop("identity_evidence")
        acquire.write_json(self.root / "provider-result.json", value)
        with patch.object(acquire, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            acquire.provider(args, self.url, "wechat_channels", self.root)
        command = run.call_args.args[0]
        self.assertEqual(command[0], sys.executable)
        self.assertEqual(Path(command[2]).name, "wechat_acquire.py")
        self.assertIn("--backend-url", command)
        self.assertNotIn("--allow-tencent-parse", command)


class OwnedBrowserProcessTests(unittest.TestCase):
    def fake_process(self, failure=None):
        process = Mock(pid=43210, returncode=0)
        process.communicate.side_effect = [failure, ("secret-url", "secret-stderr")] if failure else [("ok", "")]
        return process

    def test_success_returns_output_without_tree_kill(self):
        process = self.fake_process()
        with patch.object(acquire.subprocess, "Popen", return_value=process) as spawn, patch.object(acquire.subprocess, "run") as kill:
            result = acquire.run_owned_browser(["node", "fixture.mjs"], 5)
        self.assertEqual(result.stdout, "ok")
        self.assertEqual(result.returncode, 0)
        self.assertFalse(spawn.call_args.kwargs["shell"])
        self.assertEqual(spawn.call_args.kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(spawn.call_args.kwargs["stderr"], subprocess.DEVNULL)
        kill.assert_not_called()

    def test_windows_timeout_kills_only_owned_pid_tree_without_echoing_output(self):
        process = self.fake_process(subprocess.TimeoutExpired(["node", "secret-url"], 1, stderr="secret-stderr"))
        with patch.object(acquire, "os", SimpleNamespace(name="nt")), \
             patch.object(acquire.subprocess, "Popen", return_value=process) as spawn, \
             patch.object(acquire.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as kill, \
             patch("sys.stdout", new_callable=io.StringIO) as stdout, patch("sys.stderr", new_callable=io.StringIO) as stderr:
            with self.assertRaises(acquire.AcquisitionError) as raised:
                acquire.run_owned_browser(["node", "secret-url"], 1)
        self.assertEqual(raised.exception.code, "timeout")
        self.assertEqual(kill.call_args.args[0], ["taskkill", "/PID", "43210", "/T", "/F"])
        self.assertEqual(kill.call_args.kwargs["creationflags"], 0x08000000)
        self.assertEqual(spawn.call_args.kwargs["creationflags"], 0x08000000)
        self.assertIs(kill.call_args.kwargs["shell"], False)
        self.assertEqual(kill.call_args.kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(kill.call_args.kwargs["stderr"], subprocess.DEVNULL)
        self.assertNotIn("secret", str(raised.exception) + stdout.getvalue() + stderr.getvalue())

    def test_posix_timeout_kills_the_new_owned_process_group(self):
        process = self.fake_process(subprocess.TimeoutExpired(["node"], 1))
        killpg = Mock()
        with patch.object(acquire, "os", SimpleNamespace(name="posix", killpg=killpg)), \
             patch.object(acquire, "signal", SimpleNamespace(SIGKILL=9)), \
             patch.object(acquire.subprocess, "Popen", return_value=process) as spawn, \
             patch.object(acquire.subprocess, "run") as windows_kill:
            with self.assertRaises(acquire.AcquisitionError) as raised:
                acquire.run_owned_browser(["node"], 1)
        self.assertEqual(raised.exception.code, "timeout")
        self.assertIs(spawn.call_args.kwargs["start_new_session"], True)
        killpg.assert_called_once_with(43210, 9)
        windows_kill.assert_not_called()

    def test_cleanup_failure_is_explicit_and_still_reaps_owned_root(self):
        process = self.fake_process(subprocess.TimeoutExpired(["node"], 1))
        with patch.object(acquire, "os", SimpleNamespace(name="nt")), \
             patch.object(acquire.subprocess, "Popen", return_value=process), \
             patch.object(acquire.subprocess, "run", side_effect=subprocess.TimeoutExpired(["taskkill"], 10)):
            with self.assertRaises(acquire.AcquisitionError) as raised:
                acquire.run_owned_browser(["node"], 1)
        self.assertEqual(raised.exception.code, "browser_cleanup_failed")
        process.kill.assert_called_once()

    def test_start_failure_never_kills_an_unowned_pid(self):
        with patch.object(acquire.subprocess, "Popen", side_effect=FileNotFoundError), patch.object(acquire.subprocess, "run") as kill:
            with self.assertRaises(acquire.AcquisitionError) as raised:
                acquire.run_owned_browser(["missing-node"], 1)
        self.assertEqual(raised.exception.code, "dependency_missing")
        kill.assert_not_called()

    def test_invalid_wechat_budget_is_rejected_before_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            for seconds in (0, -1, 2147484):
                args = acquire.parser().parse_args(["--url", "https://weixin.qq.com/sph/Synthetic", "--output", str(Path(directory) / "unused"),
                                                   "--wechat-browser", "--allow-tencent-parse", "--timeout", str(seconds)])
                with self.subTest(seconds=seconds), patch.object(acquire, "provider") as provider, self.assertRaises(acquire.AcquisitionError):
                    acquire.acquire(args)
                provider.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows taskkill integration")
    def test_real_windows_browser_provider_timeout_removes_own_child_and_preserves_unrelated_process(self):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        def alive(pid):
            handle = kernel.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            try:
                code = ctypes.c_ulong()
                return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
            finally:
                kernel.CloseHandle(handle)
        sentinel = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
        child_pid = None
        try:
            with tempfile.TemporaryDirectory() as directory:
                pidfile = Path(directory) / "child.pid"
                code = ("import subprocess,sys,time,pathlib; "
                        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'], "
                        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
                        "pathlib.Path(sys.argv[1]).write_text(str(child.pid)); time.sleep(30)")
                real_popen = subprocess.Popen
                def local_browser(command, *args, **kwargs):
                    if command[0] == "node":
                        command = [sys.executable, "-c", code, str(pidfile)]
                    return real_popen(command, *args, **kwargs)
                output = Path(directory) / "acquisition"
                args = acquire.parser().parse_args(["--url", "https://www.douyin.com/video/1234567890123456789",
                                                    "--output", str(output), "--timeout", "1"])
                with patch.object(acquire.subprocess, "Popen", side_effect=local_browser):
                    with self.assertRaises(acquire.AcquisitionError) as raised:
                        acquire.acquire(args)
                self.assertEqual(raised.exception.code, "timeout")
                receipt = json.loads((output / "acquisition.json").read_text(encoding="utf-8"))
                self.assertEqual(receipt["status"], "blocked")
                self.assertFalse((output / "source.json").exists())
                child_pid = int(pidfile.read_text())
                self.assertFalse(alive(child_pid), "Owned grandchild survived the timeout")
                self.assertIsNone(sentinel.poll(), "Unrelated process was terminated")
        finally:
            if child_pid and alive(child_pid):
                subprocess.run(["taskkill", "/PID", str(child_pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000, timeout=10)
            sentinel.kill()
            sentinel.wait(timeout=5)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg and FFprobe are required")
class ActualMediaChecks(unittest.TestCase):
    setUp = SourceContracts.setUp
    def make_video(self, target):
        result = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                                 "color=c=blue:s=96x160:r=10:d=1", "-f", "lavfi", "-i",
                                 "sine=frequency=440:duration=1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                                 "-c:a", "aac", "-shortest", str(target)], capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_ready_requires_actual_decode_and_source_hash(self):
        output = self.root / "download"
        args = acquire.parser().parse_args(["--url", self.url, "--output", str(output), "--download"])
        def mocked_provider(*unused):
            media = output / "source.mp4"
            self.make_video(media)
            return {**self.data, "status": "downloaded", "media_path": str(media)}
        with patch.object(acquire, "provider", side_effect=mocked_provider):
            receipt = acquire.acquire(args)
        source = json.loads((output / "source.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["status"], "ready")
        self.assertTrue(receipt["media"]["complete_decode_verified"])
        self.assertTrue(receipt["media"]["has_audio"])
        self.assertEqual(source["source_sha256"], acquire.sha256(Path(source["source_path"])))
        self.assertEqual(source["url"], self.url)
        self.assertFalse(source["content_reviewed"])

    def test_wechat_export_source_keeps_raw_id_and_safe_reversible_local_key(self):
        data = wechat_export_fixture()
        output = self.root / "wechat-download"
        args = acquire.parser().parse_args(["--url", data["input_url"], "--output", str(output), "--download",
                                           "--wechat-browser", "--allow-tencent-parse"])
        def mocked_provider(*unused):
            media = output / "source.mp4"
            self.make_video(media)
            return {**data, "status": "downloaded", "media_path": str(media)}
        with patch.object(acquire, "provider", side_effect=mocked_provider):
            receipt = acquire.acquire(args)
        source = json.loads((output / "source.json").read_text(encoding="utf-8"))
        expected = "wxexp_" + base64.urlsafe_b64encode(data["native_id"].encode("utf-8")).decode("ascii").rstrip("=")
        self.assertEqual(receipt["status"], "ready")
        self.assertTrue(receipt["media"]["complete_decode_verified"])
        self.assertEqual(source["video_id"], expected)
        self.assertEqual(source["video_id_kind"], "encoded_platform_export_id")
        self.assertEqual(source["native_id"], data["native_id"])
        self.assertEqual(source["platform_export_id"], data["native_id"])
        self.assertEqual(source["input_url"], data["input_url"])
        self.assertEqual(source["native_id_kind"], "platform_export_id")
        self.assertFalse(source["identity_stability_verified"])
        self.assertEqual(source["source_sha256"], acquire.sha256(Path(source["source_path"])))
        identity_spec = importlib.util.spec_from_file_location(
            "acquired_source_identity", ROOT / "skill/analyze-folder-videos-to-feishu/scripts/source_identity.py")
        identity_module = importlib.util.module_from_spec(identity_spec)
        identity_spec.loader.exec_module(identity_module)
        identity = identity_module.source_identity(source["video_id"], source)
        self.assertEqual(identity["native_id"], data["native_id"])
        self.assertEqual(identity["input_url"], data["input_url"])

    def test_audio_only_and_fake_video_are_rejected(self):
        audio = self.root / "audio.m4a"
        generated = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                                    "sine=frequency=440:duration=1", "-c:a", "aac", str(audio)], capture_output=True, timeout=30)
        self.assertEqual(generated.returncode, 0, generated.stderr)
        fake = self.root / "fake.mp4"
        fake.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"not a video" * 100)
        for file in (audio, fake):
            with self.subTest(file=file.name), self.assertRaises(acquire.AcquisitionError):
                acquire.verify_media(file, "ffprobe", "ffmpeg", 30)

    def test_complete_decode_failure_is_not_ready(self):
        file = self.root / "video.mp4"
        self.make_video(file)
        original_run = acquire.run
        def decoder_fails(command, timeout):
            if command[0] == "ffmpeg":
                return subprocess.CompletedProcess(command, 1, "", "corrupt frame")
            return original_run(command, timeout)
        with patch.object(acquire, "run", side_effect=decoder_fails), self.assertRaisesRegex(acquire.AcquisitionError, "Complete"):
            acquire.verify_media(file, "ffprobe", "ffmpeg", 30)

    def test_decoder_maps_real_video_not_attached_cover(self):
        file = self.root / "probe-fixture.mp4"
        file.write_bytes(b"fixture media bytes")
        probe = {"streams": [
            {"index": 0, "codec_type": "video", "disposition": {"attached_pic": 1}, "width": 96, "height": 160},
            {"index": 1, "codec_type": "video", "disposition": {"attached_pic": 0}, "width": 96, "height": 160, "duration": "1"}],
            "format": {"duration": "1"}}
        calls = []
        def result(command, timeout):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, json.dumps(probe) if command[0] == "ffprobe" else "", "")
        with patch.object(acquire, "run", side_effect=result):
            acquire.verify_media(file, "ffprobe", "ffmpeg", 30)
        self.assertIn("0:1", calls[1])
        self.assertNotIn("0:v:0", calls[1])


if __name__ == "__main__":
    unittest.main()
