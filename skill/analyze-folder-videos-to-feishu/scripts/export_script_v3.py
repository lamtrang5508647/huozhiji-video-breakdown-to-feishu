#!/usr/bin/env python3
"""Validate and export complete continuous source-script segments.

Dry-run is default. This script is local-only and never contacts Feishu.
Completed identical builds are idempotent; different or partial builds are not
overwritten and must use a new revision/output directory.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from validate_script_v3 import InvalidManifest, read_json, validate
from emotion_progression import manifest_emotion, progression_label, event_label
from script_shot_alignment import segment_alignment, render_alignment_table, write_alignment_files


ENGINE = "script-v3-export/1.3"
REPORT_FILES = ("index.html", "README.md", "segments.csv", "export_manifest.json",
                "script-shot-alignment.json", "script-shot-alignment.csv")

# Each button uses offsets inside this semantic clip, not source-video time.
ALIGNMENT_PLAYER_SCRIPT = r'''
document.querySelectorAll('button[data-player][data-start][data-end]').forEach(button => {
  button.addEventListener('click', () => {
    const player = document.getElementById(button.dataset.player);
    if (!player) return;
    const start = Number(button.dataset.start), end = Number(button.dataset.end);
    if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end <= start) return;
    document.querySelectorAll('video').forEach(other => { if (other !== player) other.pause(); });
    player.dataset.alignmentEnd = String(end);
    player.dataset.alignmentStart = String(start);
    const seek = () => {
      const latestStart = Number(player.dataset.alignmentStart);
      if (!Number.isFinite(latestStart)) return;
      player.currentTime = latestStart;
      const attempt = player.play();
      if (attempt && attempt.catch) attempt.catch(() => {});
    };
    if (player.readyState >= 1) seek();
    else player.addEventListener('loadedmetadata', seek, {once: true});
  });
});
document.querySelectorAll('video').forEach(player => {
  player.addEventListener('timeupdate', () => {
    const end = Number(player.dataset.alignmentEnd);
    if (player.dataset.alignmentEnd && player.currentTime >= end) {
      player.pause(); delete player.dataset.alignmentEnd;
    }
  });
});
'''


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def run(command: list[str]) -> subprocess.CompletedProcess:
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        raise InvalidManifest(f"{Path(command[0]).name} failed: {result.stderr[-3500:]}")
    return result


def probe(path: Path, ffprobe: str) -> dict:
    result = run([ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)])
    data = json.loads(result.stdout)
    duration = float(data.get("format", {}).get("duration", "nan"))
    if not math.isfinite(duration) or duration <= 0:
        raise InvalidManifest(f"invalid media duration: {path.name}")
    return data


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def build_id(data: dict) -> str:
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256((ENGINE + "\n" + canonical).encode("utf-8")).hexdigest()


def safe_name(value: str) -> str:
    # IDs are validated but still normalize to portable ASCII filenames.
    import re
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-_") or "segment"
    return stem[:64]


def timecode(ms: int) -> str:
    seconds, milli = divmod(ms, 1000)
    minutes, second = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    return f"{hours:02d}:{minute:02d}:{second:02d}.{milli:03d}"


def verify_clip(path: Path, expected_sec: float, source_has_audio: bool, ffmpeg: str, ffprobe: str) -> dict:
    data = probe(path, ffprobe)
    streams = data.get("streams", [])
    video = [s for s in streams if s.get("codec_type") == "video"]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if len(video) != 1 or video[0].get("codec_name") != "h264":
        raise InvalidManifest(f"expected one H.264 video stream in {path.name}")
    if source_has_audio and (len(audio) != 1 or audio[0].get("codec_name") != "aac"):
        raise InvalidManifest(f"source audio was not preserved as AAC in {path.name}")
    if not source_has_audio and audio:
        raise InvalidManifest(f"silent source unexpectedly has an audio stream in {path.name}")
    duration = float(data["format"]["duration"])
    fps_text = video[0].get("avg_frame_rate", "30/1")
    try:
        num, den = fps_text.split("/")
        fps = float(num) / float(den)
    except (ValueError, ZeroDivisionError):
        fps = 30.0
    tolerance = max(0.15, 2 / fps if fps > 0 else 0.15)
    if abs(duration - expected_sec) > tolerance:
        raise InvalidManifest(f"duration mismatch for {path.name}: expected {expected_sec:.3f}, got {duration:.3f}s")
    stream_duration = {}
    for stream in video + audio:
        value = stream.get("duration")
        try:
            seconds = float(value)
        except (TypeError, ValueError) as exc:
            raise InvalidManifest(f"stream duration missing for {path.name}") from exc
        if abs(seconds - expected_sec) > tolerance or seconds <= 0:
            raise InvalidManifest(f"{stream['codec_type']} stream duration mismatch in {path.name}")
        stream_duration[stream["codec_type"]] = seconds
    run([ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-xerror", "-i", str(path), "-map", "0:v:0", "-map", "0:a:0?", "-f", "null", "-"])
    return {
        "container_duration_sec": duration,
        "expected_duration_sec": expected_sec,
        "stream_duration_sec": stream_duration,
        "video_codec": video[0]["codec_name"],
        "audio_codec": audio[0]["codec_name"] if audio else None,
        "fully_decoded": True,
        "sha256": sha256(path),
        "size_bytes": path.stat().st_size,
    }


def render_html(data: dict) -> str:
    title = html.escape(data.get("title", data["video_id"]))
    whole = data["whole_video"]
    framework = "".join(
        f"<li><b>{item['order']}．{html.escape('、'.join(item['source_groups']))}</b>：{html.escape(item['function'])}</li>"
        for item in whole["framework"]
    )
    whole_analysis = f"""<section class="card"><h2>整片框架</h2>
<p><b>源片策略：</b>{html.escape(whole['strategy'])}</p><ol>{framework}</ol>
<p><b>源片可复用结构：</b>{html.escape(whole['reusable'])}</p>
<p><b>目标产品改编路线：</b>{html.escape(whole['replication_outline'])}</p>
<p><b>段间衔接：</b>{html.escape(whole['transition_logic'])}</p>
<p><b>事实边界：</b>{html.escape('；'.join(whole['claim_boundaries']))}</p>
<p><b>受众定位假设：</b>{html.escape(whole['audience_hypothesis'])}</p></section>"""
    cards = []
    for segment in data["segments"]:
        name = html.escape(segment["name"])
        role = html.escape(segment["role"])
        emotion = manifest_emotion(segment)
        hypothesis = html.escape(segment["emotion"].get("audience_hypothesis") or "未提供受众反应假设")
        emotion_status = progression_label(segment["emotion"]) or "旧四阶段观察记录（不代表独立内容复核通过）"
        emotion_events = " → ".join(
            f"{event_label(event, index)} {timecode(event['evidence_time_ms'])}：{event['observable']}"
            + "（" + "、".join(event['evidence_shot_ids']) + "）"
            for index, event in enumerate(emotion["events"])) or "未明确，暂无可核对事件"
        sid = html.escape(segment["id"])
        start, end = segment["start_ms"], segment["end_ms"]
        tags = "　".join(html.escape(t) for t in segment["emotion_tags"])
        groups = "、".join(html.escape(x) for x in segment["source_group_ids"])
        sources = "、".join(html.escape(x) for x in segment["source_shot_ids"])
        alignment = render_alignment_table(segment_alignment(segment), player_id="player-" + segment["id"])
        formula = segment["reusable_formula"]
        shot_plan = "".join(
            "<li><b>" + html.escape(group["source_group_id"]) + "｜源片：</b>" + html.escape(group["source_observation"])
            + "<br><b>复刻建议：</b>主体/动作：" + html.escape(group["replication_shooting_plan"]["subject_action"])
            + "；景别/视角：" + html.escape(group["replication_shooting_plan"]["shot_size_angle"])
            + "；句式槽：" + html.escape(group["replication_shooting_plan"]["example_sentence_slots"])
            + "；信息顺序：" + html.escape(group["replication_shooting_plan"]["information_order"])
            + "；剪辑衔接：" + html.escape(group["replication_shooting_plan"]["edit_transition"])
            + "</li>"
            for group in segment["shot_groups"]
        )
        excerpt_items = "".join(
            f"<li>{html.escape(item['shot_id'])} {item['intersection_start_ms']}–{item['intersection_end_ms']}ms：{html.escape(item['text'])}"
            + ("（仅作边界上下文）" if item.get("context_only") else "") + "</li>"
            for item in segment["source_excerpt"]["items"]
        )
        model = segment["model_suitability"]
        model_info = "".join(
            f"<p><b>{label}：</b>{html.escape('；'.join(model[key]))}</p>"
            for key, label in (("generatable", "可生成候选"), ("reference_constrained", "真实参考约束"), ("real_evidence_required", "必须使用真实证据"))
        )
        boundary_context = "；".join(
            f"{item['source_group_id']} {','.join(item['source_shot_ids'])}（仅交界画面）"
            for item in segment.get("boundary_context", [])
        ) or "无"
        cards.append(f"""<article class="card" id="{sid}">
  <h2>{segment['ordinal']:02d}. {name}</h2>
  <p class="time">{timecode(start)} – {timecode(end)}　({(end-start)/1000:.3f}秒)</p>
  <img loading="lazy" src="thumbnails/{sid}.jpg" alt="{name}缩略图">
  <video id="player-{sid}" controls preload="metadata" playsinline src="clips/{sid}.mp4"></video>
  <p><b>段落作用：</b>{role}</p>
  <p><b>情绪表达标签：</b>{tags or '未明确'}</p>
  <p><b>原片情绪观察：</b>{html.escape(emotion_status)}；{html.escape(emotion_events)}</p>
  <p><b>受众反应假设（非测量）：</b>{hypothesis}</p>
  <p><b>源组/镜头：</b>{groups}；{sources}</p>
  <h3>脚本与镜头对齐</h3>
  <p>时间表示本段与画面的交集；字幕摘录不是完整口播，也不代表逐句原音时间已校准。仿写槽位按段、镜头建议按原镜头组提供。</p>
  {alignment}
  <details><summary>查看源结构、复刻脚本槽位、镜头与模型建议</summary>
    <p><b>源片结构：</b>{html.escape(segment['source_structure'])}</p>
    <p><b>可复用句式结构：</b>{html.escape(formula['structure'])}</p>
    <p><b>填槽示例：</b>{html.escape(formula['example_sentence_slots'])}</p>
    <p><b>适用条件：</b>{html.escape(formula['conditions'])}</p>
    <p><b>必须补齐的事实：</b>{html.escape('；'.join(formula['required_evidence']))}</p>
    <p><b>镜头组（源事实与新拍分开）：</b></p><ol>{shot_plan}</ol>
    <p><b>跨语义组边界上下文：</b>{html.escape(boundary_context)}</p>
    {model_info}
    <p><b>字幕摘录索引（非完整口播）：</b></p><ul>{excerpt_items}</ul>
  </details>
</article>""")
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}｜脚本段导航</title><style>
body{{font-family:system-ui,'Microsoft YaHei',sans-serif;max-width:980px;margin:auto;padding:20px;background:#f5f6f8;color:#222}}
h1{{line-height:1.35}} .notice{{padding:12px 16px;background:#fff4d7;border-left:4px solid #d99c18}}
.card{{background:white;border-radius:12px;padding:16px;margin:20px 0;box-shadow:0 2px 10px #0001}}
.time{{font-variant-numeric:tabular-nums;color:#555}} video{{width:100%;max-height:70vh;background:#111;border-radius:8px}}
img{{width:240px;max-width:100%;display:block;margin:8px 0;border-radius:6px}} p{{line-height:1.65}}
.script-shot-alignment{{overflow-x:auto}} table{{border-collapse:collapse;width:100%;font-size:14px}} th,td{{padding:10px;border:1px solid #dbe0e8;text-align:left;vertical-align:top;min-width:140px;white-space:pre-wrap}} th{{background:#eef2f9}} button{{padding:7px 10px;cursor:pointer}} td p{{margin:0 0 8px}}
</style></head><body><h1>{title}｜脚本段导航</h1>
<p class="notice">口播状态：{html.escape(data['transcript_status'])}。源片主张未独立核实；情绪标签描述原片表达，受众反应是分析假设。</p>
<p><a href="script-shot-alignment.csv" download>下载脚本镜头对齐表（CSV）</a> · <a href="script-shot-alignment.json" download>完整来源与区间（JSON）</a></p>
{whole_analysis}{''.join(cards)}<script>{ALIGNMENT_PLAYER_SCRIPT}</script></body></html>"""


def render_csv(path: Path, data: dict) -> None:
    fields = ["video_id", "revision", "ordinal", "segment_id", "name", "start_ms", "end_ms", "start_time", "end_time", "duration_sec", "script_function", "emotion_tags", "role", "source_group_ids", "source_shot_ids", "source_shot_spans_json", "source_excerpt_json", "clip_path", "thumbnail_path", "audio_review_status"]
    fields += ["emotion_progression_status", "emotion_review_note", "emotion_events_json"]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for segment in data["segments"]:
            emotion = manifest_emotion(segment)
            writer.writerow({
                "video_id": data["video_id"], "revision": data["revision"],
                "ordinal": segment["ordinal"], "segment_id": segment["id"], "name": segment["name"],
                "start_ms": segment["start_ms"], "end_ms": segment["end_ms"],
                "start_time": timecode(segment["start_ms"]), "end_time": timecode(segment["end_ms"]),
                "duration_sec": f"{(segment['end_ms']-segment['start_ms'])/1000:.3f}",
                "script_function": segment["script_function"], "emotion_tags": json.dumps(segment["emotion_tags"], ensure_ascii=False),
                "role": segment["role"], "source_group_ids": json.dumps(segment["source_group_ids"], ensure_ascii=False),
                "source_shot_ids": json.dumps(segment["source_shot_ids"], ensure_ascii=False),
                "source_shot_spans_json": json.dumps(segment["source_shot_spans"], ensure_ascii=False),
                "source_excerpt_json": json.dumps(segment["source_excerpt"], ensure_ascii=False),
                "clip_path": f"clips/{segment['id']}.mp4", "thumbnail_path": f"thumbnails/{segment['id']}.jpg",
                "audio_review_status": segment["review_status"]["audio"],
                "emotion_progression_status": emotion["status"],
                "emotion_review_note": emotion["review_note"],
                "emotion_events_json": json.dumps(emotion["events"], ensure_ascii=False),
            })


def validate_existing(output: Path, expected_build_id: str, data: dict, source_has_audio: bool, ffmpeg: str, ffprobe: str) -> bool:
    receipt_path = output / "receipt.json"
    if not receipt_path.is_file():
        return False
    receipt = read_json(receipt_path)
    if receipt.get("build_id") != expected_build_id or receipt.get("status") != "complete":
        raise InvalidManifest("output contains a different or incomplete build; refusing to overwrite")
    records = {item["segment_id"]: item for item in receipt.get("segments", [])}
    if len(records) != len(data["segments"]):
        raise InvalidManifest("existing receipt does not cover all segments")
    for segment in data["segments"]:
        sid = segment["id"]
        record = records.get(sid)
        if not record:
            raise InvalidManifest(f"existing receipt lacks {sid}")
        clip = output / "clips" / f"{sid}.mp4"
        thumb = output / "thumbnails" / f"{sid}.jpg"
        if not clip.is_file() or sha256(clip) != record["clip_sha256"]:
            raise InvalidManifest(f"existing clip missing or changed: {sid}")
        if not thumb.is_file() or sha256(thumb) != record["thumbnail_sha256"]:
            raise InvalidManifest(f"existing thumbnail missing or changed: {sid}")
        verify_clip(clip, (segment["end_ms"] - segment["start_ms"]) / 1000, source_has_audio, ffmpeg, ffprobe)
    required = [*REPORT_FILES, "export_status.json"]
    if any(not (output / name).is_file() for name in required):
        raise InvalidManifest("existing completed export is missing a required navigation/report file")
    expected_reports = set(REPORT_FILES)
    report_hashes = receipt.get("report_sha256")
    if not isinstance(report_hashes, dict) or set(report_hashes) != expected_reports:
        raise InvalidManifest("existing receipt lacks the complete report hash set")
    for relative_path, expected_hash in report_hashes.items():
        report_path = output / relative_path
        if not report_path.is_file() or sha256(report_path) != expected_hash:
            raise InvalidManifest(f"existing report missing or changed: {relative_path}")
    status = read_json(output / "export_status.json")
    if status.get("status") != "complete" or status.get("completed_segments") != len(data["segments"]):
        raise InvalidManifest("existing export_status.json is not complete")
    return True


def render(output: Path, manifest_path: Path, data: dict, build: str, ffmpeg: str, ffprobe: str, root_review_path: Path) -> dict:
    source = Path(data["source"]["path"])
    if not source.is_absolute():
        source = (manifest_path.parent / source).resolve()
    source_info = probe(source, ffprobe)
    source_has_audio = any(s.get("codec_type") == "audio" for s in source_info.get("streams", []))
    if output.exists() and any(output.iterdir()):
        if validate_existing(output, build, data, source_has_audio, ffmpeg, ffprobe):
            return {"status": "complete_idempotent_reuse", "output": str(output), "build_id": build, "segments": len(data["segments"])}
        raise InvalidManifest(f"output is nonempty without a matching complete receipt; refusing overwrite: {output}")
    output.mkdir(parents=True, exist_ok=True)
    clips_dir, thumbs_dir = output / "clips", output / "thumbnails"
    clips_dir.mkdir(exist_ok=True)
    thumbs_dir.mkdir(exist_ok=True)
    status = {"status": "rendering", "build_id": build, "video_id": data["video_id"], "total_segments": len(data["segments"]), "completed_segments": 0}
    write_json(output / "export_status.json", status)
    output_records = []
    try:
        for segment in data["segments"]:
            sid = segment["id"]
            clip, thumb = clips_dir / f"{sid}.mp4", thumbs_dir / f"{sid}.jpg"
            partial = clips_dir / f".{sid}.partial.mp4"
            start, end = segment["start_ms"] / 1000, segment["end_ms"] / 1000
            duration = end - start
            if partial.exists() or clip.exists() or thumb.exists():
                raise InvalidManifest(f"unexpected partial output exists: {sid}")
            command = [
                ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-n",
                "-ss", f"{start:.6f}", "-i", str(source), "-t", f"{duration:.6f}",
                "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn",
                "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2,setpts=PTS-STARTPTS",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
            ]
            if source_has_audio:
                command += ["-af", "asetpts=PTS-STARTPTS", "-c:a", "aac", "-b:a", "192k"]
            command += ["-movflags", "+faststart", "-map_metadata", "-1", str(partial)]
            run(command)
            verification = verify_clip(partial, duration, source_has_audio, ffmpeg, ffprobe)
            partial.replace(clip)
            midpoint = duration / 2
            run([ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-n", "-ss", f"{midpoint:.6f}", "-i", str(clip), "-map", "0:v:0", "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "2", str(thumb)])
            if not thumb.is_file() or thumb.stat().st_size < 100:
                raise InvalidManifest(f"thumbnail missing/empty: {sid}")
            record = {
                "segment_id": sid, "clip_sha256": sha256(clip), "thumbnail_sha256": sha256(thumb),
                "verification": verification,
            }
            output_records.append(record)
            status["completed_segments"] = len(output_records)
            write_json(output / "export_status.json", status)
            print(f"verified {len(output_records)}/{len(data['segments'])}: {sid}", file=sys.stderr, flush=True)

        if sha256(source) != data["source"]["sha256"]:
            raise InvalidManifest("source SHA-256 changed during export")
        rendered_data = json.loads(json.dumps(data, ensure_ascii=False))
        record_by_id = {r["segment_id"]: r for r in output_records}
        for segment in rendered_data["segments"]:
            segment["export"] = record_by_id[segment["id"]]
        rendered_data["export"] = {"engine": ENGINE, "build_id": build, "created_at_utc": datetime.now(timezone.utc).isoformat(), "status": "complete"}
        write_json(output / "export_manifest.json", rendered_data)
        render_csv(output / "segments.csv", data)
        write_alignment_files(output, data)
        (output / "index.html").write_text(render_html(data), encoding="utf-8")
        readme = f"""# {data.get('title', data['video_id'])} 脚本段拆解

本地导出共 {len(data['segments'])} 个连续源片脚本段，覆盖 00:00:00.000–{timecode(round(data['duration_sec']*1000))}。原视频为 {data['source']['duration_sec']:.3f} 秒，原始分辨率保持 {source_info['streams'][0].get('width')}×{source_info['streams'][0].get('height')}，输出不宣称高清。

打开 `index.html` 可以逐段播放视频、看缩略图，并在脚本与镜头对齐表中定位各画面的交集。`script-shot-alignment.csv` 便于并排查看源文、段级仿写槽和镜头组建议；JSON 保留源片/时间线哈希、精确交集与偏移。它们不代表逐句音频对齐，原文与改写建议分开。

`export_manifest.json` 保留完整框架、情绪证据、受众假设、镜头组与源镜头交集；`segments.csv` 便于表格查看；`receipt.json` 记录源文件和导出文件校验值。

## 校对边界

转写状态：{data['transcript_status']}。音频复核状态：{data.get('audio_review_status', '未记录；不据此推定已听校')}。具体切分依据、可见字幕和逐段证据以当前 manifest 为准；媒体导出成功不等于完成听写或语义复核。业务分类“{data['source'].get('business_product', '未指定')}”和片中可见品牌“{data['source'].get('visible_brand', '未记录')}”分开记录，不据此推定为同款商品。原片陈述是否核实见相应来源与审核记录。

## 文件

- `clips/`：保留原片顺序和原声的 H.264/AAC 连续脚本段 MP4。
- `thumbnails/`：各段中点缩略图。
- `index.html`：本地播放导航页。
- `segments.csv`：段落索引及可追溯的源镜头号。
- `script-shot-alignment.csv` / `.json`：脚本段 × 镜头交集、原字幕与仿写/拍法对照。
- `export_manifest.json`：完整结构化元数据与逐段验证结果。
- `receipt.json`：幂等重跑指纹、哈希与媒体验证结果。
"""
        (output / "README.md").write_text(readme, encoding="utf-8")
        receipt = {
            "status": "complete", "engine": ENGINE, "build_id": build,
            "manifest_sha256": sha256(manifest_path), "source_sha256": sha256(source),
            "timeline_sha256": data["source"].get("timeline_manifest_sha256"),
            "review_receipt_sha256": sha256(root_review_path),
            "report_sha256": {
                relative: sha256(output / relative)
                for relative in REPORT_FILES
            },
            "created_at_utc": datetime.now(timezone.utc).isoformat(), "segments": output_records,
        }
        write_json(output / "receipt.json", receipt)
        status.update({"status": "complete", "completed_segments": len(output_records), "source_sha256": sha256(source)})
        write_json(output / "export_status.json", status)
        return {"status": "complete", "output": str(output), "build_id": build, "segments": len(data["segments"]), "source_sha256": sha256(source)}
    except BaseException as exc:
        status.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}", "completed_segments": len(output_records)})
        write_json(output / "export_status.json", status)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--timeline", type=Path)
    parser.add_argument("--root-review", "--review-receipt", dest="root_review", type=Path, help="required with --render; analyst review receipt bound to manifest SHA")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--render", action="store_true", help="perform the source-video export; default is validate-only")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        manifest_path = args.manifest.resolve()
        data = read_json(manifest_path)
        validation = validate(data, manifest_path, args.timeline.resolve() if args.timeline else None, args.ffprobe, args.root_review, args.render)
        if not args.render:
            print(json.dumps({"status": "dry_run_validated", "output": str(args.output.resolve()), "build_id": build_id(data), **validation}, ensure_ascii=False, indent=2))
            return 0
        if args.root_review is None:
            raise InvalidManifest("--render requires --review-receipt (alias: --root-review)")
        result = render(args.output.resolve(), manifest_path, data, build_id(data), args.ffmpeg, args.ffprobe, args.root_review.resolve())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, StopIteration, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("ERROR: interrupted; partial files/status preserved; use a new revision/output path", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
