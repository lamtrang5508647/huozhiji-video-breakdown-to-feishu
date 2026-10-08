"""Synthetic Channels acquisition contracts; no platform or parser requests."""
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "wechat_acquire", ROOT / "skill/video-link-extractor/scripts/wechat_acquire.py")
acquirer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(acquirer)
OPAQUE_SHARE = "https://weixin.qq.com/sph/syntheticShare"
CHANNELS_PAGE = "https://channels.weixin.qq.com/web/pages/feed"
SHARE = CHANNELS_PAGE + "?object_id=1234567890123456789&objectNonceId=syntheticNonce"
BACKEND = "http://127.0.0.1:2022"
SOCKET_MESSAGE = "please initialize the client socket connection first"


def profile(url=SHARE):
    return {"code": 0, "provenance": {"input_url": url}, "data": {"errCode": 0, "data": {
        "object": {"id": "1234567890123456789", "objectNonceId": "syntheticNonce",
                   "objectDesc": {"description": "真实平台文案，不是文件名",
                                  "media": [{"url": "https://finder.video.qq.com/synthetic.mp4"}]}}}}}


class WechatAcquireTests(unittest.TestCase):
    def run_fixture(self, payload=None, url=SHARE, **kwargs):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(acquirer, "fetch_profile", return_value=payload or profile()) as fetch:
                result = acquirer.acquire(url, Path(directory), **kwargs)
            saved = json.loads((Path(directory) / "provider-result.json").read_text(encoding="utf-8"))
            self.assertEqual(result, saved)
            return result, fetch

    def test_no_provider_is_off_by_default_and_makes_no_request(self):
        result, fetch = self.run_fixture()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "provider_not_configured")
        self.assertFalse(result["identity_verified"])
        fetch.assert_not_called()

    def test_real_native_id_and_exact_caption_are_retained(self):
        result, fetch = self.run_fixture(backend_url=BACKEND)
        self.assertEqual(result["status"], "metadata_ready")
        self.assertEqual(result["native_id"], "1234567890123456789")
        self.assertEqual(result["title"], "真实平台文案，不是文件名")
        self.assertEqual(result["title_kind"], "caption")
        self.assertTrue(result["identity_verified"])
        self.assertIsNone(result["media_path"])
        self.assertNotIn("media", result)
        fetch.assert_called_once()

    def test_missing_native_id_cannot_be_replaced_with_share_token_or_hash(self):
        value = profile()
        del value["data"]["data"]["object"]["id"]
        result, _ = self.run_fixture(value, backend_url=BACKEND)
        self.assertEqual(result["status"], "metadata_missing")
        self.assertEqual(result["reason"], "native_id_missing")
        self.assertIsNone(result["native_id"])

    def test_missing_caption_has_no_template_fallback(self):
        value = profile()
        value["data"]["data"]["object"]["objectDesc"]["description"] = "  "
        result, _ = self.run_fixture(value, backend_url=BACKEND)
        self.assertEqual(result["status"], "metadata_missing")
        self.assertEqual(result["reason"], "title_missing")
        self.assertIsNone(result["title"])

    def test_bare_response_is_not_proof_of_opaque_share_link_identity(self):
        value = profile(OPAQUE_SHARE)
        del value["provenance"]
        result, _ = self.run_fixture(value, url=OPAQUE_SHARE, backend_url=BACKEND)
        self.assertEqual(result["status"], "metadata_missing")
        self.assertEqual(result["reason"], "source_binding_missing")

    def test_echoed_url_and_arbitrary_object_id_do_not_prove_opaque_share_identity(self):
        value = profile(OPAQUE_SHARE)
        value["data"]["data"]["object"]["id"] = "unrelated-native-id"
        value["params"] = {"url": OPAQUE_SHARE}
        value["request_url"] = OPAQUE_SHARE
        with patch.object(acquirer, "download_media", return_value="/mock/not-reached.mp4") as download:
            result, _ = self.run_fixture(value, url=OPAQUE_SHARE, backend_url=BACKEND, download=True)
        self.assertEqual(result["status"], "metadata_missing")
        self.assertEqual(result["reason"], "source_binding_missing")
        self.assertFalse(result["identity_verified"])
        self.assertIsNone(result["native_id"])
        download.assert_not_called()

    def test_echo_only_receipts_do_not_establish_identity(self):
        for key in ("input_url", "source_url", "request_url", "share_url", "shareUrl", "web_url"):
            with self.subTest(receipt=key):
                value = profile(OPAQUE_SHARE)
                del value["provenance"]
                value[key] = OPAQUE_SHARE
                result, _ = self.run_fixture(value, url=OPAQUE_SHARE, backend_url=BACKEND)
                self.assertEqual(result["reason"], "source_binding_missing")
                self.assertFalse(result["identity_verified"])

    def test_outer_nonce_or_token_echo_cannot_bind_an_unrelated_object(self):
        for query_key, response_key, token in (("objectNonceId", "objectNonceId", "nonce-echo"),
                                                ("token", "token", "token-echo")):
            with self.subTest(query=query_key):
                url = CHANNELS_PAGE + "?" + query_key + "=" + token
                value = profile(url)
                del value["data"]["data"]["object"]["objectNonceId"]
                value[response_key] = token
                value["params"] = {"url": url, response_key: token}
                result, _ = self.run_fixture(value, url=url, backend_url=BACKEND)
                self.assertEqual(result["reason"], "source_binding_missing")
                self.assertFalse(result["identity_verified"])

    def test_unexpected_source_receipt_is_rejected_before_download(self):
        value = profile()
        value["provenance"]["input_url"] = "https://weixin.qq.com/sph/otherShare"
        with patch.object(acquirer, "download_media") as download:
            result, _ = self.run_fixture(value, backend_url=BACKEND, download=True)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "unexpected_source")
        self.assertFalse(result["identity_verified"])
        download.assert_not_called()

    def test_conflicting_native_query_rejected_even_with_matching_receipt(self):
        url = CHANNELS_PAGE + "?object_id=otherId"
        value = profile()
        value["provenance"]["input_url"] = url
        with self.assertRaisesRegex(acquirer.AcquireError, "unexpected_source"):
            acquirer.metadata(value, url)

    def test_native_id_query_can_bind_without_receipt(self):
        value = profile()
        del value["provenance"]
        fields, _ = acquirer.metadata(value, CHANNELS_PAGE + "?object_id=1234567890123456789")
        self.assertTrue(fields["identity_verified"])
        self.assertEqual(fields["identity_evidence"], "native_query_match")

    def test_upstream_oid_nid_bind_decoded_big_endian_values(self):
        # Fixed vectors from the upstream pkg/util/util_test.go, not our encoder.
        url = CHANNELS_PAGE + "?oid=SuZgeu3RR6A&nid=zpu6HctPGGM"
        value = profile(url)
        item = value["data"]["data"]["object"]
        item.update(id="5397107284542310304", objectNonceId="14887697630331082851")
        result, _ = self.run_fixture(value, url=url, backend_url=BACKEND)
        self.assertEqual(result["status"], "metadata_ready")
        self.assertEqual(result["native_id"], "5397107284542310304")
        self.assertEqual(result["input_url"], url)
        self.assertEqual(result["canonical_url"], url)
        self.assertTrue(result["identity_verified"])

    def test_oid_upstream_uint64_vectors_and_padding_rules(self):
        vectors = (("AAAAAAAAAAA", "0"), ("AAAAAAAAAAE", "1"),
                   ("AAAAAAAAAQA", "256"), ("__________8", "18446744073709551615"),
                   ("z4lFRwCKGX4", "14954560208914487678"),
                   ("AQ", "1"), ("AQ==", "1"),
                   ("//////////8=", "18446744073709551615"),
                   ("AAAAAAAAAAEAAAAA", "1"))
        for encoded, native in vectors:
            with self.subTest(encoded=encoded):
                url = CHANNELS_PAGE + "?" + urlencode({"oid": encoded})
                value = profile(url)
                value["data"]["data"]["object"]["id"] = native
                fields, _ = acquirer.metadata(value, url)
                self.assertEqual(fields["native_id"], native)
                self.assertTrue(fields["identity_verified"])

    def test_nid_can_bind_only_to_the_decoded_object_nonce(self):
        url = CHANNELS_PAGE + "?nid=zpu6HctPGGM"
        value = profile(url)
        item = value["data"]["data"]["object"]
        item["objectNonceId"] = "14887697630331082851"
        fields, _ = acquirer.metadata(value, url)
        self.assertTrue(fields["identity_verified"])
        for wrong in ("14887697630331082852", "zpu6HctPGGM"):
            item["objectNonceId"] = wrong
            with self.subTest(nonce=wrong), self.assertRaisesRegex(acquirer.AcquireError, "unexpected_source"):
                acquirer.metadata(value, url)

    def test_encoded_query_conflicts_never_reach_download(self):
        queries = ("oid=AAAAAAAAAAE", "nid=AAAAAAAAAAE",
                   "oid=", "oid=not*base64", "oid=A", "nid=",
                   "nid=not*base64", "oid=AAAAAAAAAAE&oid=AAAAAAAAAAE",
                   "nid=zpu6HctPGGM&nonceId=syntheticNonce",
                   "oid=SuZgeu3RR6A&object_id=1234567890123456789")
        for query in queries:
            # A correct legacy ID cannot hide an invalid/conflicting new alias.
            url = SHARE + "&" + query
            with self.subTest(query=query), patch.object(acquirer, "download_media", return_value="/synthetic/unexpected.mp4") as download:
                result, _ = self.run_fixture(profile(url), url=url, backend_url=BACKEND, download=True)
                self.assertEqual(result["status"], "blocked")
                self.assertEqual(result["reason"], "unexpected_source")
                self.assertFalse(result["identity_verified"])
                download.assert_not_called()

    def test_nid_echo_outside_object_is_not_source_proof(self):
        url = CHANNELS_PAGE + "?nid=zpu6HctPGGM"
        value = profile(url)
        del value["data"]["data"]["object"]["objectNonceId"]
        value["params"] = {"nid": "14887697630331082851", "url": url}
        result, _ = self.run_fixture(value, url=url, backend_url=BACKEND)
        self.assertEqual(result["reason"], "source_binding_missing")
        self.assertFalse(result["identity_verified"])

    def test_disconnected_upstream_socket_requires_page_connection(self):
        # Upstream apiresult.Err deliberately responds HTTP 200 with this JSON.
        for value in ({"code": 400, "msg": SOCKET_MESSAGE},
                      {"code": 0, "data": {"code": 400, "msg": SOCKET_MESSAGE}}):
            with self.subTest(payload=value), patch.object(acquirer, "download_media") as download:
                result, _ = self.run_fixture(value, backend_url=BACKEND, download=True)
                self.assertEqual(result["status"], "blocked")
                self.assertEqual(result["reason"], "page_connection_required")
                self.assertFalse(result["identity_verified"])
                download.assert_not_called()

    def test_other_provider_errors_are_not_page_connection_errors(self):
        for value in ({"code": 400, "msg": "socket timeout"},
                      {"code": 403, "msg": SOCKET_MESSAGE},
                      {"code": 400, "msg": SOCKET_MESSAGE + " (different error)"}):
            with self.subTest(payload=value):
                result, _ = self.run_fixture(value, backend_url=BACKEND)
                self.assertEqual(result["reason"], "provider_rejected_source")
        value = profile()
        value["msg"] = SOCKET_MESSAGE
        result, _ = self.run_fixture(value, backend_url=BACKEND)
        self.assertEqual(result["status"], "metadata_ready")

    def test_encoded_query_download_stays_unverified(self):
        url = CHANNELS_PAGE + "?oid=SuZgeu3RR6A&nid=zpu6HctPGGM"
        value = profile(url)
        value["data"]["data"]["object"].update(id="5397107284542310304", objectNonceId="14887697630331082851")
        with patch.object(acquirer, "download_media", return_value="/synthetic/source.mp4") as download:
            result, _ = self.run_fixture(value, url=url, backend_url=BACKEND, download=True)
        self.assertEqual(result["status"], "downloaded")
        self.assertFalse(result["media_verified"])
        self.assertEqual(download.call_args.args[2], "5397107284542310304")

    def test_nonce_query_can_bind_without_receipt_but_conflicting_nonce_fails(self):
        value = profile()
        del value["provenance"]
        fields, _ = acquirer.metadata(value, CHANNELS_PAGE + "?nonce_id=syntheticNonce")
        self.assertTrue(fields["identity_verified"])
        with self.assertRaisesRegex(acquirer.AcquireError, "unexpected_source"):
            acquirer.metadata(value, CHANNELS_PAGE + "?nonce_id=wrongNonce")

    def test_token_query_can_bind_only_to_the_source_object_token(self):
        url = CHANNELS_PAGE + "?token=synthetic-token"
        value = profile(url)
        value["data"]["data"]["object"]["generalToken"] = "synthetic-token"
        result, _ = self.run_fixture(value, url=url, backend_url=BACKEND)
        self.assertTrue(result["identity_verified"])
        value["data"]["data"]["object"]["generalToken"] = "other-token"
        result, _ = self.run_fixture(value, url=url, backend_url=BACKEND)
        self.assertEqual(result["reason"], "unexpected_source")

    def test_explicit_online_endpoint_requires_allow_flag(self):
        result, fetch = self.run_fixture(online_endpoint="https://parser.example/api/parse")
        self.assertEqual(result["reason"], "online_parser_not_authorized")
        fetch.assert_not_called()
        online_url = CHANNELS_PAGE + "?object_id=nativeFixtureId"
        value = {"input_url": online_url, "data": {"feedInfo": {"id": "nativeFixtureId",
                 "title": "平台实际标题", "h264VideoInfo": {"videoUrl": "https://finder.video.qq.com/mock.mp4"}}}}
        result, fetch = self.run_fixture(value, url=online_url, online_endpoint="https://parser.example/api/parse", allow_online_parser=True)
        self.assertEqual(result["status"], "metadata_ready")
        self.assertEqual(result["title_kind"], "title")
        self.assertEqual(result["provider"], "online_parser")
        fetch.assert_called_once()

    def test_remote_http_backend_and_credential_endpoints_are_rejected(self):
        for endpoint in ("http://remote.example:2022", "http://user:secret@127.0.0.1:2022", "http://127.0.0.1:2022?token=secret"):
            with self.subTest(endpoint=endpoint):
                result, fetch = self.run_fixture(backend_url=endpoint)
                self.assertEqual(result["reason"], "backend_must_be_loopback_http")
                fetch.assert_not_called()

    def test_encrypted_media_does_not_call_downloader_or_decrypt(self):
        value = profile()
        value["data"]["data"]["object"]["objectDesc"]["media"][0]["decodeKey"] = "fixture-key"
        with patch.object(acquirer, "download_media") as download:
            result, _ = self.run_fixture(value, backend_url=BACKEND, download=True)
        self.assertEqual(result["reason"], "encrypted_media_not_supported")
        self.assertEqual(result["status"], "blocked")
        download.assert_not_called()

    def test_top_level_encryption_marker_also_blocks_media(self):
        item = {"id": "synthetic", "description": "caption", "decrypt_key": "fixture-key",
                "h264VideoInfo": {"videoUrl": "https://finder.video.qq.com/mock.mp4"}}
        with self.assertRaisesRegex(acquirer.AcquireError, "encrypted_media_not_supported"):
            acquirer.media_url(item)

    def test_conflicting_native_ids_are_rejected(self):
        value = profile()
        value["data"]["data"]["object"]["objectId"] = "other-native-id"
        result, _ = self.run_fixture(value, backend_url=BACKEND)
        self.assertEqual(result["reason"], "native_id_invalid_or_conflicting")

    def test_transfer_byte_limit_removes_only_new_partial_file(self):
        class Response(io.BytesIO):
            def geturl(self):
                return "https://finder.video.qq.com/mock.mp4"
            class headers:
                @staticmethod
                def get_content_type():
                    return "video/mp4"
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            existing = output / "unrelated.mp4"
            existing.write_bytes(b"keep")
            response = Response(b"synthetic-media-bytes")
            with patch.object(acquirer.urllib.request, "build_opener") as builder:
                builder.return_value.open.return_value = response
                with self.assertRaisesRegex(acquirer.AcquireError, "media_size_limit"):
                    acquirer.download_media("https://finder.video.qq.com/mock.mp4", output, "native-id", 1, 5)
            self.assertEqual(existing.read_bytes(), b"keep")
            self.assertEqual([path.name for path in output.iterdir()], ["unrelated.mp4"])

    def test_downloaded_result_remains_unverified_for_outer_media_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str((Path(directory) / "fixture.mp4").resolve())
            with patch.object(acquirer, "fetch_profile", return_value=profile()), patch.object(acquirer, "download_media", return_value=path):
                result = acquirer.acquire(SHARE, Path(directory), backend_url=BACKEND, download=True)
            self.assertEqual(result["status"], "downloaded")
            self.assertEqual(result["media_path"], path)
            self.assertFalse(result["media_verified"])

    def test_media_host_and_every_redirect_remain_https_tencent(self):
        for value in ("http://finder.video.qq.com/mock", "https://qq.com.evil.example/mock", "https://user:secret@finder.video.qq.com/mock"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                acquirer.validated_media_url(value)
        with self.assertRaises(ValueError):
            acquirer.MediaRedirect().redirect_request(None, None, 302, "", {}, "http://finder.video.qq.com/mock")

    def test_cli_summary_does_not_print_share_query_tokens(self):
        with tempfile.TemporaryDirectory() as directory, patch("sys.stdout", new_callable=io.StringIO) as stdout:
            code = acquirer.main(["--url", SHARE + "&token=synthetic-sensitive", "--output", directory])
        self.assertEqual(code, 2)
        self.assertNotIn("synthetic-sensitive", stdout.getvalue())
        self.assertEqual(json.loads(stdout.getvalue())["reason"], "provider_not_configured")


if __name__ == "__main__":
    unittest.main()
