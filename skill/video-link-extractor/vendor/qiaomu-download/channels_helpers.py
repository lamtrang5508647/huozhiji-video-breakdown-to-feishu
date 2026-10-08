"""Restricted helpers adapted from qiaomu-download; see LICENSE and UPSTREAM.md."""
from urllib.parse import urlsplit, urlunsplit

MEDIA_HOST_SUFFIXES = ("qq.com", "weixin.qq.com", "gtimg.com", "qpic.cn")


def normalize_share_url(value: str) -> str:
    """Validate a public Channels share/page URL, without discovering a session."""
    if not isinstance(value, str) or any(ord(c) < 32 for c in value):
        raise ValueError("invalid_source_url")
    parsed = urlsplit(value.strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme.lower() != "https" or parsed.username or parsed.password or parsed.port:
        raise ValueError("invalid_source_url")
    if host == "weixin.qq.com":
        if not parsed.path.startswith("/sph/") or not parsed.path[5:].strip("/"):
            raise ValueError("invalid_source_url")
    elif host == "channels.weixin.qq.com":
        if not parsed.path or parsed.path == "/" or "/api/" in parsed.path:
            raise ValueError("invalid_source_url")
    else:
        raise ValueError("invalid_source_url")
    return urlunsplit(("https", host, parsed.path, parsed.query, ""))


def validated_media_url(value: str) -> str:
    """Require an HTTPS Tencent media host, including every redirect hop."""
    if not isinstance(value, str) or any(ord(c) < 32 for c in value):
        raise ValueError("unsafe_media_url")
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host or parsed.username or parsed.password or parsed.port or parsed.fragment:
        raise ValueError("unsafe_media_url")
    if not any(host == suffix or host.endswith("." + suffix) for suffix in MEDIA_HOST_SUFFIXES):
        raise ValueError("unsafe_media_url")
    return value
