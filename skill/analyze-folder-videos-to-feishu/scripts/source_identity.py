"""Source identity and canonical-to-live field mapping; no network operations."""
from __future__ import annotations

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
