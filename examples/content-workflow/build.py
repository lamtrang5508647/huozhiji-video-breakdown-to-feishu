#!/usr/bin/env python3
"""Offline, original synthetic walkthrough of source -> cards -> measured-data mapping.

No course documents, customer media, advertising accounts or remote calls are used.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'skill/analyze-folder-videos-to-feishu/scripts'
sys.path.insert(0, str(SCRIPTS))
import creative_workflow as creative
import performance_feedback as feedback
import workflow_batch as batch


def build(output):
    output = Path(output).resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError('Output must be new or empty; existing work is preserved')
    output.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location('synthetic_media_demo', ROOT / 'examples/synthetic/build.py')
    media = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(media)
    media.build(output / 'source', SCRIPTS)
    workspace = output / 'batch'
    job = batch.add_sources(workspace, [output / 'source/source.mp4'], 'synthetic-course-workflow')['jobs'][0]
    batch.attach(workspace, job['source_id'], output / 'source/script_manifest.json', output / 'source/demo-review.json')
    exported = batch.run_batch(workspace, through='export')
    if exported['jobs'][0]['state'] != 'local_export_complete':
        raise ValueError('Actual synthetic batch export failed: ' + json.dumps(exported))
    before = batch.load_state(workspace)['jobs'][job['source_id']]['stages']['export']['attempts']
    batch.run_batch(workspace, through='export')
    after = batch.load_state(workspace)['jobs'][job['source_id']]['stages']['export']['attempts']
    if before != after:
        raise ValueError('Resume repeated the completed export')
    creative.create_demo(output / 'creative')
    bundle_path = output / 'creative/creative-workflow.json'
    bundle = creative.read_json(bundle_path)
    bundle['sources'].append(creative.bind_source(output / 'source/script_manifest.json', 'demo-product', 'source-002'))
    batch.atomic(bundle_path, bundle)
    validation = creative.validate_bundle(bundle, bundle_path.parent)
    creative.write_report(bundle_path, output / 'creative/linked-report')
    feedback.demo(output / 'feedback-example')
    feedback.init_workspace(output / 'feedback-linked')
    state = output / 'feedback-linked/feedback_state.json'
    args = (state, output / 'feedback-example/feedback_export.csv', output / 'feedback-example/feedback_mapping.json', bundle_path)
    imported = feedback.import_data(*args)
    digest = hashlib.sha256(state.read_bytes()).hexdigest()
    repeated = feedback.import_data(*args)
    if hashlib.sha256(state.read_bytes()).hexdigest() != digest:
        raise ValueError('Repeated identical CSV import changed state')
    feedback.write_report(state, output / 'feedback-linked/report')
    report = feedback.report_data(state)
    if len(report['results']) != 2 or not report['synthetic']:
        raise ValueError('Expected exactly two explicitly synthetic linked variants')
    (output / 'index.html').write_text('''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>课程方法工作流演示</title>
<style>body{max-width:850px;margin:60px auto;padding:24px;font:18px/1.8 system-ui;background:#f6f7fa;color:#172638}li{padding:12px}a{color:#125f87}</style>
<h1>从拆解到创作，再到复盘</h1><p>原创合成演示：没有真实商品、客户视频或投放效果。</p>
<ol><li><a href="source/analysis.html">看原始脚本与画面对齐</a>：4 秒色卡，有实际编码媒体。</li>
<li><a href="creative/linked-report/report.html">看创作工作台与四张任务卡</a>：主张、证据、候选展示、拍摄验收与未测试版本。</li>
<li><a href="feedback-linked/report/index.html">看报表回填与下一轮检查</a>：同一批创意 ID，点击增加而成交不变的合成案例。</li>
<li><a href="verification.json">查看验证记录</a>：真实切片、两类导出、恢复与重复导入。</li></ol>
<p>程序通过代表文件、引用和流程检查通过。真实内容仍需看片、听校和事实复核；业务效果需要实际报表。</p></html>''', encoding='utf-8')
    receipt = {'status': 'PASS', 'synthetic': True, 'source_video': str(output / 'source/source.mp4'),
               'batch_export': exported, 'resume_reused_export': before == after,
               'creative_validation': validation, 'feedback_import': imported, 'feedback_repeat': repeated,
               'feedback_results': len(report['results']), 'remote_access_attempted': False,
               'human_listening_performed': False, 'real_advertising_effect_verified': False}
    batch.atomic(output / 'verification.json', receipt)
    return {'status': 'PASS', 'synthetic': True, 'index': str(output / 'index.html'), 'receipt': str(output / 'verification.json')}


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    print(json.dumps(build(parser.parse_args().output), ensure_ascii=False, indent=2))
