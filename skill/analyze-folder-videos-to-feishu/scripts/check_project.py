#!/usr/bin/env python3
"""Offline/live read-only preflight for a packaged video-analysis project."""
from __future__ import annotations

import argparse, json, os, re, shutil, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SHA = re.compile(r"^[0-9a-fA-F]{64}$")
ID = re.compile(r"^[A-Za-z0-9_-]{5,160}$")
OPEN_ID = re.compile(r"^ou_[A-Za-z0-9_-]+$")
UNVERIFIED = ["不证明用户浏览器/UI可访问或可编辑。", "不验证live字段类型、章节内容/样式或新视频事实/分析质量。"]

class CheckError(Exception):
    pass

def need(ok: bool, message: str) -> None:
    if not ok: raise CheckError(message)

def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise CheckError("project JSON missing or invalid") from None

def project_path(root: Path, value: str) -> Path:
    path = (root / value).resolve(); need(path.is_relative_to(root), "registry path escapes project root"); return path

def validate(root: Path, out: Path) -> tuple[dict, dict, dict]:
    cfg = load_json(root / "config" / "project.json")
    need(isinstance(cfg, dict) and cfg.get("schema_version") == 1, "unsupported project config")
    mode = cfg.get("mode", "feishu")
    need(mode in {"local", "feishu"}, "project mode must be local or feishu")
    base = cfg.get("base_id")
    need(cfg.get("identity") in {"bot", "user"}, "project identity must be bot or user")
    tables = cfg.get("tables", {})
    if mode == "local":
        need(base is None and not cfg.get("base_url") and not tables,
             "local project must not conceal a configured Feishu target; use mode=feishu")
        table_ids = {}
    else:
        need(isinstance(base, str) and ID.fullmatch(base), "invalid base_id; configure the Feishu schema first")
        need(isinstance(cfg.get("base_url"), str) and base in cfg["base_url"] and "/base/" in cfg["base_url"], "base URL mismatch")
        need(isinstance(cfg.get("app_id"), str) and cfg["app_id"].strip(), "app_id missing")
        names = ("videos", "materials", "products", "platforms", "segments")
        need(isinstance(tables, dict) and all(isinstance(tables.get(n), dict) for n in names),
             "table map incomplete; configure all five logical tables before Feishu checks")
        need(all(isinstance(meta, dict) for meta in tables.values()), "invalid configured table")
        table_ids = {n: meta.get("id") for n, meta in tables.items()}
        need(all(isinstance(v, str) and v.startswith("tbl") and ID.fullmatch(v) for v in table_ids.values()), "invalid table id")
        need(len(set(table_ids.values())) == len(table_ids), "duplicate configured table id")
    registry_path = project_path(root, cfg.get("registry_path", ""))
    need(out.resolve() != registry_path, "receipt may not overwrite registry")
    registry = load_json(registry_path)
    need(isinstance(registry, dict) and registry.get("base_id") == base, "registry/base mismatch")
    groups = registry.get("groups")
    need(isinstance(groups, dict), "registry groups missing")
    need(mode != "local" or not groups, "local-only registry may not contain Feishu document groups")
    docs, h1s, h2s, video_count = {}, set(), set(), 0
    for key, group in groups.items():
        parts = key.split("::") if isinstance(key, str) else []
        need(len(parts) == 3 and parts[0] == base and all(parts[1:]), "invalid registry group key")
        need(isinstance(group, dict), "invalid registry group")
        need("product_id" not in group or group["product_id"] == parts[1], "group product key mismatch")
        need("platform_id" not in group or group["platform_id"] == parts[2], "group platform key mismatch")
        token, title, videos = group.get("doc_token"), group.get("doc_title"), group.get("videos")
        need(isinstance(videos, dict), "group videos must be an object")
        if not videos and not token and not title:
            continue  # Fresh group awaiting explicit document creation/registration.
        need(isinstance(token, str) and ID.fullmatch(token) and token not in docs, "missing or duplicate doc token")
        need(isinstance(title, str) and title.strip(), "document title missing")
        docs[token] = {"title": title, "video_count": len(videos)}
        for vk, video in videos.items():
            need(isinstance(vk, str) and vk.strip() and isinstance(video, dict), "invalid video entry")
            h1, h2 = video.get("chapter_root_block_id"), video.get("segment_block_ids")
            need(isinstance(h1, str) and ID.fullmatch(h1) and h1 not in h1s | h2s, "missing or duplicate video H1")
            need(isinstance(h2, dict) and h2, "video H2 map missing")
            h1s.add(h1)
            for sid, bid in h2.items():
                need(isinstance(sid, str) and sid.strip() and isinstance(bid, str) and ID.fullmatch(bid), "invalid H2 mapping")
                need(bid not in h1s | h2s, "duplicate heading block ID")
                h2s.add(bid)
            ch = video.get("content_sha256")
            sh = video.get("source_sha256") or video.get("source_video_sha256")
            if not sh and isinstance(video.get("source"), dict):
                sh = video["source"].get("sha256")
            need(isinstance(ch, str) and SHA.fullmatch(ch) and isinstance(sh, str) and SHA.fullmatch(sh), "invalid content/source hash")
            need(isinstance(video.get("source_title"), str) and video["source_title"].strip(), "source title missing")
            url = video.get("source_url", "")
            need(isinstance(url, str) and (not url or url.startswith(("http://", "https://"))), "invalid source URL")
            video_count += 1
    owners, base_owner = cfg.get("document_owners", {}), cfg.get("expected_base_owner_id")
    need(base_owner is None or (isinstance(base_owner, str) and OPEN_ID.fullmatch(base_owner)), "invalid expected Base owner ID")
    need(isinstance(owners, dict) and set(owners) <= set(docs), "document owner map references an unregistered document")
    need(all(isinstance(v, str) and OPEN_ID.fullmatch(v) for v in owners.values()), "invalid document owner ID")
    network = cfg.get("network")
    need(isinstance(network, dict) and isinstance(network.get("ignore_proxy_env"), bool), "network proxy policy missing")
    details = {"docs": docs, "owners": owners, "base_owner": base_owner,
               "owner_checks_unconfigured": (["base"] if mode == "feishu" and base_owner is None else [])
                    + ["document:" + t for t in docs if t not in owners],
               "mode": mode, "table_ids": table_ids, "video_count": video_count}
    return cfg, registry, details

def child_env(ignore_proxy: bool) -> dict[str, str]:
    env = dict(os.environ)
    env.update(LARKSUITE_CLI_NO_UPDATE_NOTIFIER="1", LARKSUITE_CLI_NO_SKILLS_NOTIFIER="1")
    if ignore_proxy:
        for key in list(env):
            if key.upper() in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}:
                env.pop(key, None)
    return env

def api_error(result: dict, stderr: str, label: str) -> str:
    err = result.get("error") if isinstance(result.get("error"), dict) else result
    code, subtype = err.get("code", "unknown"), err.get("subtype", err.get("error_type", "unknown"))
    message = str(err.get("message", err.get("msg", stderr.strip() or "request rejected")))
    message = re.sub(r"(?i)ou_[A-Za-z0-9_-]+", "[USER_ID]", message)
    message = re.sub(r"(?i)(token|secret|authorization)\s*[:=]\s*\S+", r"\1=[REDACTED]", message)
    message = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[EMAIL]", message)
    message = re.sub(r"\b1[3-9]\d{9}\b", "[PHONE]", message)
    message = re.sub(r"https?://\S+", "[URL]", message)[:240]
    return f"CLI/API failure {label}: code={code}; subtype={subtype}; message={message}"

def call(cli: str, args: list[str], root: Path, env: dict, label: str, identity: str) -> dict:
    try:
        p = subprocess.run([cli, *args], cwd=root, env=env, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=45)
    except subprocess.TimeoutExpired:
        raise CheckError(f"CLI timeout: {label}") from None
    except OSError:
        raise CheckError(f"CLI unavailable: {label}") from None
    parsed = []
    for raw in (p.stdout, p.stderr):
        try:
            item = json.loads(raw)
            if isinstance(item, dict): parsed.append(item)
        except (json.JSONDecodeError, TypeError):
            pass
    result = next((x for x in parsed if x.get("ok") is False or "error" in x), parsed[0] if parsed else None)
    if not isinstance(result, dict):
        raise CheckError(f"CLI invalid JSON: {label}")
    if p.returncode != 0 or result.get("ok") is not True:
        raise CheckError(api_error(result, p.stderr, label))
    need(result.get("identity") == identity, f"unexpected API identity: {label}")
    data = result.get("data")
    need(isinstance(data, dict), f"ambiguous API response: {label}")
    return data

def read_tables(cli: str, base: str, root: Path, env: dict, identity: str) -> tuple[set[str], int]:
    found, offset = set(), 0
    for page in range(1000):
        data = call(cli, ["base", "+table-list", "--base-token", base, "--limit", "100", "--offset", str(offset), "--as", identity, "--format", "json"], root, env, "table-list", identity)
        items = data.get("tables", data.get("items"))
        need(isinstance(items, list), "ambiguous table-list page")
        ids = [x.get("id") or x.get("table_id") for x in items if isinstance(x, dict)]
        need(len(ids) == len(items) and all(isinstance(x, str) and x.startswith("tbl") for x in ids), "table item lacks stable ID")
        need(len(ids) == len(set(ids)) and not found.intersection(ids), "duplicate table IDs across pages")
        found.update(ids)
        more = data.get("has_more")
        if not isinstance(more, bool):
            total = data.get("total")
            need(isinstance(total, int) and total >= 0, "table pagination state ambiguous")
            more = offset + len(items) < total
        if not more:
            return found, page + 1
        need(bool(items), "table pagination made no progress")
        offset += len(items)
    raise CheckError("table pagination limit exceeded")

def read_metas(cli: str, requests: list[dict], root: Path, env: dict, identity: str) -> dict[str, dict]:
    metas = {}
    for start in range(0, len(requests), 50):
        body = json.dumps({"request_docs": requests[start:start + 50], "with_url": False}, separators=(",", ":"))
        data = call(cli, ["drive", "metas", "batch_query", "--data", body, "--user-id-type", "open_id", "--as", identity, "--format", "json"], root, env, "metadata batch", identity)
        need(not data.get("failed_list"), "metadata lookup failed")
        rows = data.get("metas")
        need(isinstance(rows, list), "metadata response has no metas list")
        for row in rows:
            need(isinstance(row, dict) and isinstance(row.get("doc_token"), str), "ambiguous metadata row")
            need(row["doc_token"] not in metas, "duplicate metadata token")
            metas[row["doc_token"]] = row
    return metas

def verify_app(cli: str, cfg: dict, root: Path, env: dict) -> bool:
    identity = cfg["identity"]
    try:
        p = subprocess.run([cli, "whoami", "--as", identity], cwd=root, env=env, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=45)
        data = json.loads(p.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        raise CheckError("whoami failed") from None
    need(p.returncode == 0 and isinstance(data, dict), "whoami failed")
    need(data.get("identity") == identity and data.get("available") is True, "configured identity unavailable")
    need(data.get("appId") == cfg["app_id"], "CLI appId/config mismatch")
    return True

def live(cli: str, root: Path, cfg: dict, details: dict) -> dict:
    need(details["mode"] == "feishu", "Feishu is unconfigured in local mode")
    identity = cfg["identity"]
    env = child_env(cfg["network"]["ignore_proxy_env"])
    app_match = verify_app(cli, cfg, root, env)
    tables, pages = read_tables(cli, cfg["base_id"], root, env, identity)
    need(set(details["table_ids"].values()) <= tables, "configured table missing from Base")
    tokens = list(details["docs"])
    requests = [{"doc_token": cfg["base_id"], "doc_type": "bitable"}] + [{"doc_token": t, "doc_type": "docx"} for t in tokens]
    metas = read_metas(cli, requests, root, env, identity)
    need(set(metas) == {cfg["base_id"], *tokens}, "metadata tokens mismatch config/registry")
    base_meta = metas[cfg["base_id"]]
    need(base_meta.get("doc_type") == "bitable", "Base type mismatch")
    if details["base_owner"] is not None:
        need(base_meta.get("owner_id") == details["base_owner"], "Base owner mismatch")
    for token in tokens:
        meta = metas[token]
        need(meta.get("doc_type") == "docx" and meta.get("title") == details["docs"][token]["title"], "document title/token mismatch")
        if token in details["owners"]:
            need(meta.get("owner_id") == details["owners"][token], "document owner mismatch")
        auth = call(cli, ["drive", "permission.members", "auth", "--action", "edit", "--token", token,
                          "--type", "docx", "--as", identity, "--format", "json"], root, env, "document edit auth", identity)
        need(auth.get("auth_result") is True, "configured identity edit permission missing or unverified")
    return {"cli_app_id_matches": app_match, "table_pages": pages, "tables_present": len(details["table_ids"]),
            "metadata_identity_verified": len(metas), "docx_edit_auth_verified": len(tokens),
            "base_owner_verified": details["base_owner"] is not None,
            "document_owners_verified": len(details["owners"]),
            "owner_checks_unconfigured": details["owner_checks_unconfigured"]}

def protected_output(root: Path, out: Path, cfg: dict | None) -> bool:
    target = out.resolve()
    if target == (root / "config" / "project.json").resolve(): return True
    if target.is_relative_to(root) and "state" in {part.lower() for part in target.relative_to(root).parts}: return True
    if cfg is not None:
        try:
            return any(target == project_path(root, cfg[key]) for key in ('registry_path', 'video_index_path')
                       if isinstance(cfg.get(key), str) and cfg[key])
        except CheckError: return False
    return False

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", required=True, help="project root containing config/project.json")
    ap.add_argument("--live", action="store_true", help="serial read-only Lark checks using the configured bot/user identity")
    ap.add_argument("--output", required=True, help="receipt path; relative to project root")
    args = ap.parse_args()
    root, out = Path(args.project).resolve(), Path(args.output)
    if not out.is_absolute():
        out = root / out
    try: guard_cfg = load_json(root / "config" / "project.json")
    except CheckError: guard_cfg = None
    if protected_output(root, out, guard_cfg):
        print("Receipt path may not overwrite config, registry, or state", file=sys.stderr); return 1
    receipt: dict[str, Any] = {"checked_at": datetime.now(timezone.utc).isoformat(), "status": "FAIL", "mode": "live_read_only" if args.live else "offline", "identity": None, "dependencies": {}, "checks": {}, "unverified": list(UNVERIFIED), "errors": []}
    code = 1
    try:
        need(root.is_dir(), "project root missing")
        cli, ffmpeg, ffprobe, node = (shutil.which(x) for x in ("lark-cli", "ffmpeg", "ffprobe", "node"))
        receipt["dependencies"] = {"python": sys.version.split()[0], "python_ok": sys.version_info >= (3, 10), "lark_cli": bool(cli), "ffmpeg": bool(ffmpeg), "ffprobe": bool(ffprobe), "node_optional": bool(node)}
        need(sys.version_info >= (3, 10), "Python 3.10+ required")
        cfg, registry, details = validate(root, out)
        receipt["identity"] = cfg["identity"]
        receipt["checks"]["offline_config_registry"] = "PASS"
        receipt["integration_configured"] = details["mode"] == "feishu"
        if details["owner_checks_unconfigured"]:
            receipt["unverified"].append("Owner expectations are unconfigured; no owner match is claimed.")
        if not args.live:
            receipt["unverified"].append("No live identity, access, owner, schema or remote content check was performed.")
        receipt["summary"] = {"table_count": len(details["table_ids"]), "document_count": len(details["docs"]), "video_count": details["video_count"]}
        if args.live:
            need(details["mode"] == "feishu", "Feishu is unconfigured in local mode")
            need(bool(cli), "lark-cli is required for --live")
            receipt["checks"]["live_read_only"] = live(cli, root, cfg, details)
        receipt["status"], code = "PASS", 0
    except CheckError as exc:
        receipt["errors"].append(str(exc))
    except Exception:
        receipt["errors"].append("unexpected local checker failure")
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        print("Could not write receipt", file=sys.stderr); return 1
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return code

if __name__ == "__main__":
    raise SystemExit(main())
