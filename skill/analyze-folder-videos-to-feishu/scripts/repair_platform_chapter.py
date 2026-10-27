"""Reformat a reviewed chapter while retaining every existing H1/segment anchor.

Exact snapshot guards protect other chapters and human edits. The pending journal
fails closed after an interrupted write; inspect live state before reconciliation.
"""
import argparse,copy,datetime,hashlib,json,pathlib,subprocess,sys,xml.etree.ElementTree as ET
from sync_video_base import Lark,atomic_json
from check_docx_registry import check,canonical
from render_platform_chapter import effective_signature, LAYOUT_PROFILES
from phrase_annotation import validate_supervisor_review
from publication_review import validate_publication_review
from project_paths import registry_path

COLORS={'red':'rgb(216,57,49)','green':'rgb(46,161,33)','blue':'rgb(36,91,219)','purple':'rgb(100,37,208)','orange':'rgb(222,120,2)','gray':'rgb(143,149,158)','light-yellow':'rgba(255,246,122,0.8)'}
def txt(n):return ''.join(n.itertext())
def nodes(s):return list(ET.fromstring('<root>'+s+'</root>'))
def digest(ns):return hashlib.sha256(json.dumps([canonical(n) for n in ns],ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
def verify(actual,wanted):
 assert len(actual)==len(wanted),(len(actual),len(wanted))
 for a,b in zip(actual,wanted):
  assert effective_signature(a)==effective_signature(b),('character/style mismatch',a.tag,b.tag,txt(a)[:100],txt(b)[:100])
def chapter(snapshot,anchor):
 alln=nodes(snapshot['data']['document']['content']);start=next(i for i,n in enumerate(alln) if n.get('id')==anchor)
 end=next((i for i in range(start+1,len(alln)) if alln[i].tag=='h1'),len(alln));return alln[start:end]
def main():
 parser=argparse.ArgumentParser(description=__doc__)
 for name in ['project','workspace','job','layout']:parser.add_argument('--'+name,required=True,type=pathlib.Path)
 for name in ['materials-url','segments-url']:parser.add_argument('--'+name,required=True)
 parser.add_argument('--layout-profile', choices=LAYOUT_PROFILES); parser.add_argument('--apply',action='store_true');parser.add_argument('--approved-analysis-sha256');parser.add_argument('--approved-manifest-sha256');parser.add_argument('--semantic-review',type=pathlib.Path);parser.add_argument('--ignore-proxy-env',action='store_true');args=parser.parse_args()
 project=args.project.resolve();workspace=args.workspace.resolve();j=args.job.resolve();payload=args.layout.resolve();wanted=nodes(payload.read_text(encoding='utf-8'))
 from render_platform_chapter import validate_layout
 analysis=json.loads((j/'analysis.json').read_text(encoding='utf-8'));manifest=json.loads((j/'script_manifest.json').read_text(encoding='utf-8'));content=json.loads((j/'sections-content.json').read_text(encoding='utf-8'))
 validate_layout(payload.read_text(encoding='utf-8'),analysis,manifest,content,args.materials_url,args.segments_url,layout_profile=args.layout_profile)
 if args.apply:
  assert args.approved_analysis_sha256==hashlib.sha256((j/'analysis.json').read_bytes()).hexdigest() and args.approved_manifest_sha256==hashlib.sha256((j/'script_manifest.json').read_bytes()).hexdigest(),'Exact analysis and manifest approval hashes required'
  assert args.semantic_review and args.semantic_review.is_file(),'v9 needs a separate, source-bound supervising text review'
  review=json.loads(args.semantic_review.read_text(encoding='utf-8-sig'))
  validate_publication_review(review,analysis,args.approved_analysis_sha256,args.approved_manifest_sha256,j/'context-review.json')
 m=json.loads((j/'script_manifest.json').read_text(encoding='utf-8'));vid=m['video_id'];assert txt(wanted[0])==m['title']
 profile=json.loads((project/'config/project.json').read_text(encoding='utf-8-sig'))
 rp=registry_path(project,profile);registry=json.loads(rp.read_text(encoding='utf-8-sig'))
 key=next(k for k,g in registry['groups'].items() if vid in g['videos']);group=registry['groups'][key];v=group['videos'][vid];doc=group['doc_token']
 assert profile['base_id']==registry['base_id']
 a=Lark(registry['base_id'],profile['identity'],workspace,args.ignore_proxy_env);pending=j/'reformat-pending.json';receiptp=j/'reformat-receipt.json'
 assert not pending.exists(),'Pending remote operation: manually reconcile live document before retry'
 receipt=json.loads(receiptp.read_text(encoding='utf-8')) if receiptp.exists() else {'video_id':vid,'completed_groups':[],'payload_sha256':hashlib.sha256(payload.read_bytes()).hexdigest()}
 assert receipt['payload_sha256']==hashlib.sha256(payload.read_bytes()).hexdigest(),'Payload changed during reformat'
 def call(args):
  r=subprocess.run([a.executable,'docs',*args,'--as',profile['identity'],'--format','json'],cwd=workspace,env=a._env(),capture_output=True,text=True,encoding='utf-8',timeout=50)
  d=json.loads(r.stdout or r.stderr);assert r.returncode==0 and d.get('ok'),str(d)[:1300];return d
 def fetch():return call(['+fetch','--doc',doc,'--detail','full','--doc-format','xml'])
 def other_check(s):
  other=copy.deepcopy(registry);del other['groups'][key]['videos'][vid];check(other,s)
 def write(command,anchor,snapshot,content=None,pattern=None):
  args=['+update','--doc',doc,'--command',command,'--revision-id',str(snapshot['data']['document']['revision_id']),'--doc-format','xml']
  if anchor:args+=['--block-id',anchor]
  if content is not None:
   file=j/'reformat-current.xml';file.write_text(content,encoding='utf-8');args+=['--content','@'+str(file.relative_to(workspace))]
  if pattern is not None:args+=['--pattern',pattern]
  return call(args)
 if not args.apply:
  fresh=fetch();check(registry,fresh);print(json.dumps({'status':'live_readonly_reformat_preflight_passed','video_id':vid,'mutations':0}));return
 starts=[i for i,n in enumerate(wanted) if n.tag=='h2' and txt(n).startswith('段')]
 assert len(starts)==len(m['segments'])
 tasks=[('intro',v['chapter_root_block_id'],None,wanted[1:starts[0]])]
 for i,start in enumerate(starts):
  end=starts[i+1] if i+1<len(starts) else len(wanted)
  tasks.append((f'S{i+1:02d}',v['segment_block_ids'][f'S{i+1:02d}'],txt(wanted[start]),wanted[start+1:end]))
 for label,anchor,heading,newbody in tasks:
  if label in receipt['completed_groups']:continue
  before=fetch();check(registry,before);oldchapter=chapter(before,v['chapter_root_block_id'])
  ix=next(i for i,n in enumerate(oldchapter) if n.get('id')==anchor)
  nextanchors=set(v['segment_block_ids'].values())
  end=next((i for i in range(ix+1,len(oldchapter)) if oldchapter[i].get('id') in nextanchors),len(oldchapter))
  oldbody=oldchapter[ix+1:end];assert all(n.tag in {'p','h2'} for n in oldbody),'Cannot discard resource/user blocks'
  atomic_json(j/('reformat-before-'+label+'.json'),before)
  atomic_json(pending,{'video_id':vid,'group':label,'anchor':anchor,'old_body_ids':[n.get('id') for n in oldbody],'expected_payload':str(payload),'stage':'before_write'})
  if heading and txt(oldchapter[ix])!=heading:
   oldtext=txt(oldchapter[ix]);assert sum(txt(n)==oldtext for n in nodes(before['data']['document']['content']))==1
   import html
   write('str_replace',None,before,html.escape(heading),oldtext)
   before=fetch();other_check(before)
   kept=next(n for n in chapter(before,v['chapter_root_block_id']) if n.get('id')==anchor);assert kept.tag=='h2' and txt(kept)==heading
  write('block_insert_after',anchor,before,''.join(ET.tostring(n,encoding='unicode') for n in newbody))
  inserted=fetch();other_check(inserted);current=chapter(inserted,v['chapter_root_block_id']);pos=next(i for i,n in enumerate(current) if n.get('id')==anchor)
  verify(current[pos+1:pos+1+len(newbody)],newbody)
  remaining={n.get('id'):n for n in current}
  assert all(n.get('id') in remaining and canonical(n)==canonical(remaining[n.get('id')]) for n in oldbody),'Existing body changed during insertion'
  atomic_json(pending,{'video_id':vid,'group':label,'anchor':anchor,'old_body_ids':[n.get('id') for n in oldbody],'stage':'insert_verified'})
  for offset in range(0,len(oldbody),80):
   write('block_delete',','.join(n.get('id') for n in oldbody[offset:offset+80]),inserted)
   inserted=fetch();other_check(inserted)
  finalchapter=chapter(inserted,v['chapter_root_block_id']);pos=next(i for i,n in enumerate(finalchapter) if n.get('id')==anchor)
  stop=next((i for i in range(pos+1,len(finalchapter)) if finalchapter[i].get('id') in nextanchors),len(finalchapter))
  verify(finalchapter[pos+1:stop],newbody)
  assert all(x in {n.get('id') for n in finalchapter} for x in [v['chapter_root_block_id'],*v['segment_block_ids'].values()])
  v['content_sha256']=digest(finalchapter);v['layout_status']='reformat_in_progress';registry['updated_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
  check(registry,inserted);atomic_json(rp,registry);receipt['completed_groups'].append(label);atomic_json(receiptp,receipt);pending.unlink()
  print(json.dumps({'video_id':vid,'group':label,'status':'readback_verified_anchors_preserved'}),flush=True)
 final=fetch();check(registry,final);verify(chapter(final,v['chapter_root_block_id']),wanted)
 validate_layout(''.join(ET.tostring(n,encoding='unicode') for n in chapter(final,v['chapter_root_block_id'])),analysis,manifest,content,args.materials_url,args.segments_url,layout_profile=args.layout_profile)
 version='v9' if analysis.get('annotation_profile')=='phrase-functions/v9' else 'v8'
 v['layout_status']='golden_h2_'+version+'_verified';v['approved_analysis_sha256']=hashlib.sha256((j/'analysis.json').read_bytes()).hexdigest();v['annotation_profile']=analysis.get('annotation_profile','legacy/v8');atomic_json(rp,registry)
 atomic_json(j/'docx-after-reformat.json',final);receipt.update(status='golden_h2_'+version+'_live_readback_passed',h1_and_segment_anchors_preserved=True,other_chapters_preserved=True,revision=final['data']['document']['revision_id'],semantic_review_scope='text functions only; source-video/audio truth not inferred');atomic_json(receiptp,receipt)
 print(json.dumps(receipt,ensure_ascii=False))
if __name__=='__main__':main()
