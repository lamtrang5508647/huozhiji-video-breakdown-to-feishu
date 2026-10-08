#!/usr/bin/env python3
"""Acquire Channels metadata/media through an explicitly configured provider.

Only standard-library HTTP is used. No browser, credentials, decryption, proxy
mutation or backend installation. Transfer success is not final media acceptance.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit
import urllib.error
import urllib.request

VENDOR = Path(__file__).resolve().parents[1] / "vendor" / "qiaomu-download"
sys.path.insert(0, str(VENDOR))
from channels_helpers import normalize_share_url, validated_media_url

MAX_JSON_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_BYTES = 1024 * 1024 * 1024


class AcquireError(ValueError):
    def __init__(self, code: str, status: str = "blocked"):
        self.code, self.status = code, status
        super().__init__(code)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        raise AcquireError("provider_redirect_refused")


class MediaRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        validated_media_url(new_url)
        return super().redirect_request(request, response, code, message, headers, new_url)


def provider_endpoint(backend_url: str | None, online_endpoint: str | None,
                      allow_online_parser: bool) -> tuple[str, str]:
    if backend_url and online_endpoint:
        raise AcquireError("choose_one_provider")
    if backend_url:
        parsed = urlsplit(backend_url)
        if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ("", "/")):
            raise AcquireError("backend_must_be_loopback_http")
        return backend_url.rstrip("/") + "/api/channels/feed/profile", "local_backend"
    if online_endpoint:
        if not allow_online_parser:
            raise AcquireError("online_parser_not_authorized")
        parsed = urlsplit(online_endpoint)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.port or parsed.query or parsed.fragment):
            raise AcquireError("online_endpoint_must_be_https")
        return online_endpoint, "online_parser"
    raise AcquireError("provider_not_configured")


def fetch_profile(url: str, endpoint: str, provider: str, timeout: float) -> dict:
    if provider == "local_backend":
        request = urllib.request.Request(endpoint + "?" + urlencode({"url": url}))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    else:
        request = urllib.request.Request(endpoint, data=json.dumps({"url": url}).encode("utf-8"),
                                         headers={"Content-Type": "application/json"}, method="POST")
        opener = urllib.request.build_opener(NoRedirect())
    with opener.open(request, timeout=timeout) as response:
        raw = response.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        raise AcquireError("provider_response_too_large")
    result = json.loads(raw.decode("utf-8"))
    if not isinstance(result, dict):
        raise AcquireError("invalid_provider_response")
    return result


def dict_layers(payload: dict) -> list[dict]:
    layers = [payload]
    for _ in range(3):
        inner = layers[-1].get("data")
        if not isinstance(inner, dict):
            break
        layers.append(inner)
    for layer in layers:
        if layer.get("success") is False or layer.get("code", 0) not in (0, "0", None) or layer.get("errCode", 0) not in (0, "0", None):
            raise AcquireError("provider_rejected_source")
    return layers


def source_key(url: str) -> tuple:
    parsed = urlsplit(normalize_share_url(url))
    return parsed.hostname, parsed.path, tuple(sorted(parse_qsl(parsed.query, keep_blank_values=True)))


def verified_identity(input_url: str, layers: list[dict], item: dict, native_id: str) -> str:
    """Bind input identity fields to the source object; URL echoes are not proof."""
    expected = source_key(input_url)
    receipts = []
    for node in [*layers, item]:
        for key in ("input_url", "source_url", "request_url", "share_url", "shareUrl", "web_url"):
            if node.get(key):
                receipts.append(node[key])
        for key in ("provenance", "params"):
            child = node.get(key)
            if isinstance(child, dict):
                receipts.extend(child[name] for name in ("input_url", "source_url", "request_url", "url") if child.get(name))
    for receipt in receipts:
        try:
            if not isinstance(receipt, str) or source_key(receipt) != expected:
                raise AcquireError("unexpected_source")
        except (ValueError, TypeError):
            raise AcquireError("unexpected_source") from None

    pairs = parse_qsl(urlsplit(input_url).query, keep_blank_values=True)
    identity_keys = ("id", "object_id", "objectId", "feed_id", "feedId")
    # A provider can echo any input URL beside an unrelated object. Receipts are
    # checked for contradictions above, but only original query/object matches
    # can establish the identity used below.
    proof = False
    for key in identity_keys:
        values = [value for name, value in pairs if name == key]
        if values:
            if len(values) != 1 or values[0] != native_id:
                raise AcquireError("unexpected_source")
            proof = True
    for aliases, fields in [(("nonce_id", "nonceId", "objectNonceId"), ("objectNonceId", "nonce_id", "nonceId")),
                            (("token",), ("token", "generalToken"))]:
        values = [value for name, value in pairs if name in aliases]
        # Outer response fields and params may merely echo request arguments.
        # Match only identity fields attached to this object/feedInfo itself.
        returned = [str(item[field]) for field in fields
                    if isinstance(item.get(field), (str, int)) and not isinstance(item.get(field), bool)
                    and str(item[field])]
        if values:
            if len(values) != 1 or not values[0]:
                raise AcquireError("unexpected_source")
            if returned:
                if any(value != values[0] for value in returned):
                    raise AcquireError("unexpected_source")
                proof = True
    if not proof:
        raise AcquireError("source_binding_missing", "metadata_missing")
    return "native_query_match"


def metadata(payload: dict, input_url: str) -> tuple[dict, dict]:
    layers = dict_layers(payload)
    items = [node[key] for node in layers for key in ("object", "feedInfo") if isinstance(node.get(key), dict)]
    if len(items) != 1:
        raise AcquireError("source_object_missing_or_ambiguous", "metadata_missing")
    item = items[0]
    identifiers = [str(item[key]) for key in ("id", "objectId", "feedId", "native_id")
                   if isinstance(item.get(key), (str, int)) and not isinstance(item.get(key), bool) and str(item[key])]
    if not identifiers:
        raise AcquireError("native_id_missing", "metadata_missing")
    if len(set(identifiers)) != 1 or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identifiers[0]):
        raise AcquireError("native_id_invalid_or_conflicting")
    native_id = identifiers[0]
    binding = verified_identity(input_url, layers, item, native_id)
    desc = item.get("objectDesc") if isinstance(item.get("objectDesc"), dict) else item
    choices = [(item.get("title"), "title"), (desc.get("caption"), "caption"), (desc.get("description"), "caption")]
    title, title_kind = next(((value.strip(), kind) for value, kind in choices if isinstance(value, str) and value.strip()), (None, None))
    if title is None:
        raise AcquireError("title_missing", "metadata_missing")
    return {"native_id": native_id, "title": title, "title_kind": title_kind,
            "identity_verified": True, "identity_evidence": binding}, item


def media_url(item: dict) -> str:
    desc = item.get("objectDesc") if isinstance(item.get("objectDesc"), dict) else item
    encryption_keys = ("decodeKey", "decrypt_key", "decode_key", "encrypted", "isEncrypted")
    if any(node.get(key) not in (None, "", 0, "0", False) for node in (item, desc) for key in encryption_keys):
        raise AcquireError("encrypted_media_not_supported")
    candidates = []
    for name in ("h264VideoInfo", "h265VideoInfo"):
        if isinstance(item.get(name), dict):
            candidates.append(item[name])
    if item.get("videoUrl"):
        candidates.append(item)
    if isinstance(desc.get("media"), list):
        candidates.extend(value for value in desc["media"] if isinstance(value, dict))
    encrypted = False
    for candidate in candidates:
        if any(candidate.get(key) not in (None, "", 0, "0", False) for key in encryption_keys):
            encrypted = True
            continue
        value = candidate.get("videoUrl") or candidate.get("url")
        if not isinstance(value, str) or not value:
            continue
        suffix = candidate.get("urlToken", "")
        if not isinstance(suffix, str):
            continue
        try:
            return validated_media_url(value + suffix)
        except ValueError:
            continue
    raise AcquireError("encrypted_media_not_supported" if encrypted else "usable_media_missing")


def download_media(url: str, output: Path, native_id: str, timeout: float, max_bytes: int) -> str:
    validated_media_url(url)
    destination = output / ("wechat-" + native_id + ".mp4")
    if destination.exists():
        raise AcquireError("media_destination_exists")
    staged = None
    created = False
    try:
        opener = urllib.request.build_opener(MediaRedirect())
        request = urllib.request.Request(url, headers={"Referer": "https://channels.weixin.qq.com/"})
        with opener.open(request, timeout=timeout) as response, tempfile.NamedTemporaryFile(prefix=".wechat-", suffix=".part", dir=output, delete=False) as handle:
            staged = Path(handle.name)
            validated_media_url(response.geturl())
            if response.headers.get_content_type() in ("text/html", "application/json"):
                raise AcquireError("media_response_is_not_video")
            size = 0
            for chunk in iter(lambda: response.read(1024 * 1024), b""):
                size += len(chunk)
                if size > max_bytes:
                    raise AcquireError("media_size_limit")
                handle.write(chunk)
            if size == 0:
                raise AcquireError("empty_media")
        with destination.open("xb") as target, staged.open("rb") as source:
            created = True
            shutil.copyfileobj(source, target)
        return str(destination.resolve())
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


def write_result(output: Path, result: dict) -> None:
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=".provider-result-", suffix=".json", dir=output, delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary.replace(output / "provider-result.json")


def acquire(url: str, output: Path, *, backend_url: str | None = None,
            online_endpoint: str | None = None, allow_online_parser: bool = False,
            download: bool = False, timeout: float = 30, max_bytes: int = DEFAULT_MAX_BYTES) -> dict:
    output = Path(output).expanduser().resolve()
    result: dict[str, Any] = {"status": "blocked", "platform": "wechat_channels", "input_url": url,
                              "canonical_url": None, "native_id": None, "title": None,
                              "title_kind": None, "media_path": None, "identity_verified": False}
    try:
        canonical = normalize_share_url(url)
        result["canonical_url"] = canonical
        if not math.isfinite(timeout) or timeout <= 0 or timeout > 300 or max_bytes <= 0:
            raise AcquireError("invalid_limits")
        endpoint, provider = provider_endpoint(backend_url, online_endpoint, allow_online_parser)
        result["provider"] = provider
        payload = fetch_profile(canonical, endpoint, provider, timeout)
        fields, item = metadata(payload, canonical)
        result.update(fields)
        result["status"] = "metadata_ready"
        if download:
            selected = media_url(item)
            output.mkdir(parents=True, exist_ok=True)
            result["media_path"] = download_media(selected, output, fields["native_id"], timeout, max_bytes)
            result["status"] = "downloaded"
            result["media_verified"] = False
    except AcquireError as exc:
        result.update(status=exc.status, reason=exc.code)
    except (urllib.error.URLError, TimeoutError, OSError):
        result.update(status="blocked", reason="provider_or_transfer_unavailable")
    except (ValueError, TypeError, UnicodeError):
        result.update(status="blocked", reason="invalid_provider_or_source")
    write_result(output, result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--metadata-only", action="store_true")
    mode.add_argument("--download", action="store_true")
    parser.add_argument("--backend-url")
    parser.add_argument("--online-endpoint")
    parser.add_argument("--allow-online-parser", action="store_true")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    args = parser.parse_args(argv)
    result = acquire(args.url, args.output, backend_url=args.backend_url, online_endpoint=args.online_endpoint,
                     allow_online_parser=args.allow_online_parser, download=args.download,
                     timeout=args.timeout, max_bytes=args.max_bytes)
    # Source URLs may carry tokens; stdout is a fixed-code summary only.
    print(json.dumps({"status": result["status"], "reason": result.get("reason"),
                      "result_path": str(args.output.expanduser().resolve() / "provider-result.json")}, ensure_ascii=False))
    return 0 if result["status"] in ("metadata_ready", "downloaded") else 2


if __name__ == "__main__":
    raise SystemExit(main())
