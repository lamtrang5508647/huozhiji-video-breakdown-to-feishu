#!/usr/bin/env python3
"""Local evidence-bound creative cards. No AI service, publication, or factual certification.

The JSON sidecar is the editable source of truth. Existing analysis manifests are
read-only dependencies. A passing validation checks supplied structure and hashes;
it never certifies speech, meaning, product effects, comment authenticity or results.
"""
from __future__ import annotations

import argparse
import copy
import csv
from datetime import datetime
import hashlib
import html
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

from script_shot_alignment import build_alignment
from source_evidence import validate_source_evidence

SCHEMA_VERSION = "1.0"
BUNDLE_NAME = "creative-workflow.json"
NOTICE = "程序只核对结构、来源引用和文件哈希；不代表商品事实、原声听校、语义判断、评论真实性或投放效果已核实。所有测试卡仍未测试。"
SYNTHETIC_NOTICE = "原创合成教学示例：商品、资料、场景、评论均为虚构，未使用真实商品评价或投放指标。"
COLLECTIONS = ("products", "sources", "facts", "claims", "evidence", "decisions", "comments", "expression_modules", "topics", "variants")
CARD_TYPES = ("topic", "script", "shooting", "test")
SHOOT_FIELDS = ("subject", "action", "framing", "angle", "movement", "lighting", "background", "props", "focus", "composition", "start_state", "end_state")
TEST_FIELDS = ("hypothesis", "primary_metric", "metric_definition", "comparison_window", "control_notes", "success_criterion", "stopping_rule", "confounders")
ACTIVE_HTML = re.compile(r"<\s*/?\s*(?:script|iframe|object|embed|link|meta|base|style|form|svg|math)\b|\bon[a-z]+\s*=|(?:javascript|vbscript)\s*:|data\s*:\s*text/html", re.I)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def text(value, label):
    require(isinstance(value, str) and bool(value.strip()), f"{label} requires nonempty text")
    return value


def object_value(value, label):
    require(isinstance(value, dict), f"{label} must be an object")
    return value


def text_list(value, label, nonempty=False):
    require(isinstance(value, list) and all(isinstance(x, str) and x.strip() for x in value), f"{label} must be a text array")
    require(not nonempty or bool(value), f"{label} must not be empty")
    require(len(value) == len(set(value)), f"{label} has duplicate IDs or values")
    return value


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def valid_hash(value, label):
    require(isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None, f"{label} must be lowercase SHA-256")
    return value


def read_json(path):
    def reject_pairs(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    def reject_constant(value):
        raise ValueError(f"Nonfinite JSON number: {value}")
    return json.loads(Path(path).read_text(encoding="utf-8-sig"), object_pairs_hook=reject_pairs, parse_constant=reject_constant)


def save_json(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def file_ref(base, locator, digest, label):
    path = (Path(base) / text(locator, label + ".path")).resolve()
    require(path.is_file(), f"{label} file does not exist: {path}")
    require(sha256_file(path) == valid_hash(digest, label + ".sha256"), f"{label} hash mismatch: {path}")
    return path


def safe_url(value, label):
    value = text(value, label)
    parts = urlsplit(value)
    require(parts.scheme in {"http", "https"} and bool(parts.hostname) and not parts.username and not parts.password,
            f"{label} requires an HTTP(S) URL without credentials")
    return value


def check_safe_content(value):
    if isinstance(value, str):
        require(ACTIVE_HTML.search(html.unescape(value)) is None, "Unsafe active HTML or script URL in bundle")
    elif isinstance(value, dict):
        for key, item in value.items():
            check_safe_content(key)
            check_safe_content(item)
    elif isinstance(value, list):
        for item in value:
            check_safe_content(item)


def index_items(items, label, globally_seen):
    require(isinstance(items, list), f"{label} must be an array")
    result = {}
    for item in items:
        object_value(item, label)
        key = text(item.get("id"), label + ".id")
        require(key not in globally_seen, f"Duplicate id: {key}")
        globally_seen.add(key)
        result[key] = item
    return result


def validate_bundle(bundle, base_dir):
    """Validate read-only dependencies and associations; return explicit human review gaps."""
    object_value(bundle, "bundle")
    check_safe_content(bundle)
    require(bundle.get("schema_version") == SCHEMA_VERSION, "Unsupported creative schema_version")
    text(bundle.get("bundle_id"), "bundle_id")
    require(type(bundle.get("revision")) is int and bundle["revision"] >= 1, "revision must be a positive integer")
    require(type(bundle.get("synthetic")) is bool, "synthetic must be boolean")
    require(bundle.get("state") in {"draft", "ready_for_review"}, "state must be draft or ready_for_review")
    text(bundle.get("notice"), "notice")
    seen = set()
    indexes = {key: index_items(bundle.get(key), key, seen) for key in COLLECTIONS}
    require(bool(indexes["products"]), "At least one product is required")
    for product in indexes["products"].values():
        text(product.get("name"), "product.name")
    for collection in COLLECTIONS[1:]:
        for item in indexes[collection].values():
            require(item.get("product_id") in indexes["products"], f"{collection} references unknown product_id")
    cards = object_value(bundle.get("cards"), "cards")
    require(set(cards) == set(CARD_TYPES), "cards must contain topic, script, shooting and test")
    card_indexes = {key: index_items(cards[key], f"cards.{key}", seen) for key in CARD_TYPES}
    for values in card_indexes.values():
        for item in values.values():
            require(item.get("product_id") in indexes["products"], "card references unknown product_id")

    def refs(item, field, collection, nonempty=False):
        values = text_list(item.get(field), field, nonempty)
        for ref_id in values:
            target = indexes[collection].get(ref_id)
            require(target is not None, f"{field} references missing {collection}: {ref_id}")
            require(target["product_id"] == item["product_id"], f"{field} cross-product binding: {ref_id}")
        return values

    def one_ref(item, field, collection):
        target = indexes[collection].get(item.get(field))
        require(target is not None, f"{field} references missing {collection}")
        require(target["product_id"] == item["product_id"], f"{field} cross-product binding")
        return target

    gaps = []
    manifests = {}
    source_checks = []
    require(bundle.get("source_status") == ("provided" if indexes["sources"] else "not_provided"), "source_status must explicitly match source availability")
    if not indexes["sources"]:
        gaps.append("未提供源片拆解，仅可制作本品创作草稿；没有原片主张或镜头证据。")
    for source_id, source in indexes["sources"].items():
        path = file_ref(base_dir, source.get("manifest_path"), source.get("manifest_sha256"), "source manifest")
        manifest = object_value(read_json(path), "source manifest")
        aligned = build_alignment(manifest)
        source_info = manifest["source"]
        require(source.get("source_sha256") == source_info["sha256"], "Source SHA mismatch")
        require(source.get("video_id") == manifest["video_id"], "Source video_id mismatch")
        require(source.get("observed_brand") == source_info.get("visible_brand", "未记录；不得由产品归属推断原片品牌"),
                "Source observed_brand differs from the bound original manifest")
        segment_map = {s["id"]: s for s in manifest["segments"]}
        timeline = {row["shot_id"]: {"start_ms": row["source_shot_start_ms"], "end_ms": row["source_shot_end_ms"]} for row in aligned["rows"]}
        timeline_status = "not_provided"
        if source_info.get("timeline_manifest_path"):
            timeline_path = file_ref(path.parent, source_info["timeline_manifest_path"], source_info.get("timeline_manifest_sha256"), "source timeline")
            timeline_data = read_json(timeline_path)
            require(timeline_data.get("video_id") == manifest["video_id"], "Timeline video_id mismatch")
            require(timeline_data.get("source", {}).get("sha256") == source_info["sha256"], "Timeline source hash mismatch")
            actual_shots = {s["id"]: s for s in timeline_data["segments"]}
            require(len(actual_shots) == len(timeline_data["segments"]), "Duplicate timeline shot IDs")
            for sid, shot in timeline.items():
                require(sid in actual_shots and all(actual_shots[sid].get(k) == shot[k] for k in ("start_ms", "end_ms")), "Manifest shot interval differs from actual timeline")
            timeline_status = "hash_checked"
        media_status = "not_provided"
        if source_info.get("path"):
            file_ref(path.parent, source_info["path"], source_info["sha256"], "source media")
            media_status = "bytes_hash_checked_no_decode_or_listening"
        source_checks.append({"source_id": source_id, "manifest": "hash_checked", "media": media_status, "timeline": timeline_status})
        manifests[source_id] = (manifest, segment_map, timeline)

    def source_ref(item):
        ref = object_value(item.get("source_ref"), "source_ref")
        sid = ref.get("source_id")
        require(sid in manifests, "source_ref references missing source")
        source = indexes["sources"][sid]
        require(source["product_id"] == item["product_id"], "source_ref cross-product binding")
        for key in ("manifest_sha256", "source_sha256"):
            require(ref.get(key) == source[key], f"source_ref {key} mismatch")
        manifest, segments, timeline = manifests[sid]
        segment = segments.get(ref.get("segment_id"))
        require(segment is not None, "source_ref references missing segment")
        binding = {"source_video_id": source["video_id"], "source_sha256": ref["source_sha256"],
                   "source_shot_ids": ref.get("source_shot_ids"), "start_ms": ref.get("start_ms"),
                   "end_ms": ref.get("end_ms"), "description": "Source binding only"}
        validate_source_evidence({**segment, "source_evidence": [binding]}, manifest, timeline)
        return manifest, segment

    for fact in indexes["facts"].values():
        for field in ("statement", "conditions"):
            text(fact.get(field), "fact." + field)
        require(fact.get("status") in {"pending", "provided", "synthetic"}, "Product fact status cannot assert verified truth")
        source = object_value(fact.get("source"), "fact.source")
        kind = source.get("kind")
        require(kind in {"local_file", "url", "not_provided", "synthetic"}, "Product facts require their own documentary sources, not source claims")
        if kind == "local_file":
            file_ref(base_dir, source.get("path"), source.get("sha256"), "fact source")
            text(source.get("locator"), "fact source page or locator")
        elif kind == "url":
            safe_url(source.get("url"), "fact source URL")
            text(source.get("locator"), "fact source locator")
            gaps.append(f"{fact['id']}：网页来源仅记录链接，未获取或核实正文。")
        elif kind == "synthetic":
            require(bundle["synthetic"] and fact["status"] == "synthetic", "Synthetic facts require an explicitly synthetic bundle")
            text(source.get("description"), "synthetic fact source")
        else:
            require(fact["status"] == "pending", "Missing fact source must remain pending")
        require(fact["status"] != "synthetic" or kind == "synthetic", "Synthetic fact source mismatch")
        if fact["status"] == "pending":
            gaps.append(f"{fact['id']}：本品事实待补资料，不能当成已知卖点。")

    for evidence in indexes["evidence"].values():
        require(evidence.get("kind") == "source_material", "Evidence must be source material; generated visuals are not product proof")
        require(evidence.get("verification_status") in {"unverified", "source_material_only"}, "Evidence cannot assert factual verification")
        for key in ("description", "support_scope", "limitations"):
            text(evidence.get(key), "evidence." + key)
        source_ref(evidence)
    for claim in indexes["claims"].values():
        require(claim.get("origin") == "source_author", "Source claim must remain attributed to source_author")
        require(claim.get("verification_status") == "unverified", "Source claim is not verified product fact")
        manifest, segment = source_ref(claim)
        quote = text(claim.get("exact_text"), "claim.exact_text")
        pointer = text(claim.get("source_pointer"), "claim.source_pointer")
        position = manifest["segments"].index(segment)
        prefix = f"/segments/{position}/"
        require(re.fullmatch(re.escape(prefix) + r"(?:source_claims(?:/[A-Za-z0-9_~]+)*|source_excerpt/items/(?:0|[1-9][0-9]*)/text)", pointer) is not None,
                "Source claim pointer must identify original claim or excerpt in its own segment")
        current = manifest
        try:
            for token in pointer.lstrip("/").split("/"):
                token = token.replace("~1", "/").replace("~0", "~")
                if isinstance(current, list):
                    require(re.fullmatch(r"0|[1-9][0-9]*", token) is not None, "Source claim array pointer requires canonical nonnegative index")
                    current = current[int(token)]
                else:
                    current = current[token]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ValueError("Invalid source claim pointer") from exc
        require(isinstance(current, str) and current == quote, "Source claim exact text differs from original manifest")
        if "/source_excerpt/items/" in pointer:
            item_position = int(pointer.split("/")[-2])
            excerpt_item = segment["source_excerpt"]["items"][item_position]
            require(excerpt_item["shot_id"] in claim["source_ref"]["source_shot_ids"], "Source claim excerpt belongs to another shot")
        evidence_ids = refs(claim, "evidence_ids", "evidence")
        text(claim.get("support_scope"), "claim.support_scope")
        text_list(claim.get("remaining_objections"), "claim.remaining_objections", True)
        for eid in evidence_ids:
            er = indexes["evidence"][eid]["source_ref"]
            cr = claim["source_ref"]
            require(all(er[k] == cr[k] for k in ("source_id", "source_sha256", "manifest_sha256", "segment_id")), "Claim evidence must bind to the same source and segment")
        if not evidence_ids:
            gaps.append(f"{claim['id']}：原作者主张尚无对应展示材料记录。")

    for decision in indexes["decisions"].values():
        text(decision.get("need_moment"), "decision.need_moment")
        text(decision.get("buyer_question"), "decision.buyer_question")
        refs(decision, "claim_ids", "claims")
        refs(decision, "evidence_ids", "evidence")
        text_list(decision.get("remaining_objections"), "decision.remaining_objections", True)
    for comment in indexes["comments"].values():
        quote = text(comment.get("text"), "comment.text")
        text(comment.get("interpretation"), "comment.interpretation")
        provenance = object_value(comment.get("provenance"), "comment.provenance")
        require(provenance.get("kind") in {"real_user", "synthetic"}, "Comments need real_user or synthetic provenance")
        if provenance["kind"] == "synthetic":
            require(bundle["synthetic"], "Synthetic comments must be visibly synthetic")
            text(provenance.get("description"), "synthetic comment description")
        else:
            for field in ("platform", "comment_id", "collected_at"):
                text(provenance.get(field), "comment." + field)
            safe_url(provenance.get("source_url"), "comment.source_url")
            try:
                when = datetime.fromisoformat(provenance["collected_at"].replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("comment.collected_at requires an ISO timestamp") from exc
            require(when.tzinfo is not None, "comment.collected_at requires timezone")
            archive = file_ref(base_dir, provenance.get("archive_path"), provenance.get("archive_sha256"), "comment archive")
            require(quote in archive.read_text(encoding="utf-8-sig"), "comment exact text is not in archived source")
            gaps.append(f"{comment['id']}：只核对存档文字与哈希，真实用户身份、上下文及真实性待人工复核。")
    for module in indexes["expression_modules"].values():
        for field in ("relation", "conditions"):
            text(module.get(field), "expression_module." + field)
        text_list(module.get("slots"), "expression_module.slots", True)
        refs(module, "source_claim_ids", "claims")
    for topic in indexes["topics"].values():
        one_ref(topic, "decision_id", "decisions")
        for field in ("need_moment", "angle"):
            text(topic.get(field), "topic." + field)
        refs(topic, "module_ids", "expression_modules", True)
        refs(topic, "claim_ids", "claims")
    for variant in indexes["variants"].values():
        one_ref(variant, "topic_id", "topics")
        for field in ("hypothesis", "changed_variable", "control_notes"):
            text(variant.get(field), "variant." + field)
        require(variant.get("mode") in {"exploration", "iteration"}, "variant.mode must be exploration or iteration")
        require("parent_variant_id" in variant, "variant.parent_variant_id must be explicit, null allowed")
        parent = variant["parent_variant_id"]
        require(variant["mode"] != "iteration" or parent is not None, "Iteration requires a parent variant")
        visited = {variant["id"]}
        while parent is not None:
            require(parent not in visited, "Variant lineage contains a cycle")
            visited.add(parent)
            ancestor = indexes["variants"].get(parent)
            require(ancestor is not None and ancestor["product_id"] == variant["product_id"], "Variant parent is missing or cross-product")
            parent = ancestor.get("parent_variant_id")

    script_blocks = {}
    for card in cards["topic"]:
        one_ref(card, "topic_id", "topics")
        for field in ("title", "audience_hypothesis", "need_moment", "opening_question"):
            text(card.get(field), "topic card." + field)
        refs(card, "claim_ids", "claims")
    for card in cards["script"]:
        one_ref(card, "variant_id", "variants")
        require(card["variant_id"] not in script_blocks, "Each variant supports exactly one script card; put multiple segments in its blocks array")
        blocks = index_items(card.get("blocks"), "script blocks", seen)
        require(bool(blocks), "script card requires blocks")
        script_blocks[card["variant_id"]] = blocks
        for block in blocks.values():
            for field in ("function", "voiceover", "on_screen", "visual_goal", "fact_check_note"):
                text(block.get(field), "script block." + field)
            scoped = {**block, "product_id": card["product_id"]}
            refs(scoped, "fact_ids", "facts")
            refs(scoped, "claim_ids", "claims")
            refs(scoped, "evidence_ids", "evidence")
            require(block.get("attribution") in {"own_product_draft", "source_author_quote"}, "Script attribution must distinguish source claim from product fact")
            require(not block["claim_ids"] or block["attribution"] == "source_author_quote", "Source claims in script must remain source_author_quote")
    for card in cards["shooting"]:
        one_ref(card, "variant_id", "variants")
        shots = index_items(card.get("shots"), "shooting shots", seen)
        require(bool(shots), "shooting card requires shots")
        for shot in shots.values():
            require(shot.get("script_block_id") in script_blocks.get(card["variant_id"], {}), "Shot references missing script block in this variant")
            for field in SHOOT_FIELDS:
                text(shot.get(field), "shooting." + field)
            require(type(shot.get("duration_ms")) is int and shot["duration_ms"] > 0, "shot.duration_ms must be positive integer")
            text_list(shot.get("acceptance_checks"), "shooting.acceptance_checks", True)
            alternatives = index_items(shot.get("alternatives"), "visual alternatives", seen)
            require(bool(alternatives), "Each shooting shot needs a concrete viable visual option")
            if len(alternatives) == 1:
                text(shot.get("selection_reason"), "shooting.selection_reason explains why only this option is currently viable")
            for alternative in alternatives.values():
                for field in ("method", "setup", "observable_result", "limitations"):
                    text(alternative.get(field), "alternative." + field)
                require(type(alternative.get("generated")) is bool, "alternative.generated must be boolean")
                require(alternative.get("proof_role") in {"illustration_only", "product_demonstration"}, "Invalid visual proof role")
                require(not alternative["generated"] or alternative["proof_role"] == "illustration_only", "Generated visuals cannot be product proof")
    test_by_variant = {}
    for card in cards["test"]:
        variant = one_ref(card, "variant_id", "variants")
        require(card["variant_id"] not in test_by_variant, "Each variant needs exactly one test card")
        test_by_variant[card["variant_id"]] = card
        for field in TEST_FIELDS:
            text(card.get(field), "test card." + field)
        require(card.get("status") == "untested", "Creative test cards must remain untested; measured results belong in feedback")
        require(card["hypothesis"] == variant["hypothesis"] and card["control_notes"] == variant["control_notes"], "Test card hypothesis/controls must match its variant")
    for variant_id in indexes["variants"]:
        require(variant_id in test_by_variant, "Untested variant has no test card")
        require(variant_id in script_blocks, "Variant has no script card")
        require(any(c["variant_id"] == variant_id for c in cards["shooting"]), "Variant has no shooting card")

    previous = bundle.get("previous_bundle")
    if previous is not None:
        object_value(previous, "previous_bundle")
        prior_path = file_ref(base_dir, previous.get("path"), previous.get("sha256"), "previous bundle")
        prior = read_json(prior_path)
        require(prior.get("bundle_id") == bundle["bundle_id"] and prior.get("revision") == bundle["revision"] - 1,
                "Previous bundle identity/revision mismatch")
    incomplete = bundle["state"] == "draft" or "待填：" in json.dumps(bundle, ensure_ascii=False)
    for name in ("facts", "decisions", "topics", "variants"):
        if not bundle[name]:
            incomplete = True
            gaps.append(f"{LABELS.get(name, name)}为空，创作包继续保持草稿。")
    for name in CARD_TYPES:
        if not cards[name]:
            incomplete = True
            gaps.append(f"{LABELS[name]}为空，需补齐后再交人工审稿。")
    if any(f["status"] == "pending" for f in bundle["facts"]):
        incomplete = True
    if incomplete:
        gaps.insert(0, "编辑 JSON 中的待填内容，补齐本品资料后将 state 改为 ready_for_review；仍需人工审稿。")
    if not bundle["facts"]:
        gaps.append("本品事实为空；填写带来源及适用条件的事实，不可把原作者主张直接改成自己的事实。")
    if not bundle["comments"]:
        gaps.append("未提供真实评论；购买问题只能作为分析者假设，不得写成用户原话。")
    return {"schema_version": SCHEMA_VERSION, "status": "DRAFT" if incomplete else "READY_FOR_HUMAN_REVIEW",
            "structural_valid": True, "factual_verification": False, "listening_verification": False,
            "source_checks": source_checks, "review_gaps": gaps, "notice": NOTICE,
            "counts": {**{key: len(value) for key, value in indexes.items()}, **{f"{key}_cards": len(value) for key, value in card_indexes.items()}}}


def make_bundle(product_id="product-001", product_name="待填：商品正式名称", synthetic=False):
    """Construct an editable draft or a clearly synthetic complete teaching example."""
    pid = product_id
    prefix = "" if synthetic else "待填："
    fact_ids = ["fact-001"] if synthetic else []
    hypothesis = prefix + "在单手忙碌场景中先展示开盖动作，是否让观看者更容易理解使用方法？"
    controls = prefix + "同一商品、价格、场景、演员、入口和视频时长；探索阶段其他差异单独记录，不能归因单个变量。"
    bundle = {"schema_version": SCHEMA_VERSION, "bundle_id": "demo-creative" if synthetic else "creative-" + pid,
        "revision": 1, "previous_bundle": None, "synthetic": synthetic,
        "state": "ready_for_review" if synthetic else "draft", "notice": SYNTHETIC_NOTICE if synthetic else NOTICE,
        "source_status": "not_provided", "products": [{"id": pid, "name": product_name}], "sources": [],
        "facts": ([{"id": "fact-001", "product_id": pid, "statement": "合成示例杯的盖子可用拇指按键打开。",
                   "status": "synthetic", "conditions": "仅用于本示例虚构杯型；不能套用真实商品。",
                   "source": {"kind": "synthetic", "description": "原创虚构教学规格，无真实厂家或检测报告。"}}] if synthetic else []),
        "claims": [], "evidence": [], "comments": [],
        "decisions": [{"id": "decision-001", "product_id": pid, "need_moment": prefix + "出门时一只手拿着包，另一只手想开杯盖。",
                       "buyer_question": prefix + "一只手能否完成开盖？具体怎么操作？", "claim_ids": [], "evidence_ids": [],
                       "remaining_objections": [prefix + "真实杯型的开盖力、漏水情况和使用限制尚未检查。"]}],
        "expression_modules": [{"id": "module-001", "product_id": pid,
                                "relation": "[具体需求时刻] → [完整动作] → [适用条件] → [未解决疑问]",
                                "slots": ["需求时刻", "有资料的商品特征", "可观察动作", "限定条件"],
                                "conditions": "只替换有依据的本品事实；保留完整动作和限制，不将演示推成普遍功效。", "source_claim_ids": []}],
        "topics": [{"id": "topic-001", "product_id": pid, "decision_id": "decision-001", "need_moment": prefix + "单手忙碌时开盖",
                    "angle": prefix + "让观众看清按键、开盖、合盖的完整过程", "module_ids": ["module-001"], "claim_ids": []}],
        "variants": [{"id": "variant-explore-01" if synthetic else "variant-001", "product_id": pid,
                      "topic_id": "topic-001", "hypothesis": hypothesis, "changed_variable": prefix + "探索：先演示开盖的选题切入",
                      "control_notes": controls, "parent_variant_id": None, "mode": "exploration"}],
        "cards": {"topic": [{"id": "topic-card-001", "product_id": pid, "topic_id": "topic-001", "title": prefix + "一只手拿包，杯盖怎么开？",
                             "audience_hypothesis": "分析者假设：有单手操作需求的人；未经人群测量。", "need_moment": prefix + "出门拿包时",
                             "opening_question": prefix + "一只手空不出来时，你会怎么开盖？", "claim_ids": []}], "script": [], "shooting": [], "test": []}}
    if synthetic:
        bundle["comments"] = [{"id": "comment-synthetic-001", "product_id": pid,
            "text": "【虚构教学问题】拿着包的时候，能不能单手开盖？", "interpretation": "只用于练习从问题提炼需求，不是真实用户评论。",
            "provenance": {"kind": "synthetic", "description": "作者原创教学句，没有平台评论来源。"}}]
        bundle["variants"].append({"id": "variant-iterate-01", "product_id": pid, "topic_id": "topic-001",
            "hypothesis": "在相同脚本下，将按键镜头从中景改为手部特写，是否提升开盖步骤的可理解程度？",
            "changed_variable": "仅改变按键动作的景别：中景 → 手部特写", "control_notes": "固定商品、演员、话术、动作、背景、光线、时长和入口；记录仍未控制的差异。",
            "parent_variant_id": "variant-explore-01", "mode": "iteration"})
    for index, variant in enumerate(bundle["variants"], 1):
        suffix = f"{index:03d}"
        common = {"product_id": pid, "variant_id": variant["id"]}
        block_id = "script-block-" + suffix
        bundle["cards"]["script"].append({"id": "script-card-" + suffix, **common, "blocks": [{
            "id": block_id, "function": "场景提问与完整动作演示", "voiceover": prefix + ("这是虚构教学杯：按下按钮，打开杯盖；真实商品请先核对操作说明。" if synthetic else "[具体使用问题]？按说明完成[本品动作]，适用条件是[已核实条件]。"),
            "on_screen": "合成教学示例｜无实测结论" if synthetic else "待填：[本品已核对信息及条件]",
            "visual_goal": prefix + "看清拇指接触按钮、盖子打开和重新合盖的连续过程",
            "fact_ids": fact_ids, "claim_ids": [], "evidence_ids": [], "attribution": "own_product_draft",
            "fact_check_note": "演示脚本待人工核对；动作展示不证明防漏、耐久或适合所有人。"}]})
        bundle["cards"]["shooting"].append({"id": "shooting-card-" + suffix, **common, "shots": [{
            "id": "shoot-shot-" + suffix, "script_block_id": block_id, "subject": prefix + "同一只杯子与操作人的右手",
            "action": prefix + "拿稳杯身，拇指按键，完整开盖，停留一秒，再合盖", "framing": "手部特写" if index == 2 else "胸口到桌面的中景",
            "angle": "侧前方约45度，按钮和杯盖均在画面内", "movement": "固定机位，无推拉与跳切",
            "lighting": "左前方柔光，按钮无反光遮挡", "background": "干净桌面；包放在左手侧，品牌无关物品移出画面",
            "props": "同一杯型、同一手提包、桌面标记与固定支架", "focus": "锁定按钮及盖子运动平面，动作中不失焦",
            "composition": "右手不遮挡按钮；杯口全程不出框；给字幕留上方空间", "start_state": "盖子关闭，拇指未接触按钮",
            "end_state": "开盖过程结束并重新合盖，手指离开按钮", "duration_ms": 6000,
            "acceptance_checks": ["按下到开盖的动作连续无剪切", "按钮、盖子与手指接触点全程可见", "只描述本次可观察动作，条件与限制留在字幕里"],
            "alternatives": [
                {"id": "visual-real-" + suffix, "method": "固定机位连续实拍", "setup": "同一杯型同一动作，带开始与结束状态",
                 "observable_result": "看清一次按键和开盖步骤", "limitations": "一次演示不支持长期耐久或普遍适用结论", "generated": False, "proof_role": "product_demonstration"},
                {"id": "visual-diagram-" + suffix, "method": "步骤示意图", "setup": "用箭头标明手指位置和动作方向，明显标注示意",
                 "observable_result": "理解操作顺序", "limitations": "只作讲解插图，不当成真实效果或产品证据", "generated": True, "proof_role": "illustration_only"}]}]})
        bundle["cards"]["test"].append({"id": "test-card-" + suffix, **common, "hypothesis": variant["hypothesis"],
            "primary_metric": "操作步骤理解率（待人工测试）", "metric_definition": "能按预先评分规则完整复述开盖步骤的人数 / 实际完成观看并回答的人数；不给未观看者计分。",
            "comparison_window": "待安排同一测试日期与条件；未采样", "control_notes": variant["control_notes"],
            "success_criterion": "测试前写下最低可接受差异及所需样本；当前不预设赢家或虚构样本数。",
            "stopping_rule": "按预登记样本或固定截止时间结束；数据不足时保留不确定，不能提前宣告胜出。",
            "confounders": "受试者差异、观看环境、设备、样本量及探索版本中的多个变化都可能影响判断。", "status": "untested"})
    return bundle


def new_output(path):
    path = Path(path).resolve()
    require(not path.exists() or (path.is_dir() and not any(path.iterdir())), f"Output collision; use a new or empty directory: {path}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def bind_source(path, product_id, source_id):
    path = Path(path).resolve()
    manifest = read_json(path)
    build_alignment(manifest)
    return {"id": source_id, "product_id": product_id, "manifest_path": str(path), "manifest_sha256": sha256_file(path),
            "source_sha256": manifest["source"]["sha256"], "video_id": manifest["video_id"],
            "observed_brand": manifest["source"].get("visible_brand", "未记录；不得由产品归属推断原片品牌")}


def rebase_paths(bundle, old_base):
    if bundle.get("previous_bundle"):
        bundle["previous_bundle"]["path"] = str((old_base / bundle["previous_bundle"]["path"]).resolve())
    for source in bundle["sources"]:
        source["manifest_path"] = str((old_base / source["manifest_path"]).resolve())
    for fact in bundle["facts"]:
        if fact["source"]["kind"] == "local_file":
            fact["source"]["path"] = str((old_base / fact["source"]["path"]).resolve())
    for comment in bundle["comments"]:
        provenance = comment["provenance"]
        if provenance["kind"] == "real_user":
            provenance["archive_path"] = str((old_base / provenance["archive_path"]).resolve())


def initialize(output, product_id="product-001", product_name="待填：商品正式名称", source_manifests=(), from_bundle=None):
    if from_bundle:
        require(not source_manifests, "Version-copy and source import must be separate edits")
        original = Path(from_bundle).resolve()
        bundle = read_json(original)
        validate_bundle(bundle, original.parent)
        bundle = copy.deepcopy(bundle)
        rebase_paths(bundle, original.parent)
        bundle["revision"] += 1
        bundle["state"] = "draft"
        bundle["previous_bundle"] = {"path": str(original), "sha256": sha256_file(original)}
    else:
        bundle = make_bundle(product_id, product_name)
        for number, source in enumerate(source_manifests, 1):
            bundle["sources"].append(bind_source(source, product_id, f"source-{number:03d}"))
        if bundle["sources"]:
            bundle["source_status"] = "provided"
    validate_bundle(bundle, Path(output).resolve())
    output = new_output(output)
    save_json(output / BUNDLE_NAME, bundle)
    return {"bundle": str(output / BUNDLE_NAME), "state": "draft", "next_action": "编辑 JSON 中的事实、购买问题与四类卡；validate 检查后 render 生成报告。"}


LABELS = {"products": "商品归属", "sources": "原片来源（只读）", "facts": "本品事实与适用条件", "claims": "原作者主张（未独立核实）",
    "evidence": "源片展示材料与支持范围", "decisions": "购买问题与剩余顾虑", "comments": "评论来源与需求线索", "expression_modules": "可替换表达模块",
    "topics": "需求时刻与选题", "variants": "探索与迭代版本", "topic": "选题卡", "script": "脚本卡", "shooting": "拍摄卡", "test": "测试卡",
    "product_id": "商品编号", "id": "编号", "name": "名称", "statement": "记录的事实", "conditions": "适用条件", "source": "资料来源",
    "exact_text": "原文（完整保留）", "source_ref": "源片定位", "support_scope": "支持范围（待审）", "limitations": "局限", "remaining_objections": "剩余顾虑",
    "need_moment": "需求时刻", "buyer_question": "购买问题", "text": "原始文字", "provenance": "来源档案", "interpretation": "分析者解释",
    "relation": "表达关系", "slots": "替换变量", "angle": "角度", "hypothesis": "待检验假设", "changed_variable": "改变的变量", "control_notes": "保持条件与记录",
    "parent_variant_id": "上一个版本", "mode": "版本类型", "title": "选题标题", "audience_hypothesis": "人群假设（非测量）", "opening_question": "开场问题",
    "blocks": "脚本段", "function": "本段作用", "voiceover": "拟写口播", "on_screen": "拟写画面文字", "visual_goal": "画面需让人看懂什么", "fact_check_note": "事实核对要求",
    "shots": "拍摄镜头", "subject": "主体", "action": "具体动作", "framing": "景别", "movement": "运镜", "lighting": "光线", "background": "背景",
    "props": "道具", "focus": "对焦", "composition": "构图与遮挡", "start_state": "动作开始状态", "end_state": "动作结束状态", "duration_ms": "计划时长（毫秒）",
    "acceptance_checks": "验收标准", "alternatives": "候选表现方式", "selection_reason": "选择理由与现实限制", "method": "表现方式", "setup": "拍摄／制作设置", "observable_result": "可观察信息", "proof_role": "材料用途", "generated": "是否生成示意",
    "primary_metric": "主要观察指标", "metric_definition": "指标分子／分母与口径", "comparison_window": "比较时间与条件", "success_criterion": "测试前判定标准", "stopping_rule": "停止与不足判定", "confounders": "混杂因素", "status": "状态"}
VALUE_LABELS = {"exploration": "探索：寻找需求或表达方向", "iteration": "迭代：围绕父版本比较变量", "untested": "未测试",
    "source_author": "原作者（不代表本品事实）", "source_material_only": "仅源片材料记录", "unverified": "未独立核实", "not_provided": "未提供",
    "synthetic": "虚构合成教学内容", "source_author_quote": "引用原作者主张", "own_product_draft": "本品创作草稿", "illustration_only": "仅讲解示意，不是证据", "product_demonstration": "拟拍本品演示，支持范围待审"}


def render_value(value):
    if isinstance(value, dict):
        return "<dl>" + "".join(f"<dt>{html.escape(LABELS.get(k, k))}</dt><dd>{render_value(v)}</dd>" for k, v in value.items()) + "</dl>"
    if isinstance(value, list):
        return ("<ol>" + "".join("<li>" + render_value(v) + "</li>" for v in value) + "</ol>") if value else '<span class="muted">未提供</span>'
    if value is None:
        return '<span class="muted">无／首版</span>'
    if isinstance(value, bool):
        return "是" if value else "否"
    return html.escape(VALUE_LABELS.get(str(value), str(value))).replace("\n", "<br>")


def render_html(bundle, validation):
    parts = ['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">',
        '<title>内容创作工作台</title><style>body{font:16px/1.65 system-ui,Microsoft YaHei,sans-serif;background:#f4f5f7;color:#202d3d;margin:0}main{max-width:1080px;margin:auto;padding:32px 20px}h1{font-size:32px;line-height:1.3}h2{margin-top:38px;border-left:5px solid #127b77;padding-left:12px}h3{margin:0 0 14px;font-size:18px}.notice{background:#fff0cc;border:1px solid #e3bf60;padding:16px;border-radius:10px}.pill{font-size:14px;background:#dcefea;padding:4px 10px;border-radius:14px}article{background:white;border:1px solid #dce1e7;border-radius:12px;padding:22px;margin:16px 0;overflow-wrap:anywhere}dl{display:grid;grid-template-columns:minmax(130px,190px) minmax(0,1fr);gap:8px 16px;margin:0}dt{color:#536477;font-weight:600}dd{margin:0;min-width:0}dd dl{display:block;border-left:2px solid #e6eaf0;padding-left:14px}dd dt{font-size:14px;margin-top:6px}ol{margin:0;padding-left:22px}li+li{margin-top:10px}.muted{color:#6e7885}nav{display:flex;gap:10px;flex-wrap:wrap;margin:20px 0}a{color:#006c69}@media(max-width:620px){main{padding:20px 12px}dl{display:block}dt{margin-top:10px}article{padding:16px}}@media print{body{background:white}article{break-inside:avoid}nav{display:none}}</style></head><body><main>',
        '<h1>内容创作工作台</h1>', '<p>' + html.escape(bundle["bundle_id"]) + f' · 修订 {bundle["revision"]} · <span class="pill">' + ('草稿待补' if validation["status"] == "DRAFT" else '结构通过 · 仍待人工审稿') + '</span></p>',
        '<div class="notice">' + html.escape(NOTICE) + '<br>' + html.escape(bundle["notice"]) + '</div>',
        '<nav>' + ''.join(f'<a href="#card-{kind}">{LABELS[kind]}</a>' for kind in CARD_TYPES) + '<a href="#sources">来源与证据</a></nav>',
        '<section><h2>下一步与审稿缺口</h2>' + render_value(validation["review_gaps"] or ["逐项核实商品资料、声画原文、材料支持范围和拍摄可行性；没有投放结果。"] ) + '</section>']
    for kind in CARD_TYPES:
        parts.append(f'<section id="card-{kind}"><h2>{LABELS[kind]}</h2>')
        for card in bundle["cards"][kind]:
            parts.append('<article><h3>' + html.escape(card["id"]) + '</h3>' + render_value({k: v for k, v in card.items() if k != "id"}) + '</article>')
        parts.append('</section>')
    for key in COLLECTIONS:
        parts.append(f'<section id="{key}"><h2>{LABELS[key]}</h2>')
        if not bundle[key]:
            parts.append('<p class="muted">未提供。此处缺失不意味着原片没有相关内容。</p>')
        for item in bundle[key]:
            parts.append('<article><h3>' + html.escape(item["id"]) + '</h3>' + render_value({k: v for k, v in item.items() if k != "id"}) + '</article>')
        parts.append('</section>')
    parts.append('<h2>程序核对范围</h2>' + render_value(validation) + '</main></body></html>')
    return ''.join(parts)


def search_records(bundle, bundle_path):
    records = []
    collections = [(key, bundle[key]) for key in COLLECTIONS]
    collections.extend(("card_" + key, bundle["cards"][key]) for key in CARD_TYPES)
    for kind, items in collections:
        for item in items:
            records.append({"bundle_id": bundle["bundle_id"], "revision": bundle["revision"], "bundle_path": str(bundle_path),
                            "kind": kind, "id": item["id"], "product_id": item.get("product_id", item["id"] if kind == "products" else ""),
                            "text": json.dumps(item, ensure_ascii=False, sort_keys=True, allow_nan=False)})
    return records


def csv_safe(value):
    value = str(value)
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else value


def write_report(bundle_path, output):
    bundle_path = Path(bundle_path).resolve()
    bundle = read_json(bundle_path)
    validation = validate_bundle(bundle, bundle_path.parent)
    output = new_output(output)
    exported_bundle = copy.deepcopy(bundle)
    rebase_paths(exported_bundle, bundle_path.parent)
    save_json(output / "bundle.json", exported_bundle)
    save_json(output / "validation.json", validation)
    records = search_records(bundle, bundle_path)
    save_json(output / "search-index.json", {"method": "literal_keyword_all_terms", "records": records})
    with (output / "report.html").open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(render_html(bundle, validation))
    with (output / "cards.csv").open("x", encoding="utf-8-sig", newline="") as handle:
        fields = ["bundle_id", "revision", "bundle_path", "kind", "id", "product_id", "text"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            if record["kind"].startswith("card_"):
                writer.writerow({key: csv_safe(value) for key, value in record.items()})
    return {"report": str(output / "report.html"), "validation": validation}


def search_bundles(root, query, product_id=None):
    terms = text(query, "query").casefold().split()
    root = Path(root).resolve()
    require(root.exists(), "Search root does not exist")
    paths = [root] if root.is_file() else sorted(root.rglob(BUNDLE_NAME))
    found = []
    checked = 0
    for path in paths:
        bundle = read_json(path)
        validate_bundle(bundle, path.parent)
        checked += 1
        for record in search_records(bundle, path):
            if (product_id is None or record["product_id"] == product_id) and all(term in record["text"].casefold() for term in terms):
                found.append(record)
    return {"method": "literal_keyword_all_terms", "query": query, "bundles_checked": checked, "results": found,
            "notice": "逐字关键词检索（空格分词且全部匹配），不是语义检索、AI 排序或效果推荐。"}


def create_demo(output):
    output = new_output(output)
    bundle = make_bundle("demo-product", "合成教学杯（不存在的商品）", True)
    source_dir = output / "synthetic-source"
    source_dir.mkdir()
    source_bytes = source_dir / "synthetic-source.txt"
    source_bytes.write_text(SYNTHETIC_NOTICE + "\n这是文本字节绑定样例，不是视频，不验证媒体解码。\n", encoding="utf-8")
    quote = "【虚构源片台词】按一下按钮，杯盖打开了。"
    manifest = {"video_id": "synthetic-source-001", "revision": "synthetic-1", "title": "合成来源绑定样例（非真实视频）",
        "source": {"path": source_bytes.name, "sha256": sha256_file(source_bytes), "visible_brand": "无真实品牌"},
        "transcript_status": "synthetic_authored_text", "audio_review_status": "not_reviewed",
        "segments": [{"id": "S01", "name": "合成开盖演示段", "start_ms": 0, "end_ms": 2000, "script_function": "合成演示",
            "source_shot_ids": ["shot-001"], "source_shot_spans": [{"shot_id": "shot-001", "source_shot_start_ms": 0, "source_shot_end_ms": 2000,
                "segment_intersection_start_ms": 0, "segment_intersection_end_ms": 2000}],
            "source_excerpt": {"type": "subtitle_excerpts_only", "items": [{"shot_id": "shot-001", "text": quote,
                "intersection_start_ms": 0, "intersection_end_ms": 2000}]}, "source_claims": [quote], "reusable_formula": {}, "shot_groups": []}]}
    timeline_path = source_dir / "timeline.json"
    save_json(timeline_path, {"video_id": manifest["video_id"], "source": {"sha256": manifest["source"]["sha256"]},
                              "segments": [{"id": "shot-001", "start_ms": 0, "end_ms": 2000}]})
    manifest["source"].update(timeline_manifest_path=timeline_path.name, timeline_manifest_sha256=sha256_file(timeline_path))
    manifest_path = source_dir / "script-manifest.json"
    save_json(manifest_path, manifest)
    source = bind_source(manifest_path, "demo-product", "source-001")
    source["manifest_path"] = "synthetic-source/script-manifest.json"
    bundle["sources"] = [source]
    bundle["source_status"] = "provided"
    ref = {"source_id": source["id"], "manifest_sha256": source["manifest_sha256"], "source_sha256": source["source_sha256"],
           "segment_id": "S01", "source_shot_ids": ["shot-001"], "start_ms": 0, "end_ms": 2000}
    bundle["evidence"] = [{"id": "evidence-001", "product_id": "demo-product", "kind": "source_material", "source_ref": copy.deepcopy(ref),
        "description": "【合成记录】源片材料条目描述一次开盖动作；本例没有真实视频画面。", "support_scope": "仅用于验证引用的商品、段落、镜头及时间绑定。",
        "limitations": "不证明真实商品功效、手感、耐久、防漏或消费者反应。", "verification_status": "source_material_only"}]
    bundle["claims"] = [{"id": "claim-001", "product_id": "demo-product", "origin": "source_author", "exact_text": quote,
        "source_pointer": "/segments/0/source_claims/0", "source_ref": copy.deepcopy(ref), "evidence_ids": ["evidence-001"],
        "support_scope": "仅记录虚构作者的一次动作描述，未独立验证。", "remaining_objections": ["没有真实媒体、样品或实验资料。"], "verification_status": "unverified"}]
    bundle["decisions"][0].update(claim_ids=["claim-001"], evidence_ids=["evidence-001"])
    bundle["expression_modules"][0]["source_claim_ids"] = ["claim-001"]
    validate_bundle(bundle, output)
    save_json(output / BUNDLE_NAME, bundle)
    report = write_report(output / BUNDLE_NAME, output / "report")
    return {"bundle": str(output / BUNDLE_NAME), "report": report["report"], "notice": SYNTHETIC_NOTICE}


def main(argv=None):
    parser = argparse.ArgumentParser(description="本地内容创作工作台：事实／来源、选题、脚本、拍摄与未测试卡。")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="建立可编辑草稿或不覆盖旧版的下一修订")
    init.add_argument("--output", type=Path, required=True)
    init.add_argument("--product-id", default="product-001")
    init.add_argument("--product-name", default="待填：商品正式名称")
    init.add_argument("--source-manifest", type=Path, action="append", default=[])
    init.add_argument("--from-bundle", type=Path)
    validate = sub.add_parser("validate", help="只核对结构、来源引用和哈希")
    validate.add_argument("--bundle", type=Path, required=True)
    validate.add_argument("--require-ready", action="store_true")
    render = sub.add_parser("render", help="生成静态中文 HTML、JSON、CSV 和检索索引")
    render.add_argument("--bundle", type=Path, required=True)
    render.add_argument("--output", type=Path, required=True)
    search = sub.add_parser("search", help="跨工作台逐字关键词检索；空格分词，全部匹配")
    search.add_argument("--root", type=Path, required=True)
    search.add_argument("--query", required=True)
    search.add_argument("--product-id")
    demo = sub.add_parser("demo", help="运行原创合成教学示例，不调用远程服务")
    demo.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            result = initialize(args.output, args.product_id, args.product_name, args.source_manifest, args.from_bundle)
        elif args.command == "validate":
            result = validate_bundle(read_json(args.bundle), args.bundle.resolve().parent)
            if args.require_ready and result["status"] == "DRAFT":
                raise ValueError("Bundle is still DRAFT; edit it before requiring readiness")
        elif args.command == "render":
            result = write_report(args.bundle, args.output)
        elif args.command == "search":
            result = search_bundles(args.root, args.query, args.product_id)
        else:
            result = create_demo(args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
