"""Source identity and canonical-to-live field mapping; no network operations."""
from __future__ import annotations

import base64
import re
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit


def canonical_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError("Source URL must be explicit non-empty text")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as exc:
        raise ValueError("Invalid source URL") from exc
    if (parts.scheme != "https" or not parts.hostname or parts.username is not None or parts.password is not None
            or parts.fragment or any(c.isspace() or ord(c) < 32 for c in value)):
        raise ValueError("Source URL must be HTTPS without credentials, whitespace or a fragment")
    host = parts.hostname.lower()
    if ":" in host:
        host = "[" + host + "]"
    if port not in (None, 443):
        host += ":" + str(port)
    return urlunsplit(("https", host, parts.path or "/", parts.query, ""))


def bilibili_id(url: str) -> str | None:
    parts = urlsplit(canonical_url(url))
    if parts.hostname not in {"bilibili.com", "www.bilibili.com"}:
        return None
    match = re.fullmatch(r"/video/(BV[0-9A-Za-z]{10,12})/?", parts.path)
    return match.group(1) if match else None


def bilibili_part(url: str) -> int:
    parts = [value for key, value in parse_qsl(urlsplit(canonical_url(url)).query, keep_blank_values=True) if key == 'p']
    if len(parts) > 1 or (parts and not re.fullmatch(r'[1-9][0-9]*', parts[0])):
        raise ValueError('Bilibili p must be one positive integer')
    return int(parts[0]) if parts else 1


def source_identity(video_id: str, source: dict, *, platform: str | None = None,
                    id_field: str | None = None) -> dict:
    if not isinstance(video_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}", video_id):
        raise ValueError("Unsafe or missing stable source video_id")
    declared = source.get("platform")
    if platform and declared and platform != declared:
        raise ValueError("Explicit platform differs from manifest.source.platform")
    platform = platform or declared
    # Compatibility is restricted to a canonical Bilibili URL, never a title,
    # substring, short link or an arbitrary URL that happens to mention a BV ID.
    raw_url = source.get("url")
    if not raw_url and re.fullmatch(r"BV[0-9A-Za-z]{10,12}", video_id) and platform in (None, "B站"):
        raw_url = "https://www.bilibili.com/video/" + video_id
    url = canonical_url(raw_url)
    bv = bilibili_id(url)
    if not platform and bv == video_id:
        platform = "B站"
    if not isinstance(platform, str) or not platform.strip() or platform != platform.strip():
        raise ValueError("Non-Bilibili sources require an explicit source platform")
    if platform == "B站" and bv != video_id:
        raise ValueError("B站 source URL does not identify the exact stable video_id")
    if platform == "B站":
        bilibili_part(url)
    if bv and platform != "B站":
        raise ValueError("Bilibili URL conflicts with the declared source platform")
    if id_field is not None and (not isinstance(id_field, str) or not id_field.strip()):
        raise ValueError("source_identity.id_field must be a canonical field name")
    parts = urlsplit(url)
    if (source.get("native_id_kind") == "platform_export_id" or "platform_export_id" in source
            or source.get("video_id_kind") == "encoded_platform_export_id"
            or (platform == "视频号" and video_id.startswith("wxexp_"))):
        # This is a reversible local key for an opaque platform export, not a
        # numeric object ID or evidence that the identifier stays stable.
        native = source.get("native_id")
        if (platform != "视频号" or source.get("platform_key", "wechat_channels") != "wechat_channels"
                or source.get("native_id_kind") != "platform_export_id"
                or source.get("video_id_kind") != "encoded_platform_export_id"
                or source.get("identity_stability_verified") is not False
                or not isinstance(native, str) or not native
                or source.get("platform_export_id") != native
                or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in native)):
            raise ValueError("Channels export identity requires explicit matching kinds and unverified stability")
        encoded = "wxexp_" + base64.urlsafe_b64encode(native.encode("utf-8")).decode("ascii").rstrip("=")
        if video_id != encoded or source.get("video_id", video_id) != video_id:
            raise ValueError("Channels local video_id must reversibly encode the exact platform export ID")
        original_input = source.get("input_url")
        original = urlsplit(canonical_url(original_input))
        if (original.port is not None or not (
                (original.hostname == "weixin.qq.com" and original.path.startswith("/sph/") and original.path[5:].strip("/"))
                or (original.hostname == "channels.weixin.qq.com"
                    and original.path in ("/web/pages/feed", "/finder-preview/pages/feed")))):
            raise ValueError("Channels export identity must preserve the original official source input URL")
        values = [v for k, v in parse_qsl(parts.query, keep_blank_values=True) if k == "eid"]
        tokens = [v for k, v in parse_qsl(parts.query, keep_blank_values=True) if k == "token"]
        if (parts.hostname != "channels.weixin.qq.com" or parts.port is not None
                or parts.path != "/finder-preview/pages/feed" or values != [native]
                or len(tokens) != 1 or not tokens[0]):
            raise ValueError("Channels export identity requires the exact official preview URL and one matching eid")
        for key in ("source_url", "canonical_url"):
            if key in source and canonical_url(source[key]) != url:
                raise ValueError("Channels source URL declarations conflict")
        return {"platform": platform, "url": url, "video_id": video_id, "id_field": id_field,
                "input_url": original_input, "native_id": native, "platform_export_id": native,
                "native_id_kind": "platform_export_id",
                "video_id_kind": "encoded_platform_export_id", "identity_stability_verified": False}
    tokens = {unquote(p) for p in parts.path.split("/") if p}
    tokens.update(v for _, v in parse_qsl(parts.query, keep_blank_values=True))
    if platform != "B站" and video_id not in tokens and not id_field:
        raise ValueError("Source URL lacks an exact video_id token; configure a dedicated source_identity.id_field")
    return {"platform": platform, "url": url, "video_id": video_id, "id_field": id_field}


def verify_source(identity: dict, *, platform: str, urls: list[str], stable_id: str | None = None) -> None:
    if platform != identity["platform"]:
        raise ValueError("Source record platform differs from the reviewed plan")
    if not urls:
        raise ValueError("Source record has no URL to bind to the reviewed plan")
    normalized = {canonical_url(url) for url in urls}
    if identity["platform"] == "B站":
        if {(bilibili_id(url), bilibili_part(url)) for url in normalized} != {(identity["video_id"], bilibili_part(identity['url']))}:
            raise ValueError("Source record URLs do not identify the exact B站 video_id and part")
    elif normalized != {identity["url"]}:
        raise ValueError("Source record URL differs from the reviewed plan")
    if identity.get("id_field") and stable_id != identity["video_id"]:
        raise ValueError("Source record stable ID differs from the reviewed plan")


def canonical_schema(raw: dict, profile: dict, label: str) -> dict:
    """Expose only profiled logical fields; names may differ in the target Base."""
    if not isinstance(profile, dict) or not profile:
        raise ValueError(f"Field mapping is missing: {label}")
    result, used = {}, set()
    for canonical, expected in profile.items():
        if not isinstance(expected, dict):
            raise ValueError(f"Invalid field mapping: {label}/{canonical}")
        target = expected.get("name", canonical)
        actual = raw.get(target)
        if (not actual or actual.get("id") != expected.get("id")
                or actual.get("type") != expected.get("type")):
            raise ValueError(f"Field mapping differs from schema: {label}/{canonical}")
        if actual["id"] in used:
            raise ValueError(f"Multiple logical fields map to one live field: {label}")
        used.add(actual["id"])
        result[canonical] = dict(actual)
    return result
