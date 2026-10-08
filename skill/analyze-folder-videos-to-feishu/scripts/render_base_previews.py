"""Render reviewed script analysis to static, source-exact Base preview images.

Requires sibling render_script_analysis.py, Node >= 22 and Chrome/Edge. All media
stays local until a separately invoked sync uploads the resulting manifest.
"""
import argparse
import hashlib
import html
import json
from pathlib import Path
import struct
import subprocess
from render_script_analysis import validate

VERSION = 'base-preview-v1'
PREFIX = 'scriptv4-preview--'

def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def digest(data):
    return hashlib.sha256(data).hexdigest()

def esc(value):
    return html.escape(str(value), quote=True)

def stable(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')

STYLE = '''
*{box-sizing:border-box}body{margin:0;background:#fff;color:#172033;font-family:"Microsoft YaHei","Noto Sans CJK SC",sans-serif;width:1120px}
main{padding:48px 52px 32px}header{border-bottom:3px solid #172033;padding-bottom:24px;margin-bottom:24px}
.eyebrow{font-size:17px;color:#64748b;letter-spacing:1px;margin-bottom:10px}h1{font-size:34px;line-height:1.45;margin:0 0 12px;font-weight:700}
.subtitle{font-size:18px;color:#475569;line-height:1.7;overflow-wrap:anywhere}.notice{font-size:17px;line-height:1.9;background:#f1f5f9;padding:14px 18px;border-radius:8px;margin-top:16px}
.legend{display:flex;flex-wrap:wrap;gap:12px 25px;margin:24px 0;font-size:18px}.swatch{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:8px}
section{margin-top:27px;break-inside:avoid}h2{font-size:23px;margin:0 0 14px;line-height:1.65}h3{font-size:21px;line-height:1.65;margin:0 0 10px}
.formula{font-size:22px;line-height:2.05;font-weight:600;overflow-wrap:anywhere}.plus{color:#94a3b8;padding:0 10px}.note{font-size:16px;line-height:1.85;color:#64748b;margin-top:10px}
.box{border:1px solid #dbe3eb;background:#f8fafc;padding:20px 24px;border-radius:10px}.script-section{border-top:1px solid #dbe3eb;padding-top:22px}
.time{font-size:16px;color:#64748b;font-weight:400;margin-left:12px}.original{font-size:24px;line-height:2.05;overflow-wrap:anywhere;text-align:left}
.unit{margin-right:.22em}.tag{font-size:17px;background:#eef2f6;display:inline-block;padding:4px 10px;border-radius:5px;margin:0 8px 8px 0}
.emotion{font-size:18px;line-height:1.9;margin:8px 0}.evidence{color:#64748b;font-size:15px}.foot{font-size:15px;color:#64748b;border-top:1px solid #dbe3eb;margin-top:30px;padding-top:18px;line-height:1.8}
'''

def formula(steps, colors):
    return '<span class="plus">＋</span>'.join(
        f'<span style="color:{colors[x["category_family"]]}">{esc(x["label"])}</span>' for x in steps)

def source_html(units, colors):
    result=[]
    for u in units:
        spans=''.join(f'<span style="color:{colors[p["category_family"]]}">{esc(p["text"])}</span>' for p in u['phrase_runs'])
        result.append(f'<span class="unit" data-unit="{esc(u["id"])}">{spans}</span>')
    return '<div class="original">'+''.join(result)+'</div>'

def render(data, section=None):
    colors={x['family_id']:x['color'] for x in data['taxonomy']}
    sections=[section] if section else data['sections']
    units=[u for u in data['units'] if not section or u['section_id']==section['id']]
    title=f'{section.get("display_id", "")} · {section["name"]}' if section else data['title']
    # This view has no interactive controls; copying remains in the Base text fields.
    parts=[f'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>{esc(title)}</title><style>{STYLE}</style><main>',
      '<header><div class="eyebrow">脚本框架 · 句内功能标色</div>',f'<h1>{esc(title)}</h1>',
      f'<div class="subtitle">{esc(data["subtitle"])}</div>',
      f'<div class="notice">{esc(data.get("preview_disclosure",data["disclosure"].replace("；可展开逐条依据","")))}<br>颜色不代表主张已核实。复制文字请使用同记录的「标色原文」和公式字段。</div></header>',
      '<div class="legend">'+''.join(f'<span><i class="swatch" style="background:{x["color"]}"></i>{esc(x["label"])}</span>' for x in data['taxonomy'])+'</div>']
    if section:
        parts.extend(['<section class="box"><h2>本段脚本公式</h2>',f'<div class="formula">{esc(section["formula"])}</div>',
                      '<div class="note">按原片表达顺序归纳；可借鉴结构，具体事实与产品主张须单独核验。</div></section>'])
    else:
        parts.extend(['<section class="box"><h2>原片脚本公式</h2><div class="formula">',formula(data['source_formula'], colors),
            f'</div><div class="note">{esc(data["source_formula_note"])}</div></section>',
            '<section class="box"><h2>可复用变量公式</h2><div class="formula">',formula(data['reusable_formula'], colors),
            f'</div><div class="note">{esc(data["reuse_note"])}</div></section>'])
    for s in sections:
        su=[u for u in units if u['section_id']==s['id']]
        parts.extend(['<section class="script-section">',f'<h2>{esc(s.get("display_id", ""))} · {esc(s["name"])}<span class="time">{esc(s["time_range"])}</span></h2>',source_html(su, colors),'</section>'])
    if section:
        parts.extend(['<section class="box"><h2>情绪表达与依据</h2>',
          ''.join(f'<span class="tag">{esc(t)}</span>' for t in section.get('emotion_labels',[]))])
        for e in section.get('emotion_evidence',[]):
            parts.append(f'<p class="emotion"><b>{esc(e["stage"])}</b>：{esc(e["observable"])}<br><span class="evidence">依据：{esc("、".join(e["evidence_shot_ids"]))}</span></p>')
        parts.append(f'<div class="note">{esc(section.get("emotion_hypothesis", ""))}</div></section>')
    parts.append('<div class="foot">静态彩色预览 · 原文、公式及视频保存在同一多维表格记录中。<br>内容段保留完整语义；单镜头索引继续用于定位和取素材。源片中的效果、比例和停用等说法不自动成为可复用事实。</div></main></html>')
    return ''.join(parts), units

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--bindings', type=Path, required=True, help='base_id + main + sections keyed by analysis section id')
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--browser', type=Path)
    a=p.parse_args()
    data=load(a.input); validate(data); bindings=load(a.bindings)
    out=a.output_dir.resolve(); out.mkdir(parents=True, exist_ok=True)
    if set(bindings['sections']) != {s['id'] for s in data['sections']}:
        raise ValueError('Binding section IDs must exactly match approved analysis sections')
    seen=set()
    for binding in [bindings['main']]+list(bindings['sections'].values()):
        key=(binding.get('table_id'),binding.get('record_id'))
        if any(not isinstance(x,str) or not x.strip() for x in key) or key in seen:
            raise ValueError('Bindings require non-empty unique table/record targets')
        seen.add(key)
    rows=[]; jobs=[]
    for i,s in enumerate([None]+data['sections']):
        key=s['id'] if s else 'main'
        binding=bindings['sections'][key] if s else bindings['main']
        content,units=render(data,s)
        html_path=out/f'preview-{i:02}.html'; png_path=out/f'preview-{i:02}.png'
        html_path.write_text(content,encoding='utf-8')
        fingerprint=digest(stable({'renderer':VERSION,'input_sha':digest(a.input.read_bytes()),'section':key,
          'html_sha':digest(content.encode('utf-8')),'base_id':bindings['base_id'],'target':binding,
          'provenance':bindings.get('provenance',{})}))
        text_updates={'标色原文':'\n'.join(u['source_text'] for u in units)}
        if s: expected_text_fields={'本段脚本公式':s['formula'].replace(' + ',' → ')}
        else:
            expected_text_fields={'原片脚本公式':' → '.join(x['label'] for x in data['source_formula'])}
            text_updates['可复用变量公式']=' + '.join(x['label'] for x in data['reusable_formula'])
        if 'expected_text_fields' in binding:
            supplied=binding['expected_text_fields']
            if not isinstance(supplied,dict) or set(supplied)!=set(expected_text_fields) or not all(isinstance(v,str) for v in supplied.values()):
                raise ValueError('Binding expected_text_fields must contain the exact existing source formula field')
            expected_text_fields=supplied
        rows.append({'table_id':binding['table_id'],'record_id':binding['record_id'],'section_id':key,
          'attachment_field':'彩色脚本预览','preview_path':str(png_path),'fingerprint':fingerprint,
          'fingerprint_field':'预览指纹','text_updates':text_updates,'expected_text_fields':expected_text_fields})
        jobs.append({'html_path':str(html_path),'png_path':str(png_path),'expected_units':[{'id':u['id'],'text':u['source_text']} for u in units]})
    capture=out/'capture-jobs.json'; capture.write_text(json.dumps(jobs,ensure_ascii=False,indent=2),encoding='utf-8')
    cmd=['node',str(Path(__file__).with_name('capture_base_previews.mjs')),'--jobs',str(capture),'--temp-root',str(out/'capture-temp')]
    if a.browser: cmd+=['--browser',str(a.browser.resolve())]
    subprocess.run(cmd,check=True)
    for row in rows:
        png=Path(row['preview_path']); raw=png.read_bytes()
        if not raw.startswith(b'\x89PNG\r\n\x1a\n') or len(raw)>20*1024*1024:
            raise ValueError(f'Invalid or oversized PNG: {png}')
        width,height=struct.unpack('>II',raw[16:24]); sha=digest(raw)
        target=out/f'{PREFIX}{sha}.png'
        if target.exists() and target.read_bytes()!=raw: raise ValueError('Unexpected digest collision')
        if not target.exists(): target.write_bytes(raw)
        row.update(preview_path=str(target),preview_filename=target.name,sha256=sha,bytes=len(raw),width=width,height=height)
    result={'schema':'base-script-preview-sync-v1','base_id':bindings['base_id'],'renderer_version':VERSION,
      'source_analysis_sha256':digest(a.input.read_bytes()),'source_analysis_path':str(a.input.resolve()),
      'provenance':bindings.get('provenance',{}),'records':rows}
    target=out/'sync-manifest.json'; target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':'PASS','records':len(rows),'manifest':str(target),'total_png_bytes':sum(r['bytes'] for r in rows)},ensure_ascii=False))

if __name__=='__main__': main()
