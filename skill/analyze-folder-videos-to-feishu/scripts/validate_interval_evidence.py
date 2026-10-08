"""Check final-interval frame provenance/timing; not a substitute for viewing images."""
import argparse,hashlib,json,pathlib,sys
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def validate(timeline_path,evidence_path):
 timeline_path=pathlib.Path(timeline_path);evidence_path=pathlib.Path(evidence_path);t=json.loads(timeline_path.read_text(encoding='utf-8-sig'));e=json.loads(evidence_path.read_text(encoding='utf-8-sig'));errors=[]
 def check(ok,msg):
  if not ok:errors.append(msg)
 check(e.get('video_id')==t['video_id'],'Video identity mismatch');check(e.get('source_sha256')==t['source']['sha256'],'Source hash mismatch');check(e.get('timeline_sha256')==sha(timeline_path),'Stale interval evidence')
 check(set(e.get('shots',{}))=={s['id'] for s in t['segments']},'Evidence must cover exactly the final shot IDs')
 for i,s in enumerate(t['segments']):
  frames=e.get('shots',{}).get(s['id'],{});roles=['start','middle','end']+(['cut_before','cut_after'] if i else [])
  for role in roles:
   f=frames.get(role);check(isinstance(f,dict),s['id']+': missing '+role)
   if not isinstance(f,dict):continue
   path=pathlib.Path(f.get('path',''));path=path if path.is_absolute() else evidence_path.parent/path
   valid=path.is_file() and path.stat().st_size>100;check(valid,s['id']+': missing frame file '+role)
   if valid:check(sha(path)==f.get('sha256'),s['id']+': changed evidence frame '+role)
   actual=f.get('actual_pts_ms');check(isinstance(actual,(int,float)),s['id']+': missing measured PTS')
   if not isinstance(actual,(int,float)):continue
   if role in ['start','middle','end']:check(s['start_ms']-1.1<=actual<s['end_ms']+1.1,s['id']+': evidence outside final interval '+role)
   elif role=='cut_before':check(actual<s['start_ms']+1.1,s['id']+': before frame after cut')
   elif role=='cut_after':check(actual>=s['start_ms']-1.1,s['id']+': after frame before cut')
   try:
    num,den=map(int,str(f['time_base']).split('/'));measured=f['pts']*num/den*1000;check(abs(measured-actual)<.01,s['id']+': PTS value disagrees with measured time')
   except (KeyError,ValueError,ZeroDivisionError,TypeError):check(False,s['id']+': invalid PTS/time base')
  for span in s.get('subtitle_spans',[]):
   check(s['start_ms']<=span['start_ms']<span['end_ms']<=s['end_ms'],s['id']+': subtitle span crosses final interval')
 return {'status':'FAIL' if errors else 'PASS','video_id':t['video_id'],'shots':len(t['segments']),'errors':errors,'scope':'Frame identity and final-interval timing only; image content and source claims require visual review','content_review_required':True}
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--timeline',required=True,type=pathlib.Path);p.add_argument('--evidence-index',required=True,type=pathlib.Path);p.add_argument('--output',type=pathlib.Path);a=p.parse_args();r=validate(a.timeline,a.evidence_index)
 if a.output:a.output.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print(json.dumps(r,ensure_ascii=False));return 0 if r['status']=='PASS' else 1
if __name__=='__main__':sys.exit(main())
