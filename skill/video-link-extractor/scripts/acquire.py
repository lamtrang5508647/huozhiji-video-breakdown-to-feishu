#!/usr/bin/env python3
"""Acquire source-bound video metadata/media; never equate a downloaded file with reviewed content."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
from urllib.parse import urlsplit, parse_qs

SKILL = Path(__file__).resolve().parents[1]
PLATFORMS = {
    "bilibili": ("bilibili.com", "b23.tv"),
    "douyin": ("douyin.com", "iesdouyin.com"),
    "xiaohongshu": ("xiaohongshu.com", "xhslink.com", "xhslink.cn"),
    "kuaishou": ("kuaishou.com", "gifshow.com"),
    "wechat_channels": ("channels.weixin.qq.com", "weixin.qq.com"),
}
LABELS = {"bilibili": "B站", "douyin": "抖音", "xiaohongshu": "小红书",
          "kuaishou": "快手", "wechat_channels": "视频号", "generic": "其他平台"}
EXTRACTOR_LABELS = {"Youtube": "YouTube", "Vimeo": "Vimeo", "TikTok": "TikTok"}


class AcquisitionError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def input_url(text: str) -> str:
    # Accept pasted share copy without guessing which of multiple links the user meant.
    urls = re.findall(r"https?://[^\s<>\"\u3000]+", text)
    urls = [url.rstrip("。，、；：！？）】》)]}>.,;!?") for url in urls]
    if len(urls) != 1:
        raise AcquisitionError("invalid_input", "Provide one video link or share text containing exactly one link")
    url = urls[0]
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise AcquisitionError("invalid_url", "Source links must use HTTPS and contain no URL credentials")
    if parsed.port not in (None, 443):
        raise AcquisitionError("invalid_url", "Source links must use the default HTTPS port")
    return url


def detect_platform(url: str) -> str:
    host = urlsplit(url).hostname.lower()
    for platform, domains in PLATFORMS.items():
        if any(host == domain or host.endswith("." + domain) for domain in domains):
            if platform == "wechat_channels" and host == "mp.weixin.qq.com":
                return "generic"  # An article is not a Channels video.
            return platform
    return "generic"


def bilibili_page(url: str) -> str:
    values = parse_qs(urlsplit(url).query, keep_blank_values=True).get("p", ["1"])
    if len(values) != 1 or not re.fullmatch(r"[1-9][0-9]*", values[0]):
        raise AcquisitionError("invalid_source_part", "Bilibili p must be one positive integer")
    return values[0]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(command: list[str], timeout: int) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, shell=False)
    except FileNotFoundError as exc:
        raise AcquisitionError("dependency_missing", f"Executable not found: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise AcquisitionError("timeout", "Acquisition or media validation exceeded its time limit") from exc


def _stop_owned_browser(process: subprocess.Popen) -> bool:
    """Terminate only the tree/group created by this Popen, then reap it."""
    cleaned = True
    try:
        if os.name == "nt":
            killed = subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
                                    timeout=10, shell=False)
            cleaned = killed.returncode == 0
        else:
            # start_new_session below makes this owned PID the process-group ID.
            os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass  # The owned group has already exited.
    except (OSError, subprocess.TimeoutExpired):
        cleaned = False
    if not cleaned:
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.communicate(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        cleaned = False
        try:
            process.kill()
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
        finally:
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
    return cleaned


def run_owned_browser(command: list[str], timeout: int) -> subprocess.CompletedProcess:
    """Give the Channels browser a hard deadline and own its cleanup boundary."""
    creation = ({"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
                if os.name == "nt" else {"start_new_session": True})
    try:
        # The provider contract is its JSON file. Discard console output so a
        # descendant cannot keep inherited pipes open after the owned root exits.
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   text=True, encoding="utf-8", errors="replace", shell=False, **creation)
    except FileNotFoundError:
        raise AcquisitionError("dependency_missing", "The Channels browser executable is unavailable") from None
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if not _stop_owned_browser(process):
            raise AcquisitionError("browser_cleanup_failed", "Browser timed out and its owned process tree could not be fully terminated") from None
        raise AcquisitionError("timeout", "Channels browser acquisition exceeded its time limit; its owned process tree was terminated") from None
    except BaseException:
        _stop_owned_browser(process)
        raise
    return subprocess.CompletedProcess(command, process.returncode, stdout or "", stderr or "")


def verify_media(path: Path, ffprobe: str, ffmpeg: str, timeout: int) -> dict:
    if not path.is_file() or path.stat().st_size == 0:
        raise AcquisitionError("media_missing", "Provider did not produce a nonempty local media file")
    before_hash = sha256(path)
    result = run([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], timeout)
    if result.returncode:
        raise AcquisitionError("invalid_media", "FFprobe could not read the media container")
    try:
        probe = json.loads(result.stdout)
        video = next(stream for stream in probe["streams"] if stream.get("codec_type") == "video"
                     and not stream.get("disposition", {}).get("attached_pic"))
        duration = float(video.get("duration") or probe.get("format", {}).get("duration") or 0)
        width, height = int(video.get("width", 0)), int(video.get("height", 0))
        if width <= 0 or height <= 0 or not math.isfinite(duration) or duration <= 0:
            raise ValueError("No usable dimensions/duration")
    except (ValueError, TypeError, KeyError, StopIteration) as exc:
        raise AcquisitionError("invalid_video", "A real video stream with dimensions and positive duration is required") from exc
    index = video.get("index")
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise AcquisitionError("invalid_video", "FFprobe did not identify the actual video stream index")
    decoded = run([ffmpeg, "-nostdin", "-v", "error", "-xerror", "-i", str(path),
                   "-map", f"0:{index}", "-map", "0:a?", "-f", "null", "-"], timeout)
    if decoded.returncode:
        raise AcquisitionError("decode_failed", "Complete video/audio decoding failed; the file is not marked ready")
    after_hash = sha256(path)
    if before_hash != after_hash:
        raise AcquisitionError("media_changed", "Media bytes changed during probe/decode; validation must be repeated")
    return {"sha256": after_hash, "bytes": path.stat().st_size, "duration_seconds": duration,
            "width": width, "height": height, "video_codec": video.get("codec_name"),
            "has_audio": any(stream.get("codec_type") == "audio" for stream in probe["streams"]),
            "complete_decode_verified": True}


def wechat_export_key(native_id: str) -> str:
    if (not isinstance(native_id, str) or not native_id
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in native_id)):
        raise AcquisitionError("source_mismatch", "Channels export ID must be nonempty opaque text without whitespace or controls")
    key = "wxexp_" + base64.urlsafe_b64encode(native_id.encode("utf-8")).decode("ascii").rstrip("=")
    if len(key) > 160:
        raise AcquisitionError("source_mismatch", "Channels export ID exceeds the supported reversible local key length")
    return key


def validate_wechat_export(data: dict, url: str, canonical: str) -> None:
    identity, evidence = data.get("native_id"), data.get("identity_evidence")
    if (data.get("platform") != "wechat_channels" or data.get("native_id_kind") != "platform_export_id"
            or data.get("platform_export_id") != identity
            or data.get("identity_stability_verified") is not False or not isinstance(evidence, dict)
            or evidence.get("kind") != "tencent_same_origin_request_chain"
            or evidence.get("parse_input_url") != url or evidence.get("parse_playable_url") != canonical
            or evidence.get("parse_endpoint") != "https://yuanbao.tencent.com/api/weixin/get_parse_result"
            or evidence.get("feed_endpoint") != "https://channels.weixin.qq.com/finder-preview/api/feed/get_feed_info"
            or evidence.get("play_token_matched") is not True
            or any(evidence.get(key) != identity for key in
                   ("play_eid", "feed_export_id", "dynamic_export_id"))):
        raise AcquisitionError("source_mismatch", "Channels export ID requires the exact Tencent request/response identity chain")
    for key in ("parse_http_status", "feed_http_status"):
        if type(evidence.get(key)) is not int or not 200 <= evidence[key] < 300:
            raise AcquisitionError("source_mismatch", "Tencent identity evidence requires actual successful HTTP statuses")
    for key in ("parse_code", "feed_err_code", "business_error_type"):
        if type(evidence.get(key)) is not int or evidence[key] != 0:
            raise AcquisitionError("source_mismatch", "Tencent identity evidence requires explicit successful business codes")
    wechat_export_key(identity)
    parsed = urlsplit(canonical)
    query = parse_qs(parsed.query, keep_blank_values=True)
    tokens = query.get("token", [])
    if (canonical != data.get("canonical_url") or parsed.scheme != "https"
            or parsed.hostname != "channels.weixin.qq.com" or parsed.port not in (None, 443)
            or parsed.username is not None or parsed.password is not None or parsed.fragment
            or any(c.isspace() or ord(c) < 32 for c in canonical)
            or parsed.path != "/finder-preview/pages/feed"
            or query.get("eid") != [identity] or len(tokens) != 1 or not tokens[0]):
        raise AcquisitionError("source_mismatch", "Channels export ID requires the exact official preview URL and one matching eid")


def validate_wechat_options(args, platform: str) -> None:
    browser = getattr(args, "wechat_browser", False)
    consent = getattr(args, "allow_tencent_parse", False)
    manual = getattr(args, "manual_login", False)
    if (browser or consent or manual) and platform != "wechat_channels":
        raise AcquisitionError("invalid_options", "Channels browser options require a Channels source")
    if (consent or manual) and not browser:
        raise AcquisitionError("invalid_options", "Tencent parsing and manual login require --wechat-browser")
    if browser:
        if getattr(args, "backend_url", None) or getattr(args, "online_endpoint", None) or getattr(args, "allow_online_parser", False):
            raise AcquisitionError("invalid_options", "Choose the Channels browser or a configured provider, without mixing them")
        if not consent:
            raise AcquisitionError("tencent_parse_not_authorized", "--wechat-browser requires explicit --allow-tencent-parse")
        if type(args.timeout) is not int or not 0 < args.timeout * 1000 <= 2147483647:
            raise AcquisitionError("invalid_options", "Channels browser timeout must be a positive whole-second timer budget")


def validate_provider(data: dict, url: str, platform: str, output: Path, download: bool) -> dict:
    if data.get("status") not in ("metadata_ready", "downloaded"):
        raise AcquisitionError(data.get("error_code") or data.get("reason") or "provider_failed",
                               "Provider could not establish usable source metadata; see the local provider result")
    if data.get("input_url") != url or data.get("platform") != platform or data.get("identity_verified") is not True:
        raise AcquisitionError("source_mismatch", "Provider metadata is not bound to the requested link/platform")
    identity, title = data.get("native_id"), data.get("title")
    if not isinstance(identity, str) or not identity.strip() or not isinstance(title, str) or not title.strip():
        raise AcquisitionError("metadata_missing", "Actual platform video ID and title/caption are required")
    canonical = input_url(data.get("canonical_url", ""))
    if detect_platform(canonical) != platform:
        raise AcquisitionError("source_mismatch", "Canonical URL platform differs from the requested platform")
    if data.get("title_kind") not in ("title", "caption", "extractor_title"):
        raise AcquisitionError("metadata_missing", "Title must come from source metadata, not a filename/template")
    evidence = data.get("identity_evidence")
    if (data.get("native_id_kind") == "platform_export_id" or "platform_export_id" in data
            or (isinstance(evidence, dict) and evidence.get("kind") == "tencent_same_origin_request_chain")
            or (platform == "wechat_channels" and urlsplit(canonical).path == "/finder-preview/pages/feed")):
        validate_wechat_export(data, url, canonical)
    if platform == "generic":
        extractor = data.get("extractor")
        if not isinstance(extractor, str) or not extractor.strip() or extractor.lower() == "generic":
            raise AcquisitionError("identity_unverified", "Other sites require a dedicated extractor identity")
    if platform in ("bilibili", "douyin", "xiaohongshu", "kuaishou"):
        # Short links must be resolved to a target URL that contains the actual platform ID.
        parsed = urlsplit(canonical)
        tokens = re.findall(r"[A-Za-z0-9_-]+", parsed.path)
        tokens += [value for values in parse_qs(parsed.query).values() for value in values]
        if identity not in tokens:
            raise AcquisitionError("source_mismatch", "Canonical video URL does not contain the extracted platform video ID")
        if platform == "bilibili":
            expected_page = bilibili_page(url)
            actual_page = bilibili_page(canonical)
            has_explicit_part = "p" in parse_qs(urlsplit(url).query, keep_blank_values=True)
            has_direct_id = re.search(r"/video/(?:BV[A-Za-z0-9]+|av[0-9]+)(?:/|$)", urlsplit(url).path)
            if (has_explicit_part or has_direct_id) and expected_page != actual_page:
                raise AcquisitionError("source_mismatch", "Bilibili multi-part page changed during acquisition")
    if download:
        if data.get("status") != "downloaded":
            raise AcquisitionError("media_missing", "Download requested, but provider produced metadata only")
        media = Path(data.get("media_path", "")).resolve()
        if not media.is_relative_to(output.resolve()) or media == output.resolve():
            raise AcquisitionError("media_path_escape", "Provider media must be a file inside this acquisition directory")
        data["media_path"] = str(media)
    return data


def ytdlp_provider(args, url: str, output: Path) -> dict:
    executable = args.ytdlp or "yt-dlp"
    command = [executable, "--ignore-config", "--no-cache-dir", "--no-playlist", "--no-progress",
               "--dump-single-json", "--restrict-filenames", "--socket-timeout", "30"]
    if args.download:
        command += ["--no-simulate", "--write-info-json", "--merge-output-format", "mp4",
                    "--ffmpeg-location", args.ffmpeg, "-o", str(output / "source.%(ext)s")]
    else:
        command += ["--skip-download"]
    command += ["--", url]
    result = run(command, args.timeout)
    if result.returncode:
        # Do not echo provider stderr, which can contain cookies, signed URLs or session values.
        raise AcquisitionError("extraction_failed", "yt-dlp could not extract this link; login/restrictions or unsupported site may be the cause")
    try:
        info = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AcquisitionError("provider_contract", "yt-dlp did not return one video JSON record") from exc
    if info.get("_type") in ("playlist", "multi_video") or info.get("entries"):
        raise AcquisitionError("ambiguous_source", "Playlists and multi-video results require a specific video URL")
    extractor = info.get("extractor_key")
    if not isinstance(extractor, str) or not extractor.strip() or extractor.lower() == "generic":
        raise AcquisitionError("identity_unverified", "Generic webpage/file extraction cannot establish a native video ID and original title")
    native_id = info.get("id")
    canonical = info.get("webpage_url")
    if (not isinstance(native_id, str) or not native_id.strip() or not isinstance(canonical, str)
            or not isinstance(info.get("title"), str) or not info["title"].strip()):
        raise AcquisitionError("metadata_missing", "Dedicated extractor must return actual native ID, title and webpage URL")
    canonical = input_url(canonical)
    canonical_parts = urlsplit(canonical)
    identity_tokens = set(canonical_parts.path.split("/"))
    identity_tokens.update(value for values in parse_qs(canonical_parts.query).values() for value in values)
    if native_id not in identity_tokens:
        raise AcquisitionError("identity_unverified", "Dedicated extractor's canonical URL does not bind the exact native video ID")
    if info.get("original_url", canonical) != url:
        raise AcquisitionError("source_mismatch", "Dedicated extractor did not preserve the requested source URL")
    media = None
    if args.download:
        paths = [Path(item["filepath"]) for item in info.get("requested_downloads", []) if item.get("filepath")]
        paths += [output / ("source" + ext) for ext in (".mp4", ".mkv", ".webm", ".flv", ".mov")]
        paths = list(dict.fromkeys(path.resolve() for path in paths if path.is_file()))
        if len(paths) != 1:
            raise AcquisitionError("ambiguous_media", "Expected one completed media file; incomplete/multiple results are rejected")
        media = paths[0]
    return {"status": "downloaded" if args.download else "metadata_ready", "platform": "generic",
            "input_url": url, "canonical_url": canonical,
            "native_id": native_id, "title": info["title"],
            "title_kind": "extractor_title", "extractor": extractor,
            "identity_verified": True, "media_path": str(media) if media else None}


def provider(args, url: str, platform: str, output: Path) -> dict:
    validate_wechat_options(args, platform)
    if platform == "generic":
        data = ytdlp_provider(args, url, output)
        write_json(output / "provider-result.json", data)
        return data
    wechat_browser = platform == "wechat_channels" and getattr(args, "wechat_browser", False)
    if wechat_browser:
        command = ["node", str(SKILL / "scripts/wechat_browser_acquire.mjs"),
                   "--allow-tencent-parse", "--ffmpeg", args.ffmpeg,
                   "--timeout-ms", str(args.timeout * 1000)]
        for flag, value in (("--browser", args.browser), ("--storage-state", args.storage_state)):
            if value:
                command += [flag, value]
        if args.headed:
            command.append("--headed")
        if args.manual_login:
            command.append("--manual-login")
    elif platform == "wechat_channels":
        command = [sys.executable, "-B", str(SKILL / "scripts/wechat_acquire.py")]
        for flag, value in (("--backend-url", args.backend_url), ("--online-endpoint", args.online_endpoint)):
            if value:
                command += [flag, value]
        if args.allow_online_parser:
            command.append("--allow-online-parser")
    else:
        command = ["node", str(SKILL / "scripts/browser_acquire.mjs"), "--platform", platform,
                   "--ffmpeg", args.ffmpeg]
        for flag, value in (("--browser", args.browser), ("--storage-state", args.storage_state)):
            if value:
                command += [flag, value]
        if args.headed:
            command.append("--headed")
    command += ["--url", url, "--output", str(output), "--download" if args.download else "--metadata-only"]
    result = (run_owned_browser if wechat_browser else run)(command, args.timeout)
    file = output / "provider-result.json"
    if not file.is_file():
        raise AcquisitionError("provider_contract", "Provider did not create its metadata result; check runtime dependencies")
    data = json.loads(file.read_text(encoding="utf-8-sig"))
    if result.returncode and data.get("status") in ("metadata_ready", "downloaded"):
        raise AcquisitionError("provider_failed", "Provider exit code and success status disagree")
    if wechat_browser and data.get("status") in ("metadata_ready", "downloaded"):
        # Route knowledge prevents a browser receipt from dropping its kind and
        # pretending to satisfy the older native-query provider contract.
        validate_wechat_export(data, url, input_url(data.get("canonical_url", "")))
    return data


def acquire(args) -> dict:
    url = input_url(args.url)
    detected = detect_platform(url)
    platform = detected if args.platform == "auto" else args.platform
    if platform != detected:
        raise AcquisitionError("platform_mismatch", "Explicit platform differs from the recognized source host")
    validate_wechat_options(args, platform)
    if platform == "bilibili":
        bilibili_page(url)
    output = Path(args.output).expanduser().resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise AcquisitionError("output_exists", "Use a new or empty output directory; existing source files are preserved")
    output.mkdir(parents=True, exist_ok=True)
    try:
        data = validate_provider(provider(args, url, platform, output), url, platform, output, args.download)
        platform_label = LABELS[platform] if platform != "generic" else EXTRACTOR_LABELS.get(data["extractor"], data["extractor"])
        receipt = {"schema_version": 1, **data, "platform_label": platform_label,
                   "status": "metadata_ready", "media_verified": False,
                   "transcript_status": "not_started", "content_reviewed": False,
                   "original_upload_quality_verified": False}
        if args.download:
            receipt["media"] = verify_media(Path(data["media_path"]), args.ffprobe, args.ffmpeg, args.timeout)
            receipt.update(status="ready", media_verified=True)
            source = {"schema_version": 1, "platform": platform_label, "platform_key": platform,
                      "video_id": data["native_id"], "title": data["title"], "title_kind": data["title_kind"],
                      "url": data["canonical_url"], "source_url": data["canonical_url"], "input_url": url,
                      "source_path": data["media_path"], "source_sha256": receipt["media"]["sha256"],
                      "duration_seconds": receipt["media"]["duration_seconds"],
                      "acquisition_receipt": str(output / "acquisition.json"),
                      "transcript_status": "not_started", "content_reviewed": False}
            if data.get("native_id_kind") == "platform_export_id":
                source.update(video_id=wechat_export_key(data["native_id"]),
                              video_id_kind="encoded_platform_export_id", native_id=data["native_id"],
                              native_id_kind="platform_export_id", platform_export_id=data["native_id"],
                              identity_stability_verified=False)
            write_json(output / "source.json", source)
        write_json(output / "acquisition.json", receipt)
        return receipt
    except (AcquisitionError, ValueError, OSError) as exc:
        receipt = {"schema_version": 1, "status": "blocked", "platform": platform,
                   "input_url": url, "error_code": getattr(exc, "code", "provider_contract"),
                   "message": str(exc), "media_verified": False, "content_reviewed": False}
        write_json(output / "acquisition.json", receipt)
        raise


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--url", required=True, help="One HTTPS video link or pasted share text")
    cli.add_argument("--output", required=True, help="New or empty acquisition directory")
    cli.add_argument("--platform", choices=("auto", *PLATFORMS, "generic"), default="auto")
    mode = cli.add_mutually_exclusive_group()
    mode.add_argument("--metadata-only", action="store_true", help="Default: metadata probe without media download")
    mode.add_argument("--download", action="store_true", help="Download and fully decode media before ready")
    cli.add_argument("--browser", help="Explicit local Chromium/Chrome/Edge executable")
    cli.add_argument("--storage-state", help="Explicit caller-owned Playwright login state; never exported automatically")
    cli.add_argument("--headed", action="store_true", help="Explicitly allow a visible browser; never bypass captcha")
    cli.add_argument("--wechat-browser", action="store_true", help="Use the explicit Tencent browser flow for a Channels source")
    cli.add_argument("--allow-tencent-parse", action="store_true", help="Consent to send this one Channels link to Tencent Yuanbao")
    cli.add_argument("--manual-login", action="store_true", help="Allow manual login in the Channels browser context; never export it")
    cli.add_argument("--backend-url", help="Configured local Channels metadata/download backend")
    cli.add_argument("--online-endpoint", help="Explicit third-party Channels parser; off by default")
    cli.add_argument("--allow-online-parser", action="store_true", help="Consent to send this link to the supplied parser endpoint")
    cli.add_argument("--ffmpeg", default="ffmpeg")
    cli.add_argument("--ffprobe", default="ffprobe")
    cli.add_argument("--ytdlp", help="Optional yt-dlp executable for its other dedicated site extractors")
    cli.add_argument("--timeout", type=int, default=600, help="Provider and complete decode timeout in seconds")
    return cli


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    if args.timeout < 1:
        parser().error("--timeout must be positive")
    try:
        receipt = acquire(args)
        # Full metadata stays local. Console output excludes media/CDN URLs and authentication values.
        print(json.dumps({"status": receipt["status"], "platform": receipt["platform"],
                          "output": str(Path(args.output).resolve()), "media_verified": receipt["media_verified"]},
                         ensure_ascii=False))
        return 0
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": "blocked", "error_code": getattr(exc, "code", "provider_contract"),
                          "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
