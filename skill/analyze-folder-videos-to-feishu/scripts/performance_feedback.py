#!/usr/bin/env python3
"""Offline performance feedback: explicit CSV mapping, audited imports and reports.

Amounts, totals and ratios are JSON decimal strings, never binary floats. Blank
numeric cells mean unknown. Dates are inclusive ISO calendar dates in the explicitly
declared reporting timezone. No platform APIs, account credentials or ad operations.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
import csv
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import html
import io
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ASSETS = Path(__file__).resolve().parent.parent / "assets"
BASE_COLUMNS = ("platform", "creative_id", "date", "account", "goal", "attribution_window", "currency")
CONTEXT_FIELDS = ("price", "entry", "placement")
STAGES = ("attention", "click", "conversion")
BOUNDARY = "报表是观察性记录；满足自定样本条件不代表统计显著或因果验证，不自动选出胜出素材。"


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _hash(value):
    if not isinstance(value, bytes):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _read_json(path):
    def reject_constant(value):
        raise ValueError("non-finite JSON number: " + value)
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key: " + key)
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding="utf-8-sig"), parse_constant=reject_constant, object_pairs_hook=pairs)


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + " must be a nonempty string")
    return value.strip()


def _decimal_text(value):
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _number(value, kind, label, allow_blank=True):
    if value is None or value == "":
        if allow_blank:
            return None
        raise ValueError(label + " needs a number")
    if isinstance(value, bool):
        raise ValueError(label + " is not a number")
    text = str(value).strip()
    # No implicit locale conversion, exponent overflow or spreadsheet expressions.
    if len(text) > 100 or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", text):
        raise ValueError(label + " must be a finite nonnegative decimal (no separators)")
    number = Decimal(text)
    if not number.is_finite() or number < 0 or (kind == "count" and number != number.to_integral_value()):
        raise ValueError(label + " must be a finite nonnegative " + kind)
    return _decimal_text(number)


def _date(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(label + " date must use unambiguous YYYY-MM-DD")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError(label + " date is invalid") from exc


def _timezone(value):
    value = _text(value, "timezone")
    if re.fullmatch(r"[+-]\d{2}:\d{2}", value):
        hours, minutes = map(int, value[1:].split(":"))
        if hours > 14 or minutes > 59 or (hours == 14 and minutes):
            raise ValueError("invalid timezone offset")
    else:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone must be an available IANA zone or explicit offset like +08:00") from exc
    return value


def _registry(data):
    if not isinstance(data, dict) or not isinstance(data.get("variants"), list):
        raise ValueError("registry requires a variants list")
    result = {}
    for variant in data["variants"]:
        if not isinstance(variant, dict):
            raise ValueError("variant must be an object")
        for key in ("id", "product_id", "hypothesis", "changed_variable", "control_notes"):
            _text(variant.get(key), "variant." + key)
        if variant["id"] in result:
            raise ValueError("duplicate variant id: " + variant["id"])
        if variant.get("mode") not in ("exploration", "iteration"):
            raise ValueError("variant mode must be exploration or iteration")
        if "parent_variant_id" not in variant:
            raise ValueError("variant parent_variant_id must be explicit, null allowed")
        if variant["mode"] == "iteration" and variant["parent_variant_id"] is None:
            raise ValueError("iteration requires a parent variant")
        result[variant["id"]] = copy.deepcopy(variant)
    for variant in result.values():
        parent = variant["parent_variant_id"]
        seen = {variant["id"]}
        while parent is not None:
            _text(parent, "parent variant id")
            if parent in seen:
                raise ValueError("variant parent lineage contains a cycle")
            seen.add(parent)
            ancestor = result.get(parent)
            if ancestor is None or ancestor["product_id"] != variant["product_id"]:
                raise ValueError("unknown or cross-product parent variant")
            parent = ancestor["parent_variant_id"]
        # Do not trust a caller-supplied fingerprint. Derive it from the current
        # bundle's per-variant cards and reachable evidence, or mark it unbound.
        variant["content_binding"] = _content_binding(data, variant)
    return result


def _content_binding(data, variant):
    if "cards" not in data:
        return {"kind": "standalone_registry", "note": "Stable creative identity is owned by the supplied registry; no production cards were supplied."}
    cards = data["cards"]
    if not isinstance(cards, dict):
        raise ValueError("creative registry cards must be an object")
    selected_cards = {}
    for kind in ("topic", "script", "shooting", "test"):
        values = cards.get(kind, [])
        if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
            raise ValueError("creative registry card collections must contain objects")
        selected_cards[kind] = sorted((item for item in values if
            (item.get("variant_id") == variant["id"] if kind != "topic" else
             variant.get("topic_id") is not None and item.get("topic_id") == variant["topic_id"])), key=lambda item: str(item.get("id", "")))
    collections = ("products", "topics", "decisions", "expression_modules", "facts", "claims", "evidence", "sources", "comments")
    indexes = {}
    for name in collections:
        values = data.get(name, [])
        if not isinstance(values, list) or any(not isinstance(item, dict) or not isinstance(item.get("id"), str) for item in values):
            raise ValueError("creative registry " + name + " must contain identified objects")
        indexes[name] = {item["id"]: item for item in values}
        if len(indexes[name]) != len(values):
            raise ValueError("duplicate creative registry " + name + " id")
    singular = {"product_id": "products", "topic_id": "topics", "decision_id": "decisions", "source_id": "sources"}
    plural = {"fact_ids": "facts", "claim_ids": "claims", "source_claim_ids": "claims", "evidence_ids": "evidence", "module_ids": "expression_modules", "comment_ids": "comments"}
    selected = {name: {} for name in collections}

    def collect(value):
        if isinstance(value, list):
            for item in value:
                collect(item)
        elif isinstance(value, dict):
            for key, item in value.items():
                collection = singular.get(key) or plural.get(key)
                if collection:
                    identifiers = [item] if key in singular else item
                    if not isinstance(identifiers, list):
                        raise ValueError("invalid creative reference: " + key)
                    for identifier in identifiers:
                        if not isinstance(identifier, str) or identifier not in indexes[collection]:
                            raise ValueError("missing creative reference: " + key + "=" + str(identifier))
                        if identifier not in selected[collection]:
                            target = indexes[collection][identifier]
                            selected[collection][identifier] = target
                            collect(target)
                collect(item)

    canonical_variant = {key: value for key, value in variant.items() if key != "content_binding"}
    collect(canonical_variant)
    collect(selected_cards)

    def content_only(value):
        if isinstance(value, list):
            return [content_only(item) for item in value]
        if isinstance(value, dict):
            # Relocating a hash-bound file or exporting a rebased bundle does
            # not change its creative identity; source bytes remain hash-bound.
            omitted = set()
            for path_key, hash_key in (("path", "sha256"), ("manifest_path", "manifest_sha256"), ("archive_path", "archive_sha256")):
                if hash_key in value:
                    omitted.add(path_key)
            return {key: content_only(item) for key, item in value.items() if key not in omitted}
        return value

    content = {"variant": canonical_variant, "cards": selected_cards,
               "references": {name: [items[key] for key in sorted(items)] for name, items in selected.items()}}
    return {"kind": "creative_bundle", "sha256": _hash(content_only(content))}


def _mapping(data, variants):
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("mapping schema_version must be 1")
    data = copy.deepcopy(data)
    data["timezone"] = _timezone(data.get("timezone"))
    if not isinstance(data.get("synthetic", False), bool):
        raise ValueError("synthetic must be boolean")
    columns = data.get("columns", {})
    cohort = data.get("cohort_fields", [])
    if not isinstance(columns, dict) or not isinstance(cohort, list) or any(not isinstance(key, str) for key in cohort):
        raise ValueError("columns must be an object and cohort_fields a text array")
    if not set(CONTEXT_FIELDS).issubset(cohort):
        raise ValueError("cohort_fields must explicitly include price, entry, placement")
    if len(set(cohort)) != len(cohort):
        raise ValueError("duplicate or invalid cohort field")
    if any(key in BASE_COLUMNS or key in ("date_end", "timezone", "date_start") for key in cohort):
        raise ValueError("cohort fields cannot shadow standard dimensions")
    if set(columns) - set(BASE_COLUMNS) - set(cohort) - {"date_end"}:
        raise ValueError("columns contain reserved or undeclared dimensions; declare extra context in cohort_fields")
    for key in (*BASE_COLUMNS, *cohort):
        _text(columns.get(key), "columns." + key)
    if "date_end" in columns:
        _text(columns["date_end"], "columns.date_end")
    fields = data.get("numeric_fields")
    if not isinstance(fields, dict) or not fields:
        raise ValueError("numeric_fields must declare raw counters/amounts")
    for key, definition in fields.items():
        _text(key, "numeric field name")
        if not isinstance(definition, dict) or definition.get("kind") not in ("count", "amount"):
            raise ValueError("numeric field kind must be count or amount")
        _text(definition.get("column"), "numeric field column")
    source_columns = list(columns.values()) + [item["column"] for item in fields.values()]
    if len(source_columns) != len(set(source_columns)):
        raise ValueError("duplicate source column mapping")
    metrics = data.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        raise ValueError("metrics require explicit numerator and denominator")
    for key, metric in metrics.items():
        _text(key, "metric name")
        if not isinstance(metric, dict):
            raise ValueError("metric must be an object")
        for side in ("numerator", "denominator"):
            if metric.get(side) not in fields:
                raise ValueError("metric " + key + " " + side + " must reference a numeric field")
        if metric.get("stage") not in STAGES:
            raise ValueError("metric stage must be attention, click or conversion")
        _text(metric.get("label"), "metric label")
    criteria = data.get("sample_criteria", {})
    if not isinstance(criteria, dict):
        raise ValueError("sample_criteria must be an object of minimum raw totals")
    for key, value in criteria.items():
        if key not in fields:
            raise ValueError("sample criterion must reference a numeric field")
        value = _number(value, fields[key]["kind"], "sample_criteria." + key, False)
        if Decimal(value) <= 0:
            raise ValueError("sample criteria must be positive explicit minimums")
        criteria[key] = value
    data["sample_criteria"] = criteria
    creative_map = data.get("creative_map")
    if not isinstance(creative_map, list):
        raise ValueError("creative_map must be a list")
    mapping = {}
    for entry in creative_map:
        if not isinstance(entry, dict):
            raise ValueError("creative mapping must be an object")
        key = (_text(entry.get("platform"), "mapping platform"), _text(entry.get("account"), "mapping account"), _text(entry.get("creative_id"), "mapping creative_id"))
        if key in mapping:
            raise ValueError("duplicate creative mapping: " + repr(key))
        if entry.get("variant_id") not in variants:
            raise ValueError("unknown variant in creative_map: " + str(entry.get("variant_id")))
        mapping[key] = entry["variant_id"]
    return data, mapping


def _empty_state():
    return {"schema_version": 1, "observations": [], "imports": [], "revisions": [], "variants": {}}


def _load_state(path):
    if not Path(path).exists():
        return _empty_state()
    state = _read_json(path)
    if not isinstance(state, dict) or state.get("schema_version") != 1:
        raise ValueError("unsupported feedback state schema")
    for key in ("observations", "imports", "revisions"):
        if not isinstance(state.get(key), list):
            raise ValueError("invalid feedback state: " + key)
    if not isinstance(state.get("variants"), dict):
        raise ValueError("invalid feedback state: variants")
    keys = [row.get("key") for row in state["observations"]]
    if None in keys or len(set(keys)) != len(keys):
        raise ValueError("duplicate or missing observation key in state")
    return state


def _atomic_json(path, value):
    path = Path(path)
    descriptor, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_json_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _same_series(row):
    cohort = {key: value for key, value in row["cohort"].items() if key not in ("date_start", "date_end")}
    return _hash([row["creative_id"], cohort, row["synthetic"]])


def _check_overlaps(rows):
    series = {}
    for row in rows:
        series.setdefault(_same_series(row), []).append(row)
    for group in series.values():
        ordered = sorted(group, key=lambda row: row["cohort"]["date_start"])
        for previous, current in zip(ordered, ordered[1:]):
            if previous["cohort"]["date_end"] >= current["cohort"]["date_start"]:
                raise ValueError("overlapping observation date ranges; use disjoint exports or explicit upsert of the same range")


@contextmanager
def state_lock(state):
    """Keep the lock file; the OS releases its ownership if a process exits."""
    state = Path(state).resolve()
    state.parent.mkdir(parents=True, exist_ok=True)
    lock = state.with_name(state.name + ".lock")
    with lock.open("a+b") as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError("feedback state is locked; another import is running: " + str(state)) from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def import_data(state, csv_path, mapping_path, registry_path, operation="append", reason=None):
    """Validate the entire input before an atomic, locked state update."""
    if operation not in ("append", "upsert"):
        raise ValueError("operation must be append or upsert")
    if operation == "upsert":
        _text(reason, "upsert reason")
    state, csv_path, mapping_path, registry_path = map(Path, (state, csv_path, mapping_path, registry_path))
    state = state.resolve()
    if state.resolve() in {csv_path.resolve(), mapping_path.resolve(), registry_path.resolve()}:
        raise ValueError("state must not overwrite an input file")
    registry_data = _read_json(registry_path)
    variants = _registry(registry_data)
    config, creative_map = _mapping(_read_json(mapping_path), variants)
    if registry_data.get("synthetic") is True and not config.get("synthetic", False):
        raise ValueError("synthetic registry requires synthetic=true in the mapping")
    csv_bytes = csv_path.read_bytes()
    hashes = {"csv_sha256": _hash(csv_bytes), "mapping_sha256": _hash(mapping_path.read_bytes()), "registry_sha256": _hash(registry_path.read_bytes())}
    import_id = _hash(hashes)
    numeric_definitions = {name: {key: value for key, value in definition.items() if key != "column"} for name, definition in config["numeric_fields"].items()}
    definitions = {"numeric_fields": numeric_definitions, "metrics": config["metrics"]}
    definition_hash = _hash(definitions)
    reader = csv.DictReader(io.StringIO(csv_bytes.decode("utf-8-sig"), newline=""))
    if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
        raise ValueError("CSV needs unique column headers")
    required = set(config["columns"].values()) | {item["column"] for item in config["numeric_fields"].values()}
    missing = required - set(reader.fieldnames)
    if missing:
        raise ValueError("CSV missing mapped columns: " + ", ".join(sorted(missing)))
    incoming = []
    for line, raw in enumerate(reader, start=2):
        if None in raw or any(value is None for value in raw.values()):
            raise ValueError("CSV row " + str(line) + " has an inconsistent number of cells")
        dims = {key: _text(raw[column], "row " + str(line) + " " + key) for key, column in config["columns"].items()}
        creative_id = dims.pop("creative_id")
        variant_id = creative_map.get((dims["platform"], dims["account"], creative_id))
        if variant_id is None:
            raise ValueError("unknown creative/platform/account mapping at CSV row " + str(line) + ": " + creative_id)
        start = _date(dims.pop("date"), "row " + str(line))
        end = _date(dims.pop("date_end", start), "row " + str(line))
        if end < start:
            raise ValueError("date_end must not precede date")
        cohort = {"date_start": start, "date_end": end, "timezone": config["timezone"], **dims}
        values = {name: _number(raw[definition["column"]].strip(), definition["kind"], "row " + str(line) + " " + name) for name, definition in config["numeric_fields"].items()}
        synthetic = config.get("synthetic", False)
        # A revised metric definition reinterprets the same observation; it is
        # an explicit correction, never a new row to count alongside the old one.
        key = _hash([creative_id, cohort, synthetic])
        incoming.append({"key": key, "creative_id": creative_id, "variant": variants[variant_id], "cohort": cohort,
                         "values": values, "definitions": definitions, "definition_hash": definition_hash,
                         "sample_criteria": config["sample_criteria"], "synthetic": synthetic,
                         "provenance": {"import_id": import_id, "csv_row": line, **hashes}})
    if not incoming:
        raise ValueError("CSV has no observations; fill the export template first")
    if len({row["key"] for row in incoming}) != len(incoming):
        raise ValueError("duplicate observations inside CSV; resolve the source export before import")
    _check_overlaps(incoming)
    with state_lock(state):
        stored = _load_state(state)
        for variant_id, variant in variants.items():
            prior = stored["variants"].get(variant_id, {})
            prior_binding = prior.get("content_binding", {})
            if prior_binding.get("kind") == "creative_bundle" and prior_binding != variant["content_binding"]:
                raise ValueError("creative content changed for " + variant_id + "; create a new variant id with parent_variant_id before import")
        existing = {row["key"]: row for row in stored["observations"]}
        input_id = import_id
        prior_imports = [item for item in stored["imports"] if item.get("input_id", item["import_id"]) == input_id]
        matches_current = all(row["key"] in existing and
            {key: value for key, value in row.items() if key != "provenance"} ==
            {key: value for key, value in existing[row["key"]].items() if key != "provenance"} for row in incoming)
        if prior_imports and (operation == "append" or matches_current):
            return {"status": "already_imported", "import_id": prior_imports[-1]["import_id"], "input_id": input_id, "added_count": 0, "replaced_count": 0}
        if prior_imports:
            # The same source bytes can be explicitly restored after a later
            # correction. Preserve a distinct audit event and revision chain.
            import_id = _hash([input_id, len(stored["imports"]) + 1])
        for row in incoming:
            row["provenance"].update(import_id=import_id, input_id=input_id)
        duplicates = [row for row in incoming if row["key"] in existing]
        if duplicates and operation == "append":
            raise ValueError("observations already exist; use --operation upsert --reason TEXT for explicit corrections")
        for row in incoming:
            if row["key"] in existing:
                stored["revisions"].append({"replaced_by_import_id": import_id, "reason": reason, "previous_observation": existing[row["key"]]})
            existing[row["key"]] = row
        _check_overlaps(list(existing.values()))
        receipt = {"status": "imported", "import_id": import_id, "input_id": input_id, "operation": operation, "reason": reason,
                   "added_count": len(incoming) - len(duplicates), "replaced_count": len(duplicates),
                   "imported_at": datetime.now(timezone.utc).isoformat(), "source_name": csv_path.name, **hashes}
        stored["observations"] = list(existing.values())
        stored["imports"].append(receipt)
        stored["variants"].update(variants)
        _atomic_json(state, stored)
        return receipt


def _assessment(totals, criteria):
    if not criteria:
        return {"status": "unassessed", "criteria": {}, "checks": {}, "note": "尚未定义样本条件，不能判断样本是否足够；" + BOUNDARY}
    checks = {name: {"minimum": minimum, "observed": totals[name], "met": totals[name] is not None and Decimal(totals[name]) >= Decimal(minimum)} for name, minimum in criteria.items()}
    return {"status": "sufficient" if all(item["met"] for item in checks.values()) else "insufficient", "criteria": criteria, "checks": checks, "note": BOUNDARY}


def _next_checks(metrics, assessment):
    instructions = {
        "attention": "核对开头画面与原声、观看计数口径和缺失数据；需要留存/观看分子分母才能检查注意阶段。",
        "click": "结合点击分子分母检查开头承诺和行动入口是否一致；点击变化不能直接说明成交变化。",
        "conversion": "核对归因是否成熟、价格/优惠、商品页或直播承接；成交变化仍可能受人群与流量分配影响。",
    }
    checks = []
    for stage in STAGES:
        available = {key: item["value"] for key, item in metrics.items() if item["stage"] == stage}
        known = any(value is not None for value in available.values())
        checks.append({"stage": stage, "measured_metrics": available, "status": "review_observation" if known else "missing_measurement",
                       "action": instructions[stage] + (" 当前样本条件未满足或未评估，先补齐观察。" if assessment["status"] != "sufficient" else " 仅满足用户设定的样本条件，仍需控制混杂因素并复查。")})
    return checks


def report_data(state):
    """Group only exact matched cohorts and compute ratios from summed raw fields."""
    state_path = Path(state)
    if not state_path.exists():
        raise ValueError("feedback state does not exist")
    stored = _load_state(state_path)
    grouped = {}
    for row in stored["observations"]:
        key = _hash([row["variant"], row["cohort"], row["definition_hash"], row["sample_criteria"], row["synthetic"]])
        grouped.setdefault(key, []).append(row)
    results = []
    with localcontext() as context:
        context.prec = 128
        for group_id, rows in sorted(grouped.items()):
            first = rows[0]
            totals = {}
            for name in first["definitions"]["numeric_fields"]:
                values = [row["values"][name] for row in rows]
                totals[name] = None if any(value is None for value in values) else _decimal_text(sum((Decimal(value) for value in values), Decimal(0)))
            metrics = {}
            for name, definition in first["definitions"]["metrics"].items():
                numerator, denominator = totals[definition["numerator"]], totals[definition["denominator"]]
                value = None
                if numerator is not None and denominator is not None and Decimal(denominator) != 0:
                    with localcontext() as ratio_context:
                        ratio_context.prec = 28
                        value = _decimal_text(Decimal(numerator) / Decimal(denominator))
                metrics[name] = {**definition, "value": value, "numerator_total": numerator, "denominator_total": denominator,
                                 "unknown_reason": None if value is not None else "missing_value_or_zero_denominator"}
            assessment = _assessment(totals, first["sample_criteria"])
            comparison_key = _hash([first["variant"]["product_id"], first["cohort"], first["definition_hash"], first["sample_criteria"], first["synthetic"]])
            results.append({"result_id": group_id, "comparison_group": comparison_key, "variant": first["variant"], "cohort": first["cohort"],
                            "synthetic": first["synthetic"], "definitions": first["definitions"], "creative_ids": sorted({row["creative_id"] for row in rows}),
                            "observation_count": len(rows), "totals": totals, "metrics": metrics,
                            "sample_assessment": assessment, "next_checks": _next_checks(metrics, assessment), "causal_claim": False,
                            "provenance": [row["provenance"] for row in rows]})
    measured = {row["variant"]["id"] for row in stored["observations"]}
    comparison_groups = {}
    for result in results:
        comparison_groups.setdefault(result["comparison_group"], []).append(result["result_id"])
    return {"schema_version": 1, "synthetic": bool(results) and all(row["synthetic"] for row in results),
            "has_synthetic_observations": any(row["synthetic"] for row in results), "state_sha256": _hash(state_path.read_bytes()),
            "boundary": BOUNDARY, "numeric_encoding": "decimal_string_or_null", "date_semantics": "inclusive dates; exact periods kept separate",
            "comparison_note": "只有同产品、同平台、同日期范围、时区、账户、目标、归因窗口、币种、价格、入口、版位及指标口径才归入同组；同组也不等于随机对照。",
            "results": results, "comparison_groups": comparison_groups,
            "unmeasured_variants": [variant for key, variant in stored["variants"].items() if key not in measured],
            "imports": stored["imports"], "revision_count": len(stored["revisions"])}


def _new_directory(path):
    path = Path(path)
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValueError("output must be a new or empty directory; existing files are preserved: " + str(path))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _html(report):
    escape = lambda value: html.escape(str(value), quote=True)
    status = {"sufficient": "满足自定样本条件", "insufficient": "样本不足/数据缺失", "unassessed": "尚未评估样本"}
    stages = {"attention": "注意阶段", "click": "点击阶段", "conversion": "转化阶段"}
    cards = []
    for result in report["results"]:
        variant, cohort = result["variant"], result["cohort"]
        context = " · ".join(str(key) + "=" + str(value) for key, value in cohort.items())
        metric_rows = "".join("<tr><th>" + escape(item["label"]) + "</th><td>" + escape(item["value"] if item["value"] is not None else "未知") + "</td><td>" + escape(item["numerator"]) + "=" + escape(item["numerator_total"]) + " / " + escape(item["denominator"]) + "=" + escape(item["denominator_total"]) + "</td></tr>" for item in result["metrics"].values())
        checks = "".join("<li><strong>" + stages[item["stage"]] + "：</strong>" + escape(item["action"]) + "</li>" for item in result["next_checks"])
        sample = result["sample_assessment"]
        criteria = "；".join(name + "：" + str(item["observed"]) + " / 最低 " + item["minimum"] for name, item in sample["checks"].items()) or "未配置"
        cards.append("<section><h2>" + escape(variant["id"]) + (" · 合成演示" if result["synthetic"] else "") + "</h2><p><strong>假设：</strong>" + escape(variant["hypothesis"]) + "</p><p><strong>改变变量：</strong>" + escape(variant["changed_variable"]) + "</p><p><strong>控制说明：</strong>" + escape(variant["control_notes"]) + "</p><p class='context'>" + escape(context) + "</p><p>平台创意：" + escape(", ".join(result["creative_ids"])) + "；模式：" + escape(variant["mode"]) + "；父版本：" + escape(variant.get("parent_variant_id") or "无") + "</p><table><thead><tr><th>指标</th><th>比值（非百分数）</th><th>累计分子 / 累计分母</th></tr></thead><tbody>" + metric_rows + "</tbody></table><p><strong>" + status[sample["status"]] + "：</strong>" + escape(criteria) + "</p><ul>" + checks + "</ul><details><summary>来源与导入哈希</summary><pre>" + escape(json.dumps(result["provenance"], ensure_ascii=False, indent=2)) + "</pre></details></section>")
    unmeasured = "".join("<li>" + escape(item["id"]) + "：尚无导入数据，假设未测试。</li>" for item in report["unmeasured_variants"])
    label = "合成演示 · 非真实投放效果" if report["synthetic"] else "本地效果回填"
    return "<!doctype html><html lang='zh-CN'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>创意效果复盘</title><style>body{font-family:system-ui,sans-serif;max-width:1100px;margin:32px auto;padding:0 20px;background:#f5f6f8;color:#182532;line-height:1.65}section,header{background:white;border:1px solid #d9e0e7;border-radius:12px;padding:22px;margin:18px 0}h1,h2{line-height:1.25}table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;border-bottom:1px solid #dce2e8;padding:9px;overflow-wrap:anywhere}.context,pre{font-size:12px;overflow-wrap:anywhere;white-space:pre-wrap}a{color:#125f87}.label{font-weight:bold;color:#875400}details{margin-top:18px}</style><header><p class='label'>" + label + "</p><h1>创意效果复盘</h1><p>" + escape(report["boundary"]) + "</p><p>" + escape(report["comparison_note"]) + "</p><p>比值按累计分子除以累计分母计算；未知和零分母保留为空，不平均百分比。日期范围分别展示。</p><p><a href='report.json'>完整 JSON</a> · <a href='results.csv'>结果 CSV</a></p></header>" + ("".join(cards) or "<section><p>尚无效果数据。先导入真实报表并核对字段映射。</p></section>") + ("<section><h2>未测试创意</h2><ul>" + unmeasured + "</ul></section>" if unmeasured else "") + "</html>"


def _csv_safe(value):
    text = "" if value is None else str(value)
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else text


def write_report(state, output):
    report = report_data(state)
    output = _new_directory(output)
    (output / "report.json").write_bytes(_json_bytes(report))
    (output / "index.html").write_text(_html(report), encoding="utf-8")
    columns = ["variant_id", "product_id", "hypothesis", "changed_variable", "control_notes", "mode", "parent_variant_id", "synthetic", "sample_status", "comparison_group"]
    extra = sorted({key for result in report["results"] for key in result["cohort"]})
    totals = sorted({name for result in report["results"] for name in result["totals"]})
    metrics = sorted({name for result in report["results"] for name in result["metrics"]})
    columns += extra + ["total_" + name for name in totals] + ["ratio_" + name for name in metrics]
    with (output / "results.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writerow({column: _csv_safe(column) for column in columns})
        for result in report["results"]:
            row = {key: result["variant"].get(key) for key in columns[:7]}
            row["variant_id"] = result["variant"]["id"]
            row.update(result["cohort"])
            row.update(synthetic=result["synthetic"], sample_status=result["sample_assessment"]["status"], comparison_group=result["comparison_group"])
            row.update({"total_" + name: value for name, value in result["totals"].items()})
            row.update({"ratio_" + name: value["value"] for name, value in result["metrics"].items()})
            writer.writerow({key: _csv_safe(value) for key, value in row.items()})
    return {"status": "written", "output": str(output.resolve()), "results": len(report["results"]), "html": str((output / "index.html").resolve())}


def init_workspace(output):
    output = _new_directory(output)
    for source, target in (("feedback_mapping.example.json", "feedback_mapping.json"), ("feedback_registry.example.json", "feedback_registry.json"), ("feedback_export.example.csv", "feedback_export.csv")):
        shutil.copyfile(ASSETS / source, output / target)
    (output / "feedback_state.json").write_bytes(_json_bytes(_empty_state()))
    return {"status": "initialized", "output": str(output.resolve()), "next_step": "填写映射与创意 ID 后导入真实报表；也可将 --registry 指向 creative-workflow.json。"}


def demo(output):
    output = Path(output)
    init_workspace(output)
    config = _read_json(output / "feedback_mapping.json")
    config["synthetic"] = True
    config["sample_criteria"] = {"impressions": 1000, "conversions": 20}
    config["creative_map"] = [{"platform": "synthetic-platform", "account": "synthetic-account", "creative_id": "demo-ad-a", "variant_id": "variant-explore-01"}, {"platform": "synthetic-platform", "account": "synthetic-account", "creative_id": "demo-ad-b", "variant_id": "variant-iterate-01"}]
    variants = [
        {"id": "variant-explore-01", "product_id": "demo-product", "hypothesis": "合成假设：开头展示使用过程后，点击与成交分别怎样变化？", "changed_variable": "开头表达", "control_notes": "合成数据仅演示；账户、商品、价格、入口和日期相同，未做随机对照。", "parent_variant_id": None, "mode": "exploration"},
        {"id": "variant-iterate-01", "product_id": "demo-product", "hypothesis": "合成假设：保持承接条件时，改换开头画面是否出现不同观察？", "changed_variable": "开头演示画面", "control_notes": "合成数据仅演示；不能从点击差异推出成交优势。", "parent_variant_id": "variant-explore-01", "mode": "iteration"},
    ]
    (output / "feedback_mapping.json").write_bytes(_json_bytes(config))
    (output / "feedback_registry.json").write_bytes(_json_bytes({"synthetic": True, "variants": variants}))
    fields = list(config["columns"].values()) + [item["column"] for item in config["numeric_fields"].values()]
    with (output / "feedback_export.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for creative_id, clicks, conversions in (("demo-ad-a", "20", "2"), ("demo-ad-b", "40", "2")):
            canonical = {"platform": "synthetic-platform", "creative_id": creative_id, "date": "2026-10-20", "account": "synthetic-account", "goal": "商品成交", "attribution_window": "点击7日", "currency": "CNY", "price": "99", "entry": "合成商品页", "placement": "合成信息流"}
            row = {config["columns"][key]: value for key, value in canonical.items()}
            counters = {"impressions": "1000", "qualified_views": "300", "clicks": clicks, "conversions": conversions, "spend": "80", "revenue": "198"}
            row.update({config["numeric_fields"][key]["column"]: value for key, value in counters.items()})
            writer.writerow(row)
    receipt = import_data(output / "feedback_state.json", output / "feedback_export.csv", output / "feedback_mapping.json", output / "feedback_registry.json")
    report = write_report(output / "feedback_state.json", output / "report")
    return {"status": "synthetic_demo", "synthetic": True, "import": receipt, "report": report}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "demo"):
        command = commands.add_parser(name)
        command.add_argument("--output", type=Path, required=True)
    command = commands.add_parser("import")
    command.add_argument("--state", type=Path, required=True)
    command.add_argument("--csv", dest="csv_path", type=Path, required=True)
    command.add_argument("--mapping", dest="mapping_path", type=Path, required=True)
    command.add_argument("--registry", dest="registry_path", type=Path, required=True)
    command.add_argument("--operation", choices=("append", "upsert"), default="append")
    command.add_argument("--reason")
    command = commands.add_parser("report")
    command.add_argument("--state", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    args = vars(parser.parse_args(argv))
    name = args.pop("command")
    try:
        result = {"init": init_workspace, "import": import_data, "report": write_report, "demo": demo}[name](**args)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (ValueError, OSError, UnicodeError, InvalidOperation) as exc:
        parser.exit(2, "error: " + str(exc) + "\n")


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    raise SystemExit(main())
