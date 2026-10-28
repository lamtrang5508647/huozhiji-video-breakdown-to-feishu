#!/usr/bin/env python3
"""Report candidate semantic cut risks from unverified ASR; never change cuts."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def report(source, timeline_path, asr_path):
    source = source.resolve(strict=True)
    timeline_path = timeline_path.resolve(strict=True)
    asr_path = asr_path.resolve(strict=True)
    if not source.is_file(): raise ValueError('Source must be a file')
    tbytes, abytes = timeline_path.read_bytes(), asr_path.read_bytes()
    timeline = json.loads(tbytes.decode('utf-8-sig'))
    asr = json.loads(abytes.decode('utf-8-sig'))
    if not isinstance(timeline, dict) or not isinstance(asr, dict): raise ValueError('Inputs must be JSON objects')
    video_id = timeline.get('video_id')
    if not isinstance(video_id, str) or not video_id or asr.get('video_id') != video_id:
        raise ValueError('Video ID mismatch')
    source_sha = digest(source)
    if not isinstance(timeline.get('source'), dict) or timeline['source'].get('sha256') != source_sha or asr.get('source_sha256') != source_sha:
        raise ValueError('Source hash mismatch')
    duration = timeline.get('duration_sec')
    asr_duration = asr.get('source_duration_sec')
    if not number(duration) or duration <= 0 or not number(asr_duration) or abs(duration-asr_duration)>0.05:
        raise ValueError('Timeline and ASR declared duration mismatch')
    groups = timeline.get('semantic_groups')
    if not isinstance(groups, list) or not groups: raise ValueError('semantic_groups required')
    cursor, ids = 0, set()
    for item in groups:
        if not isinstance(item,dict) or not isinstance(item.get('id'),str) or not item['id'] or item['id'] in ids:
            raise ValueError('Invalid or duplicate semantic group ID')
        a,b=item.get('start_ms'),item.get('end_ms')
        if type(a) is not int or type(b) is not int or a != cursor or b <= a:
            raise ValueError('Semantic groups must be contiguous integer milliseconds')
        ids.add(item['id']); cursor=b
    if abs(cursor / 1000 - duration)>0.05: raise ValueError('Group coverage differs from declared duration')
    segments = asr.get('segments')
    if not isinstance(segments, list): raise ValueError('ASR segments must be a list')
    valid_segments, valid_words, warnings = [], [], []
    def valid_range(a,b): return number(a) and number(b) and 0 <= a < b <= duration + 0.05
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict) or not valid_range(seg.get('start_sec'), seg.get('end_sec')):
            warnings.append({'path':f'segments[{i}]','reason':'invalid or out-of-range segment time'}); continue
        start,end=seg['start_sec'],seg['end_sec']
        if not isinstance(seg.get('text'),str):
            warnings.append({'path':f'segments[{i}].text','reason':'text is not a string'}); continue
        valid_segments.append({'start_sec':start,'end_sec':end,'text':seg['text']})
        words=seg.get('words',[])
        if not isinstance(words,list):
            warnings.append({'path':f'segments[{i}].words','reason':'words is not a list'});continue
        if not words:
            warnings.append({'path':f'segments[{i}].words','reason':'no word timing available for segment'})
        for k,w in enumerate(words):
            label=f'segments[{i}].words[{k}]'
            if not isinstance(w,dict) or not valid_range(w.get('start_sec'),w.get('end_sec')) or not isinstance(w.get('text'),str):
                warnings.append({'path':label,'reason':'invalid or out-of-range word'});continue
            if w['start_sec'] < start - 0.05 or w['end_sec'] > end + 0.05:
                warnings.append({'path':label,'reason':'word time outside parent segment'});continue
            valid_words.append({'start_sec':w['start_sec'],'end_sec':w['end_sec'],'text':w['text']})
    if not segments: warnings.append({'path':'segments','reason':'empty transcript cannot assess boundaries'})
    boundaries=[]
    for prev,nxt in zip(groups,groups[1:]):
        at=prev['end_ms']/1000
        words=[w for w in valid_words if w['start_sec']<at<w['end_sec']]
        sentences=[s for s in valid_segments if s['start_sec']<at<s['end_sec']]
        if words: category='machine_word_overlap'
        elif sentences: category='machine_segment_overlap'
        elif warnings: category='not_determinable_invalid_asr_times'
        else: category='no_machine_overlap_detected'
        boundaries.append({'before_group':prev['id'],'after_group':nxt['id'],'boundary_ms':prev['end_ms'],
                           'risk':category,'crossing_words':words,'crossing_segments':sentences,
                           'review_status':'requires_listening',
                           'note':'Machine timing is unverified; absence of overlap does not establish a safe cut.'})
    if digest(source)!=source_sha or hashlib.sha256(tbytes).hexdigest()!=digest(timeline_path) or hashlib.sha256(abytes).hexdigest()!=digest(asr_path):
        raise ValueError('Input changed during analysis')
    return {'schema':'semantic-boundary-risk/v1','video_id':video_id,'source_sha256':source_sha,
            'timeline_sha256':hashlib.sha256(tbytes).hexdigest(),'asr_sha256':hashlib.sha256(abytes).hexdigest(),
            'asr_status':asr.get('status','not_provided'),'timing_warnings':warnings,'boundaries':boundaries,
            'scope':'Machine timing hints only. Actual speech, semantic completeness, source duration, audio playback and listening are not verified; boundaries are not changed.'}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('source','timeline','asr','output'): p.add_argument('--'+n, type=Path, required=True)
    args=p.parse_args(argv)
    try:
        if args.output.exists(): raise ValueError('Output exists; choose a new path')
        result=report(args.source,args.timeline,args.asr)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        with args.output.open('x',encoding='utf8',newline='\n') as f:json.dump(result,f,ensure_ascii=False,indent=2);f.write('\n')
        print(json.dumps({'status':'risk_report_not_listened','boundaries':len(result['boundaries']),
                          'timing_warnings':len(result['timing_warnings'])},ensure_ascii=False));return 0
    except (OSError,ValueError,TypeError,KeyError) as exc:
        print(json.dumps({'status':'BLOCKED','error':str(exc)},ensure_ascii=False));return 1
if __name__=='__main__':raise SystemExit(main())
