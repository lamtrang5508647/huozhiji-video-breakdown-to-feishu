"""Read-only traceability gate. Does not authenticate visual or semantic judgments."""
import argparse,hashlib,json,pathlib,sys

def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def gate(timeline_path,audit_path,tag_map_path=None,schema_path=None):
 timeline_path=pathlib.Path(timeline_path).resolve();audit_path=pathlib.Path(audit_path).resolve()
 t=read(timeline_path);a=read(audit_path);errors=[]
 def require(ok,msg):
  if not ok:errors.append(msg)
 def path(v):
  p=pathlib.Path(v);return p if p.is_absolute() else audit_path.parent/p
 require(a.get('schema_version')==1,'Unsupported ledger version')
 require(a.get('video_id')==t['video_id'],'Video identity mismatch')
 require(a.get('source_sha256')==t['source']['sha256'],'Source identity mismatch')
 require(a.get('timeline_sha256')==sha(timeline_path),'Reviewed timeline hash mismatch')
 cp=path(a['candidates_path']);c=read(cp);require(sha(cp)==a.get('candidate_sha256'),'Candidate hash mismatch')
 require(c.get('source_sha256')==t['source']['sha256'],'Candidate source mismatch')
 clusters=c.get('clusters') or c.get('scene_candidates',{}).get('clusters')
 require(isinstance(clusters,list),'Missing candidate clusters')
 clusters=clusters or [];decisions=a.get('decisions',[])
 require([d.get('index') for d in decisions]==list(range(1,len(clusters)+1)),'Ledger must cover every candidate once in order')
 shots=t['segments'];cuts=[s['start_ms'] for s in shots[1:]];fps=float(a.get('source_fps',30));tol=max(2,1000/fps+2);accounted=set();split_count=0
 def evidence(d,label):
  require(isinstance(d.get('reason'),str) and len(d['reason'].strip())>=4,label+': missing concrete reason')
  for k in ('before','after'):
   v=d.get(k);require(isinstance(v,str) and path(v).is_file() and path(v).stat().st_size>100,label+': missing '+k+' frame')
 def cut(ms,label):
  nonlocal split_count
  split_count+=1;matches=[x for x in cuts if isinstance(ms,(int,float)) and abs(x-ms)<=tol]
  require(bool(matches),label+': real visual cut merged inside a numbered shot')
  accounted.update(matches)
 for d,candidate in zip(decisions,clusters):
  label='candidate '+str(d['index']);evidence(d,label)
  require(abs(float(d.get('candidate_sec',-100))-float(candidate['candidate_sec']))<.001,label+': candidate time changed')
  decision=d.get('decision');require(decision in {'cut','continuous'},label+': unresolved/unknown decision')
  if decision=='cut':cut(d.get('reviewed_cut_ms'),label)
  elif decision=='continuous':require(d.get('continuity')=='same_source_scene',label+': merge lacks continuity basis')
 for i,d in enumerate(a.get('added_cuts',[])):
  label='added cut '+str(i+1);evidence(d,label);cut(d.get('time_ms'),label)
 require(set(cuts)<=accounted,'Timeline has cut boundaries without review evidence')
 if tag_map_path or schema_path:
  require(bool(tag_map_path and schema_path),'Tag map and live schema must be supplied together')
  if tag_map_path and schema_path:
   mapping=read(pathlib.Path(tag_map_path));schema=read(pathlib.Path(schema_path));fields=schema.get('data',schema).get('fields',[]);tag_fields=[f for f in fields if f.get('name')=='画面标签'];require(len(tag_fields)==1,'Live shot tag field missing/ambiguous')
   if len(tag_fields)==1:
    allowed={o['name'] for o in tag_fields[0]['options']};require(set(mapping)=={s['id'] for s in shots},'Tag map does not cover exact shot IDs')
    for sid,tags in mapping.items():require(isinstance(tags,list) and bool(tags) and all(v in allowed for v in tags),sid+': unknown live select tag')
 return {'status':'FAIL' if errors else 'PASS','publication_ready':False,'content_review_required':True,'video_id':t['video_id'],'reviewed_candidates':len(decisions),'numbered_shots':len(shots),'cut_decisions':split_count,'errors':errors,'scope':'Structure, source identity and evidence file traceability only; native visual judgment, full audio correctness and marketing effectiveness are not certified','mutations':0}

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--timeline',required=True,type=pathlib.Path);p.add_argument('--audit',required=True,type=pathlib.Path);p.add_argument('--tag-map',type=pathlib.Path);p.add_argument('--schema',type=pathlib.Path);p.add_argument('--output',type=pathlib.Path);a=p.parse_args()
 try:r=gate(a.timeline,a.audit,a.tag_map,a.schema)
 except (KeyError,TypeError,ValueError,OSError) as e:r={'status':'FAIL','errors':[str(e)],'mutations':0}
 if a.output:a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print(json.dumps(r,ensure_ascii=False,indent=2));return 0 if r['status']=='PASS' else 2
if __name__=='__main__':sys.exit(main())
