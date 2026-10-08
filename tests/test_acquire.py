"""Source provenance checks and actual media decoding, with no platform/network requests."""
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
spec = importlib.util.spec_from_file_location("link_acquire", ROOT / "skill/video-link-extractor/scripts/acquire.py")
acquire = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acquire)


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
