"""Self-authored synthetic media fixture. No real source content or network calls."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

STAGES = ("起点", "触发", "转折", "落点")
NOTICE = "Synthetic fixture only: RED/BLUE text is drawn into generated frames; audio is a test tone, not speech. No human source review, factual verification, or Feishu publication is claimed."
GLYPHS = {
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(argv):
    result = subprocess.run([str(x) for x in argv], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    if result.returncode:
        raise RuntimeError(f"Command failed: {Path(str(argv[0])).name}\n{result.stderr[-6000:]}\n{result.stdout[-3000:]}")
    return result


def ppm(path, color, text):
    """Tiny built-in glyphs avoid host fonts and external imaging dependencies."""
    width, height, scale = 320, 180, 6
    pixels = bytearray(bytes(color) * (width * height))
    left = (width - (len(text) * 6 - 1) * scale) // 2
    top = (height - 7 * scale) // 2
    for letter_index, letter in enumerate(text):
        for row_index, row in enumerate(GLYPHS[letter]):
            for column, bit in enumerate(row):
                if bit != "1":
                    continue
                for dy in range(scale):
                    for dx in range(scale):
                        offset = ((top + row_index * scale + dy) * width + left + (letter_index * 6 + column) * scale + dx) * 3
                        pixels[offset:offset + 3] = b"\xff\xff\xff"
    path.write_bytes(f"P6\n{width} {height}\n255\n".encode("ascii") + pixels)


def fixture(output, annotation):
    source = {"path": "source.mp4", "sha256": sha(output / "source.mp4"), "duration_sec": 4.0,
              "business_product": "None — synthetic fixture",
              "platform": "synthetic", "url": "https://example.invalid/video/synthetic-colors-001",
              "visible_brand": "None — synthetic fixture", "evidence_kind": "generated_color_cards_with_burned_in_text"}
    timeline = {"video_id": "synthetic-colors-001", "revision": "synthetic-v1", "duration_sec": 4.0,
                "source": source.copy(), "coverage": "full", "review_status": "complete",
                "review_scope": "Synthetic construction checks only; no human source-video review", "segments": [], "semantic_groups": []}
    for index, text in enumerate(("RED", "BLUE"), 1):
        sid = f"shot-{index:04d}"
        timeline["segments"].append({"id": sid, "start_ms": (index - 1) * 2000, "end_ms": index * 2000,
                                     "reviewed": True, "subtitle_excerpt": text, "description": f"Synthetic {text} card, authored text embedded in pixels",
                                     "tags": ["synthetic", text.lower()], "evidence": [f"card-{index}.ppm", "construction.json"]})
        timeline["semantic_groups"].append({"id": f"G{index:02d}", "name": f"{text} card", "source_shot_ids": [sid]})
    save(output / "timeline.json", timeline)
    source.update(timeline_manifest_path="timeline.json", timeline_manifest_sha256=sha(output / "timeline.json"))
    manifest = {"schema_version": "script-v3.1", "video_id": timeline["video_id"], "revision": "synthetic-v1",
                "title": "Synthetic RED to BLUE demo", "core_question": "Can two authored color cards pass the local export and mapping checks?",
                "source": source, "duration_sec": 4.0, "coverage": "full",
                "transcript_status": "Synthetic burned-in labels only; no spoken transcript",
                "audio_review_status": "synthetic_test_tone_no_speech", "emotion_tag_options": ["未明确"],
                "script_function_options": ["合成画面展示"], "segments": [],
                "whole_video": {"strategy": "Two authored color cards verify two-layer export wiring.",
                    "framework": [{"order": i, "source_groups": [f"G{i:02d}"], "function": f"Show {t} card"} for i, t in enumerate(("RED", "BLUE"), 1)],
                    "reusable": "[First color card] then [second color card]", "replication_outline": "Replace only with authorized footage.",
                    "transition_logic": "Hard cut at exactly two seconds", "claim_boundaries": [NOTICE],
                    "audience_hypothesis": "None assessed for this technical fixture."}}
    analysis = {"annotation_profile": annotation.PROFILE, "taxonomy": annotation.canonical_taxonomy(),
                "video_id": timeline["video_id"], "title": manifest["title"], "text_kind": "subtitle_excerpts",
                "disclosure": NOTICE, "sections": [], "units": [], "source_formula": [], "reusable_formula": []}
    content = {"script_shot_alignment": True, "visible_brand": "None — synthetic fixture", "replication_outline": "Synthetic technical example only",
               "audience_hypothesis": "Not measured", "claim_boundaries": [NOTICE], "sections": []}
    def runs(text):
        return [{"text": text, "char_start": 0, "char_end": len(text), "category_family": "unclassified",
                 "function_tag": "合成色卡字样", "reason": "Authored technical fixture label; no advertising or semantic-quality claim."}]
    for index, label in enumerate(("RED", "BLUE"), 1):
        sid, shot, group, unit = f"S{index:02d}", f"shot-{index:04d}", f"G{index:02d}", f"unit-{index:02d}"
        start, end = (index - 1) * 2000, index * 2000
        arc = [{"stage": stage, "observable": f"Static {label} card; no emotional change asserted.",
                "evidence_time_ms": start + offset * 400, "evidence_shot_ids": [shot]} for offset, stage in enumerate(STAGES)]
        formula_text = f"[Color card {index}]"
        segment = {"id": sid, "ordinal": index, "name": f"{label} card", "start_ms": start, "end_ms": end,
            "role": "Synthetic label demonstration", "core_question": f"Is the authored {label} label preserved in the exported card?",
            "script_function": "合成画面展示", "emotion_tags": ["未明确"],
            "source_group_ids": [group], "source_shot_ids": [shot],
            "source_shot_spans": [{"shot_id": shot, "source_shot_start_ms": start, "source_shot_end_ms": end,
                                   "segment_intersection_start_ms": start, "segment_intersection_end_ms": end}],
            "source_excerpt": {"type": "subtitle_excerpts_only", "provenance": "Authored text is burned into the synthetic source frames", "items": [
                {"shot_id": shot, "intersection_start_ms": start, "intersection_end_ms": end, "text": label}]},
            "emotion": {"observable_timeline": arc, "audience_hypothesis": "No audience response measured."},
            "source_structure": f"Display {label} for two seconds",
            "reusable_formula": {"structure": "[Color label]", "example_sentence_slots": formula_text,
                                 "conditions": "Synthetic demonstration only", "required_evidence": ["Own the replacement footage"]},
            "shot_groups": [{"source_group_id": group, "source_shot_ids": [shot], "source_observation": f"Generated {label} card",
                             "replication_shooting_plan": {"subject_action": "Static color card", "shot_size_angle": "Full frame",
                                 "example_sentence_slots": formula_text, "information_order": "One label", "edit_transition": "Hard cut"}}],
            "model_suitability": {"generatable": [], "reference_constrained": [], "real_evidence_required": ["No generation quality was tested"]},
            "review_status": {"audio": "synthetic_test_tone_no_speech"}}
        manifest["segments"].append(segment)
        analysis["sections"].append({"id": sid, "name": segment["name"], "start_ms": start, "end_ms": end,
            "time_range": f"{start / 1000:.1f}–{end / 1000:.1f}s", "formula": formula_text, "formula_runs": runs(formula_text),
            "clip_url": f"semantic/clips/{sid}.mp4"})
        analysis["units"].append({"id": unit, "section_id": sid, "source_text": label, "source_shot_ids": [shot],
            "locate_ms": start, "locator_note": "Authored label visible in the generated source pixels", "phrase_runs": runs(label)})
        for name, text in (("source_formula", label), ("reusable_formula", formula_text)):
            analysis[name].append({"label": text, "section_id": sid, "source_unit_ids": [unit], "phrase_runs": runs(text)})
        content["sections"].append({"name": segment["name"], "start_ms": start, "end_ms": end, "function": segment["script_function"],
            "tags": ["未明确"], "structure": segment["source_structure"], "formula": formula_text, "formula_runs": runs(formula_text),
            "slots": formula_text, "conditions": "Synthetic fixture only", "shoot": "Generate a color card with embedded text",
            "audience": "No response measured", "materials": [f"card-{index}.ppm"], "family": "unclassified"})
    for name, value in (("source.json", source), ("script_manifest.json", manifest), ("analysis.json", analysis), ("sections-content.json", content)):
        save(output / name, value)
    save(output / "demo-review.json", {"manifest_sha256": sha(output / "script_manifest.json"),
         "content_and_provenance": "PASS", "reviewer": "synthetic-fixture-construction-check",
         "approved_actions": ["render continuous segment clips"], "scope": NOTICE,
         "human_review_performed": False, "semantic_quality_verified": False})
    return manifest


def build(output, scripts, ffmpeg="ffmpeg", ffprobe="ffprobe"):
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError(f"Demo output must be new or empty; refusing overwrite: {output}")
    run([ffmpeg, "-version"])
    run([ffprobe, "-version"])
    output.mkdir(parents=True, exist_ok=True)
    save(output / "construction.json", {"notice": NOTICE, "source_duration_sec": 4, "fps": 30,
         "cards": [{"text": "RED", "start_ms": 0, "end_ms": 2000}, {"text": "BLUE", "start_ms": 2000, "end_ms": 4000}],
         "audio": "440 Hz synthetic sine wave; no speech", "real_source_content_used": False})
    try:
        ppm(output / "card-1.ppm", (150, 20, 20), "RED")
        ppm(output / "card-2.ppm", (20, 45, 160), "BLUE")
        run([ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-n", "-loop", "1", "-framerate", "30", "-t", "2", "-i", output / "card-1.ppm",
             "-loop", "1", "-framerate", "30", "-t", "2", "-i", output / "card-2.ppm", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4",
             "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0,format=yuv420p[v]", "-map", "[v]", "-map", "2:a", "-t", "4",
             "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-movflags", "+faststart", output / "source.mp4"])
        sys.path.insert(0, str(scripts))
        import phrase_annotation
        manifest = fixture(output, phrase_annotation)
        def cli(name, *arguments):
            return run([sys.executable, "-B", scripts / name, *arguments])
        cli("export_reviewed_timeline.py", "--manifest", output / "timeline.json", "--output", output / "shots", "--render", "--ffmpeg", ffmpeg, "--ffprobe", ffprobe)
        arguments = ("--manifest", output / "script_manifest.json", "--timeline", output / "timeline.json", "--root-review", output / "demo-review.json",
                     "--output", output / "semantic", "--render", "--ffmpeg", ffmpeg, "--ffprobe", ffprobe)
        cli("export_script_v3.py", *arguments)
        repeat = cli("export_script_v3.py", *arguments)
        cli("render_script_analysis.py", "--input", output / "analysis.json", "--manifest", output / "script_manifest.json", "--output", output / "analysis.html")
        chapter_args = ("--analysis", output / "analysis.json", "--manifest", output / "script_manifest.json", "--content", output / "sections-content.json",
                        "--materials-url", "https://example.invalid/materials", "--segments-url", "https://example.invalid/segments", "--output", output / "chapter.xml")
        cli("render_platform_chapter.py", *chapter_args)
        xml_result = json.loads(cli("render_platform_chapter.py", *chapter_args, "--validate-only").stdout)
        tree = ET.fromstring("<chapter>" + (output / "chapter.xml").read_text(encoding="utf-8") + "</chapter>")
        shots = json.loads((output / "shots/timeline.json").read_text(encoding="utf-8"))
        exported = json.loads((output / "semantic/export_manifest.json").read_text(encoding="utf-8"))
        alignment = json.loads((output / "semantic/script-shot-alignment.json").read_text(encoding="utf-8"))
        if alignment['rows_count'] != 2 or alignment['source_sha256'] != manifest['source']['sha256']:
            raise RuntimeError('Synthetic alignment source identity or row count differs')
        if [row['source_text'] for row in alignment['rows']] != ['RED', 'BLUE']:
            raise RuntimeError('Synthetic alignment changed authored subtitle text')
        shot_clips = list((output / "shots/clips").glob("*.mp4"))
        semantic_clips = list((output / "semantic/clips").glob("*.mp4"))
        if len(shot_clips) != 2 or len(semantic_clips) != 2 or len(tree.findall("h1")) != 1 or len(tree.findall("h2")) != 5:
            raise RuntimeError("Unexpected synthetic export counts or chapter heading structure")
        if not all(s["verification"]["fully_decoded"] for s in shots["segments"]):
            raise RuntimeError("Shot decode receipt is incomplete")
        if sha(output / "source.mp4") != manifest["source"]["sha256"]:
            raise RuntimeError("Source changed during demo")
        receipt = {"status": "PASS", "scope": "Synthetic offline integration: source hash, real encoded clips and decoding, HTML generation, exact local XML structure/styles. Not a real-video analysis or remote acceptance.",
            "output": str(output), "source_duration_sec": 4.0, "shot_clips": 2, "semantic_clips": 2,
            "xml_validation": xml_result, "repeat_export_result": repeat.stdout.strip(),
            "source_sha256": manifest["source"]["sha256"], "semantic_manifest_segments": len(exported["segments"]),
            "alignment_rows": alignment['rows_count'], "sentence_audio_alignment": alignment['sentence_audio_alignment'],
            "human_review_performed": False, "remote_access_attempted": False, "browser_playback_verified": False,
            "artifact_sha256": {str(p.relative_to(output)).replace("\\", "/"): sha(p) for p in sorted(output.rglob("*")) if p.is_file()}}
        save(output / "verification.json", receipt)
        return {"status": "PASS", "output": str(output), "receipt": str(output / "verification.json"),
                "source_video": str(output / "source.mp4"), "html": str(output / "analysis.html"),
                "shot_clips": 2, "semantic_clips": 2, "remote_access_attempted": False, "human_review_performed": False}
    except Exception as exc:
        save(output / "verification.json", {"status": "FAIL", "scope": NOTICE, "error": str(exc), "remote_access_attempted": False})
        raise
