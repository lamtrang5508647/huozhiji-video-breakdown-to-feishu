"""Extract true-PTS start/middle/end frames for final intervals; no visual judgments.

Requires locally installed PyAV. Decodes source once. Never downloads dependencies
or changes the source. Adjacent cut frames are indexed separately from subtitles.
"""
import argparse,hashlib,json,pathlib,sys

def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for chunk in iter(lambda:f.read(1048576),b''):h.update(chunk)
 return h.hexdigest()

def extract(timeline_path,out):
 import av
 timeline_path=pathlib.Path(timeline_path).resolve();out=pathlib.Path(out).resolve()
 t=json.loads(timeline_path.read_text(encoding='utf-8-sig'));source=pathlib.Path(t['source']['path']).resolve()
 assert source.is_file() and sha(source)==t['source']['sha256'],'Source hash mismatch'
 shots=t['segments'];assert shots and len({s['id'] for s in shots})==len(shots)
 assert not out.exists() or not any(out.iterdir()),'Use an empty evidence directory; do not overwrite reviewed images'
 out.mkdir(parents=True,exist_ok=True)
 targets=[]
 for s in shots:
  assert 0<=s['start_ms']<s['end_ms']
  targets.extend([(s['start_ms'],'after',s['id'],'start'),((s['start_ms']+s['end_ms'])/2,'nearest',s['id'],'middle'),(s['end_ms'],'before',s['id'],'end')])
 for s in shots[1:]:targets.extend([(s['start_ms'],'before',s['id'],'cut_before'),(s['start_ms'],'after',s['id'],'cut_after')])
 targets.sort(key=lambda q:(q[0],q[1],q[2],q[3]));cursor=0;previous=None;written={};result={s['id']:{} for s in shots}
 def emit(request,frame):
  ms,mode,sid,role=request
  assert frame is not None,(sid,role,'No source frame')
  actual=float(frame.pts*frame.time_base)*1000
  key=(frame.pts,str(frame.time_base))
  if key not in written:
   p=out/f'frame-{len(written)+1:05d}-pts-{frame.pts}.jpg';frame.to_image().save(p,quality=95)
   written[key]={'path':p.name,'sha256':sha(p),'actual_pts_ms':actual,'pts':frame.pts,'time_base':str(frame.time_base)}
  result[sid][role]={**written[key],'requested_ms':ms,'selection':mode}
 with av.open(str(source)) as container:
  stream=container.streams.video[0]
  for frame in container.decode(stream):
   if frame.pts is None:continue
   ms=float(frame.pts*frame.time_base)*1000
   # Timeline cut times are integer milliseconds. Accept the native frame within
   # half a millisecond of that rounded value (66.666... ms is labelled 67 ms),
   # otherwise a rounded-up boundary would incorrectly select the next frame.
   while cursor<len(targets) and targets[cursor][0]<=ms+0.500001:
    request=targets[cursor];target,mode=request[:2]
    chosen=frame
    if mode=='before':chosen=previous
    elif mode=='nearest' and previous is not None:
     prev_ms=float(previous.pts*previous.time_base)*1000
     if abs(prev_ms-target)<abs(ms-target):chosen=previous
    emit(request,chosen);cursor+=1
   previous=frame
  while cursor<len(targets):
   request=targets[cursor];assert request[1] in {'before','nearest'},('Source video ends before requested frame',request)
   emit(request,previous);cursor+=1
 for s in shots:
  for role in ['start','middle','end']:
   actual=result[s['id']][role]['actual_pts_ms'];assert s['start_ms']-1.1<=actual<s['end_ms']+1.1,('Chosen frame outside final interval',s['id'],role,actual)
 manifest={'schema_version':1,'video_id':t['video_id'],'source_sha256':t['source']['sha256'],'timeline_sha256':sha(timeline_path),'source_path':str(source),'source_pts_origin':'native decoded presentation timestamp; not an approximate seek request','integer_millisecond_quantization_tolerance_ms':0.500001,'shots':result,'images':len(written),'visual_review_performed':False}
 (out/'interval-evidence.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 return {'status':'extracted','shots':len(shots),'images':len(written),'manifest':str(out/'interval-evidence.json'),'visual_review_performed':False}

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--timeline',required=True,type=pathlib.Path);p.add_argument('--output-dir',required=True,type=pathlib.Path);a=p.parse_args();print(json.dumps(extract(a.timeline,a.output_dir),ensure_ascii=False))
if __name__=='__main__':main()
