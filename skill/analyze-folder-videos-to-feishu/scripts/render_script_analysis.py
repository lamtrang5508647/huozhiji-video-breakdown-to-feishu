"""Render a validated script annotation presentation as standalone local HTML.

This renderer does not transcribe, infer annotations, or publish to any service.
Its input is a reviewed presentation JSON with exact source text and phrase runs.
"""
from __future__ import annotations
import argparse
import html
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from phrase_annotation import is_v9, reading_runs, validate_annotations, validate_runs


# Preserve these source fields for validation; hide them only in the default
# display payload generated from a copy of the input.
OMITTED_DEFAULT_PRESENTATION_FIELDS = frozenset({
    'audience_hypothesis', 'claim_boundaries', 'adaptation_checks',
    'materials', 'material_requirements', 'assets_checklist',
})


def _default_presentation_copy(value):
    """Copy nested input while excluding fields for omitted display blocks."""
    if isinstance(value, dict):
        return {key: _default_presentation_copy(item) for key, item in value.items()
                if key not in OMITTED_DEFAULT_PRESENTATION_FIELDS}
    if isinstance(value, list):
        return [_default_presentation_copy(item) for item in value]
    return value


def validate(d):
    validate_annotations(d)
    assert d.get('title') and d.get('text_kind'), 'title and text_kind required'
    tax = {t['family_id']: t for t in d['taxonomy']}
    assert len(tax) == len(d['taxonomy']), 'duplicate taxonomy IDs'
    assert all(re.fullmatch(r'#[0-9a-fA-F]{6}', t['color']) for t in tax.values()), 'invalid color'
    sections = {s['id']: s for s in d['sections']}
    assert len(sections) == len(d['sections']), 'duplicate section IDs'
    for url in [s.get('clip_url', '') for s in d['sections']] + [x['url'] for x in d.get('links', [])]:
        assert urlsplit(url).scheme in {'', 'http', 'https'}, 'unsupported URL scheme'
    ids = set()
    for unit in d['units']:
        assert unit['id'] not in ids, 'duplicate unit ID'
        ids.add(unit['id'])
        assert unit['section_id'] in sections, 'unknown section'
        assert isinstance(unit['source_text'], str) and unit['source_text'], 'missing source text'
        runs = unit['phrase_runs']
        assert runs and ''.join(r['text'] for r in runs) == unit['source_text'], 'phrase runs changed source text'
        cursor = 0
        for run in runs:
            assert run['category_family'] in tax, 'unknown functional category'
            assert run['text'] and run['char_start'] == cursor, 'run gap, overlap, or empty run'
            cursor += len(run['text'])
            assert run['char_end'] == cursor, 'wrong character offsets'
            assert unit['source_text'][run['char_start']:run['char_end']] == run['text'], 'wrong original span'
            assert run.get('function_tag') and run.get('reason'), 'missing function or rationale'
        if d['text_kind'] == 'subtitle_excerpts':
            assert unit.get('source_shot_ids') and unit.get('locator_note'), 'subtitle provenance missing'
    assert d['units'], 'no annotated units'
    for group in ['source_formula', 'reusable_formula']:
        assert d.get(group), f'{group} missing'
        for step in d[group]:
            assert step.get('label') and (is_v9(d) or step.get('category_family') in tax), 'invalid formula step'
            if not is_v9(d) and 'phrase_runs' in step:
                validate_runs(step['label'], step['phrase_runs'], context=group, strict=False)
    for section in d['sections']:
        if 'formula_runs' in section:
            validate_runs(section['formula'], section['formula_runs'], context='section formula', strict=is_v9(d))
    return d


STYLE = r'''
:root{--ink:#192434;--muted:#64748b;--line:#e5e9f0;--paper:#fff;--bg:#f4f6fa;--accent:#315bea}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.7 "Microsoft YaHei","PingFang SC",sans-serif}button,a{touch-action:manipulation}button{font:inherit;cursor:pointer;border:1px solid var(--line);border-radius:8px;background:white;padding:7px 13px;color:var(--ink)}button:hover{background:#f0f4ff;border-color:#adc0fa}button:focus-visible,a:focus-visible{outline:3px solid #99b5ff;outline-offset:3px}a{color:#315bea;text-decoration:none}a:hover{text-decoration:underline}.app{max-width:1500px;margin:auto;padding:30px 34px 50px}header{display:flex;justify-content:space-between;align-items:flex-start;gap:25px;margin-bottom:22px}h1{font-size:29px;line-height:1.4;letter-spacing:-.5px;margin:0 0 8px}h2{font-size:19px;margin:0}h3{font-size:17px;margin:0}.muted{color:var(--muted)}.eyebrow{font-size:12px;font-weight:700;letter-spacing:1px;color:#4961a0;margin:0 0 7px}.badge{display:inline-block;padding:2px 9px;border-radius:5px;background:#e9edf5;color:#4c5970;font-size:12px;margin-right:7px}.note{background:#eff4ff;color:#445777;padding:12px 17px;border-left:3px solid #8da9ef;border-radius:5px;margin:18px 0}.layout{display:grid;grid-template-columns:300px minmax(0,1fr);gap:23px;align-items:start}.side{position:sticky;top:20px}.panel{background:var(--paper);border:1px solid var(--line);border-radius:13px;box-shadow:0 2px 8px #243b5310;padding:22px;margin-bottom:18px}.side .panel{padding:16px}.player{width:100%;max-height:290px;border-radius:9px;background:#111827;display:block}.player-title{font-weight:700;margin:12px 0 3px}.small{font-size:12px}.toc{display:flex;flex-direction:column;gap:5px}.toc a{display:block;padding:8px 10px;border-radius:7px;color:#42516a;font-size:13px}.toc a.active{background:#ecf1ff;color:#264ad0;font-weight:bold}.row{display:flex;justify-content:space-between;align-items:center;gap:12px}.tools{display:flex;flex-wrap:wrap;gap:7px}.legend{display:flex;flex-wrap:wrap;gap:8px;margin:17px 0 9px}.legend button{border-color:var(--line);font-size:12px;display:flex;align-items:center;gap:6px;padding:6px 9px}.dot{width:9px;height:9px;display:inline-block;border-radius:2px}.legend .chosen{outline:2px solid var(--tag);outline-offset:1px}.formula{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:17px 0 2px}.formula-step{padding:5px 9px;border-radius:6px;border:1px solid currentColor;background:#ffffff;cursor:pointer;font-weight:600;font-size:14px}.plus{color:#93a0b3}.reuse{background:#fafbff}.formula-description{font-size:13px;color:#596b83;margin-top:13px}.section{scroll-margin-top:20px;border-top:1px solid var(--line);padding-top:24px;margin-top:25px}.section:first-child{border-top:0;margin-top:12px;padding-top:8px}.section-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;margin-bottom:13px}.section-meta{font-size:12px;color:#6b7890}.section-function{margin-top:8px;padding:8px 12px;border-radius:6px;background:#f5f7fc;font-size:13px;color:#43516b}.unit{border-radius:7px;padding:12px 13px;margin:5px -5px;position:relative}.unit:hover{background:#f7f9fd}.unit.current{background:#edf3ff;box-shadow:inset 3px 0 #5078f1}.source-line{font-size:17px;line-height:1.95;margin:0;white-space:pre-wrap;word-break:break-word}.phrase{border-radius:2px;padding:1px 0;transition:opacity .15s}.phrase:hover{background:#e8edf588;text-decoration:underline;text-underline-offset:4px}.phrase.dim{opacity:.23}.unit-meta{font-size:11px;color:#8894a7;margin-top:4px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}.unit-meta button{font-size:11px;padding:1px 7px;background:transparent}.reason{margin:9px 0 1px;font-size:12px;color:#59687c;border-left:2px solid #d8e1f0;padding-left:12px}.reason li{margin-bottom:4px}details>summary{cursor:pointer;color:#667c99;font-size:12px}.plain .phrase{color:var(--ink)!important;opacity:1!important;background-color:transparent!important;font-weight:inherit!important;text-decoration:none!important}.plain .analysis-only{display:none}.empty-video{padding:22px;background:#f1f4f9;border-radius:9px;text-align:center;color:#6b7890}.foot{font-size:12px;color:#7d899c;padding:8px 0}.toast{position:fixed;bottom:25px;left:50%;transform:translateX(-50%);background:#1d2b42;color:white;border-radius:8px;padding:10px 19px;box-shadow:0 5px 20px #0003;z-index:10;display:none}.links{display:flex;flex-wrap:wrap;gap:14px}.copy-main{background:var(--accent);border-color:var(--accent);color:white}.copy-main:hover{background:#264bc6;color:white}.empty{display:none}#active-filter{min-height:18px;font-size:12px;color:#5b6b83}.source-note{font-size:13px;max-width:1060px}.metrics-note{font-size:12px;color:#7f8aa0;margin:12px 0 0}@media(max-width:1000px){.app{padding:22px 18px}.layout{grid-template-columns:245px minmax(0,1fr)}.panel{padding:17px}.source-line{font-size:16px}}@media(max-width:740px){header{display:block}h1{font-size:23px}.layout{display:block}.side{position:static}.side .panel:has(.toc){display:none}.player{max-height:240px}.row{flex-wrap:wrap}.tools{margin-top:8px}.panel{padding:17px}.app{padding:17px 12px}.section-head{flex-wrap:wrap}.source-line{font-size:16px}}
'''

STYLE += r'''
[hidden]{display:none!important}.compact .unit{display:inline;padding:0;margin:0}.compact .unit:after{content:"　"}.compact .source-line{display:inline;font-size:17px;line-height:2.3}.compact .unit-meta,.compact .unit details{display:none}.compact .unit.current{box-shadow:none;text-decoration:underline;text-underline-offset:5px}.compact .section{padding-bottom:8px}.compact .unit:hover{background:transparent}.compact .phrase:hover{background:#eaf0fb}
'''

SCRIPT = r'''
const D=JSON.parse(document.getElementById('data').textContent);
const tax=Object.fromEntries(D.taxonomy.map(x=>[x.family_id,x]));
const sections=Object.fromEntries(D.sections.map(x=>[x.id,x]));
let activeFamily=null, selectedSection=null;
function el(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e}
function message(text){const e=document.getElementById('toast');e.textContent=text;e.style.display='block';clearTimeout(window.toastTimer);window.toastTimer=setTimeout(()=>e.style.display='none',2200)}
async function copy(text){try{if(navigator.clipboard)await navigator.clipboard.writeText(text);else throw 0;message('已复制')}catch{const t=el('textarea');t.value=text;t.style.position='fixed';t.style.opacity=0;document.body.append(t);t.select();const ok=document.execCommand('copy');t.remove();message(ok?'已复制':'复制未完成，请手动选择文字')}}
function time(ms){let total=Math.floor(ms/1000);return String(Math.floor(total/60)).padStart(2,'0')+':'+String(total%60).padStart(2,'0')}
function sectionLabel(s){return s.display_id||s.id}
function applyFamilyStyle(node,family){const entry=tax[family];node.style.color=entry.color;if(entry.style){node.style.fontWeight=entry.style.bold?'700':'400';node.style.textDecoration=entry.style.underline?'underline':'none';const bg=entry.style.background_color;if(bg){const rgb=[1,3,5].map(i=>parseInt(bg.slice(i,i+2),16));node.style.backgroundColor='rgba('+rgb.join(',')+','+entry.style.background_opacity+')'}else node.style.backgroundColor='transparent'}return node}
function phrase(run){const node=el('span','phrase',run.text);applyFamilyStyle(node,run.category_family);node.dataset.family=run.category_family;node.title=tax[run.category_family].label+' · '+(run.function_tag||'')+'\n'+(run.reason||'');return node}
function appendRuns(node,runs){runs.forEach(run=>node.append(phrase(run)))}
function visibleSourceText(units){return units.map(u=>u.display_runs.map(r=>r.text).join('')).filter(text=>text.length>0).join('\n')}
function pickFamily(id){activeFamily=activeFamily===id?null:id;document.querySelectorAll('.phrase').forEach(x=>x.classList.toggle('dim',!!activeFamily&&x.dataset.family!==activeFamily));document.querySelectorAll('.legend button').forEach(x=>x.classList.toggle('chosen',x.dataset.family===activeFamily));document.getElementById('active-filter').textContent=activeFamily?'正在突出：'+tax[activeFamily].label+'；再次点击恢复全文':'点击图例，可突出对应功能；颜色不表示内容已被证实。'}
function selectVideo(sectionId,unit){const s=sections[sectionId];selectedSection=sectionId;document.querySelectorAll('.toc a').forEach(x=>x.classList.toggle('active',x.dataset.section===sectionId));document.getElementById('player-title').textContent=s.name;document.getElementById('player-range').textContent=s.time_range||'';const v=document.getElementById('video');if(s.clip_url){document.getElementById('video-empty').hidden=true;v.hidden=false;const changed=v.dataset.section!==sectionId;if(changed){v.src=s.clip_url;v.dataset.section=sectionId;v.load()}const seek=()=>{if(unit&&Number.isFinite(unit.locate_ms)){v.currentTime=Math.max(0,(unit.locate_ms-(s.start_ms||0))/1000)}};if(changed)v.addEventListener('loadedmetadata',seek,{once:true});else seek()}else{v.hidden=true;document.getElementById('video-empty').hidden=false}document.querySelectorAll('.unit').forEach(x=>x.classList.toggle('current',!!unit&&x.dataset.id===unit.id))}
function formula(container,steps){const node=document.getElementById(container);steps.forEach((s,i)=>{if(i)node.append(el('span','plus','＋'));const b=el('button','formula-step');if(s.phrase_runs)appendRuns(b,s.phrase_runs);else{b.textContent=s.label;applyFamilyStyle(b,s.category_family)}b.title=s.note||'脚本结构分析';b.onclick=()=>{if(s.section_id&&sections[s.section_id]){document.getElementById('sec-'+s.section_id).scrollIntoView({behavior:'smooth'});selectVideo(s.section_id)}else if(s.category_family)pickFamily(s.category_family)};node.append(b)})}
document.getElementById('title').textContent=D.title;document.title=D.title;
document.getElementById('subtitle').textContent=D.subtitle||'';
document.getElementById('source-note').textContent=D.disclosure||'';
document.getElementById('text-kind').textContent=D.text_kind==='subtitle_excerpts'?'源字幕摘录 · 非完整口播':'用户提供文案示例';
document.getElementById('count').textContent=D.units.length+' 条原文 · '+D.sections.length+' 个内容段';
document.getElementById('script-heading').textContent=D.text_kind==='subtitle_excerpts'?'本视频字幕摘录':'用户提供原文';
document.getElementById('copy-script').textContent=D.text_kind==='subtitle_excerpts'?'复制字幕摘录':'复制原文';document.getElementById('copy-formula').textContent=D.text_kind==='subtitle_excerpts'?'复制原片公式':'复制本篇公式';
document.getElementById('copy-script').onclick=()=>copy(visibleSourceText(D.units));
document.getElementById('copy-formula').onclick=()=>copy(D.source_formula.map(x=>x.label).join(' + '));
document.getElementById('copy-reuse').onclick=()=>copy(D.reusable_formula.map(x=>x.label).join(' + '));
document.getElementById('toggle-mode').onclick=e=>{const plain=document.getElementById('script-body').classList.toggle('plain');e.target.textContent=plain?'显示功能分析':'只看原文';e.target.setAttribute('aria-pressed',String(plain))};
const detailToggle=el('button',null,'逐条查看依据');detailToggle.id='toggle-detail';detailToggle.setAttribute('aria-pressed','false');document.querySelector('.tools').prepend(detailToggle);document.getElementById('script-body').classList.add('compact');detailToggle.onclick=()=>{const compact=document.getElementById('script-body').classList.toggle('compact');detailToggle.textContent=compact?'逐条查看依据':'连续阅读';detailToggle.setAttribute('aria-pressed',String(!compact))};
D.taxonomy.forEach(t=>{const b=el('button');b.dataset.family=t.family_id;b.style.setProperty('--tag',t.color);const dot=el('i','dot');dot.style.background=t.style?.background_color||t.color;const label=el('span',null,t.label);applyFamilyStyle(label,t.family_id);b.append(dot,label);b.onclick=()=>pickFamily(t.family_id);document.getElementById('legend').append(b)});pickFamily(null);
formula('source-formula',D.source_formula);formula('reuse-formula',D.reusable_formula);
document.getElementById('formula-note').textContent=D.source_formula_note||'按原文顺序归纳；同一功能在后文再次出现时保留重复。';
document.getElementById('reuse-note').textContent=D.reuse_note||'用于改编的变量公式，需填入目标产品真实资料。';

D.sections.forEach(s=>{const a=el('a',null,sectionLabel(s)+' · '+s.name);a.href='#sec-'+s.id;a.dataset.section=s.id;a.onclick=()=>selectVideo(s.id);document.getElementById('toc').append(a);const section=el('section','section');section.id='sec-'+s.id;const h=el('div','section-head');const title=el('div');title.append(el('h3',null,sectionLabel(s)+'  '+s.name),el('div','section-meta',s.time_range?s.time_range+' · 段级定位':''));h.append(title);if(s.clip_url){const b=el('button',null,'查看该视频段');b.onclick=()=>selectVideo(s.id);h.append(b)}section.append(h);if(s.formula){const f=el('div','section-function analysis-only');f.append(document.createTextNode('本段公式：'));if(s.formula_runs)appendRuns(f,s.formula_runs);else f.append(document.createTextNode(s.formula));section.append(f)}if(s.emotion_labels?.length){const e=el('details','analysis-only');e.style.margin='10px 0 16px';e.append(el('summary',null,'情绪表达（段级）：'+s.emotion_labels.join(' → ')));const ul=el('ul','reason');for(const x of s.emotion_evidence||[])ul.append(el('li',null,x.stage+'｜'+x.observable+'（'+x.evidence_shot_ids.join('、')+'）'));e.append(ul,el('p','small muted',s.emotion_hypothesis||''));section.append(e)}D.units.filter(u=>u.section_id===s.id).forEach(u=>{const row=el('article','unit');row.dataset.id=u.id;const line=el('p','source-line');appendRuns(line,u.display_runs||u.phrase_runs);row.append(line);if(u.reading_note)row.append(el('p','small muted analysis-only','字幕辨读备注：'+u.reading_note));const meta=el('div','unit-meta');if(u.source_shot_ids?.length)meta.append(el('span',null,u.source_shot_ids.join('、')));meta.append(el('span',null,u.locator_note||'原文顺序保留'));if(s.clip_url&&Number.isFinite(u.locate_ms)){const b=el('button',null,'定位画面 '+time(u.locate_ms));b.onclick=()=>selectVideo(s.id,u);meta.append(b)}row.append(meta);const detail=el('details','analysis-only');detail.append(el('summary',null,'展开功能依据'));const reasons=el('ul','reason');u.phrase_runs.forEach(r=>reasons.append(el('li',null,'「'+r.text+'」｜'+r.function_tag+'：'+r.reason)));detail.append(reasons);row.append(detail);section.append(row)});document.getElementById('script-body').append(section)});
if(D.sections.length)selectVideo(D.sections[0].id);
for(const link of D.links||[]){const a=el('a',null,link.label);a.href=link.url;a.target='_blank';a.rel='noreferrer';document.getElementById('links').append(a)}
window.__scriptAnalysis={data:D,selectVideo,pickFamily,copy,applyFamilyStyle,phrase,formula,visibleSourceText};
'''


def render(d):
    validate(d)
    presentation = _default_presentation_copy(d)
    presentation['annotation_validation'] = validate_annotations(d)
    for unit in presentation['units']:
        unit['display_runs'] = reading_runs(unit, strict=is_v9(d))
    data = json.dumps(presentation, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    return '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>脚本分析</title><style>'''+STYLE+'''</style></head><body><main class="app"><header><div><div class="eyebrow">SCRIPT ANALYSIS / 脚本功能拆解</div><h1 id="title"></h1><div id="subtitle" class="muted"></div></div><span class="badge" id="text-kind"></span></header><div class="note source-note" id="source-note"></div><div class="layout"><aside class="side"><div class="panel"><video id="video" class="player" controls preload="metadata"></video><div id="video-empty" class="empty-video" hidden>文案示例 · 未提供对应视频</div><div id="player-title" class="player-title"></div><div id="player-range" class="muted small"></div><p class="small muted">文字旁的定位基于画面与脚本段范围，不代表已校准逐句原音时间。</p></div><div class="panel"><h3>内容段导航</h3><nav id="toc" class="toc"></nav></div><div class="foot links" id="links"></div></aside><div><section class="panel"><div class="row"><h2>本篇脚本公式</h2><button id="copy-formula">复制原片公式</button></div><div id="legend" class="legend"></div><div id="active-filter"></div><div id="source-formula" class="formula"></div><div id="formula-note" class="formula-description"></div></section><section class="panel reuse"><div class="row"><h2>可复用变量公式</h2><button id="copy-reuse">复制复用公式</button></div><div id="reuse-formula" class="formula"></div><div id="reuse-note" class="formula-description"></div></section><section class="panel"><div class="row"><div><h2 id="script-heading"></h2><div class="muted small" id="count"></div></div><div class="tools"><button id="toggle-mode" aria-pressed="false">只看原文</button><button id="copy-script" class="copy-main">复制原文</button></div></div><div id="script-body"></div></section><p class="metrics-note">功能标注和公式属于内容分析；未接入该视频的真实互动时序或转化数据。</p></div></div></main><div class="toast" id="toast" role="status" aria-live="polite"></div><script id="data" type="application/json">'''+data+'''</script><script>'''+SCRIPT+'''</script></body></html>'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    args = p.parse_args()
    d = json.loads(args.input.read_text(encoding='utf-8-sig'))
    document = render(d)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(document, encoding='utf-8')
    print(json.dumps({'status': 'rendered', 'units': len(d['units']), 'sections': len(d['sections']),
                      'annotation_validation': validate_annotations(d), 'output': str(args.output)}, ensure_ascii=False))

if __name__ == '__main__':
    main()
