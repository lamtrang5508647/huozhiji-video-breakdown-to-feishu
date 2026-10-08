"""Repair reviewed text paragraphs in place, preserving every heading anchor.

Uses exact current registry guards, source-text comparison, an independent
supervising review, and actual per-character style readback. No media/Base writes.
"""
import argparse,copy,datetime,hashlib,json,pathlib,subprocess,xml.etree.ElementTree as ET
from check_docx_registry import check,canonical
from repair_platform_chapter import chapter,digest,verify
from render_platform_chapter import effective_signature,validate_layout
from phrase_annotation import validate_annotations,validate_supervisor_review
from publication_review import validate_publication_review
from sync_video_base import Lark,atomic_json
def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser(description=__doc__)
 for name in ['project','workspace','job','source-analysis']:p.add_argument('--'+name,required=True,type=pathlib.Path)
 p.add_argument('--apply',action='store_true');p.add_argument('--ignore-proxy-env',action='store_true');args=p.parse_args()
 project=args.project.resolve();workspace=args.workspace.resolve();job=args.job.resolve();ap=job/'analysis.json';mp=job/'script_manifest.json';layout=job/'chapter-golden.xml';a=read(ap);m=read(mp);old=read(args.source_analysis)
 validate_annotations(a,read(job/'sections-content.json') if (job/'sections-content.json').exists() else None)
 source_keys=('id','section_id','source_text','source_shot_ids','locate_ms','raw_item_ids','display_kind','reading_span')
 assert [{k:u.get(k) for k in source_keys} for u in a['units']]==[{k:u.get(k) for k in source_keys} for u in old['units']],'Paragraph repair cannot change source text/provenance'
 validate_publication_review(read(job/'semantic-review.json'),a,sha(ap),sha(mp),job/'context-review.json')
 vid=m['video_id'];assert a.get('video_id',vid)==old.get('video_id',vid)==vid
 wanted=list(ET.fromstring('<r>'+layout.read_text(encoding='utf8')+'</r>'));assert ''.join(wanted[0].itertext())==m['title']
 rp=project/'state/platform-document-registry.json';reg=read(rp);key=next(k for k,g in reg['groups'].items() if vid in g['videos']);g=reg['groups'][key];v=g['videos'][vid];profile=read(project/'config/project.json');api=Lark(profile['base_id'],profile['identity'],workspace,args.ignore_proxy_env)
 def call(argv):
  r=subprocess.run([api.executable,'docs',*argv,'--as',profile['identity'],'--format','json'],cwd=workspace,env=api._env(),capture_output=True,text=True,encoding='utf8',timeout=50);d=json.loads(r.stdout or r.stderr);assert r.returncode==0 and d.get('ok'),str(d)[:1000];return d
 def fetch():return call(['+fetch','--doc',g['doc_token'],'--detail','full','--doc-format','xml'])
 pending=job/'paragraph-repair-pending.json';assert not pending.exists(),'Uncertain prior paragraph update: reconcile its saved live/before state'
 receiptp=job/'paragraph-repair-receipt.json';receipt=read(receiptp) if receiptp.exists() else dict(video_id=vid,payload_sha256=sha(layout),completed_indices=[])
 assert receipt['payload_sha256']==sha(layout),'Payload changed during paragraph repair'
 before=fetch();check(reg,before);current=chapter(before,v['chapter_root_block_id']);assert len(current)==len(wanted),'Cannot insert/remove paragraphs in a color correction'
 for n,new in zip(current,wanted):
  assert n.tag==new.tag,'Block type changed'
  if n.tag!='p':assert effective_signature(n)==effective_signature(new),'Heading/resource changes require another reviewed operation'
 changes=[i for i,(n,new) in enumerate(zip(current,wanted)) if effective_signature(n)!=effective_signature(new)]
 if not args.apply:print(json.dumps(dict(status='live_readonly_paragraph_preflight_passed',video_id=vid,paragraphs=changes,mutations=0)));return
 for index in changes:
  before=fetch();check(reg,before);current=chapter(before,v['chapter_root_block_id']);n=current[index];new=wanted[index];assert n.tag=='p'
  atomic_json(job/('paragraph-before-'+str(index)+'.json'),before);atomic_json(pending,dict(video_id=vid,index=index,old_block_id=n.get('id'),payload_sha256=sha(layout)))
  content=job/'paragraph-current.xml';content.write_text(ET.tostring(new,encoding='unicode'),encoding='utf8')
  call(['+update','--doc',g['doc_token'],'--command','block_replace','--block-id',n.get('id'),'--revision-id',str(before['data']['document']['revision_id']),'--doc-format','xml','--content','@'+str(content.relative_to(workspace))])
  after=fetch();other=copy.deepcopy(reg);del other['groups'][key]['videos'][vid];check(other,after);actual=chapter(after,v['chapter_root_block_id']);assert len(actual)==len(current)
  verify([actual[index]],[new]);assert all(canonical(x)==canonical(actual[i]) for i,x in enumerate(current) if i!=index),'Unrelated paragraph or heading changed'
  v['content_sha256']=digest(actual);reg['updated_at']=datetime.datetime.now(datetime.timezone.utc).isoformat();check(reg,after);atomic_json(rp,reg);receipt['completed_indices'].append(index);atomic_json(receiptp,receipt);pending.unlink()
  print(json.dumps(dict(video_id=vid,paragraph=index,status='actual_character_styles_readback_passed')),flush=True)
 final=fetch();check(reg,final);verify(chapter(final,v['chapter_root_block_id']),wanted)
 v.update(layout_status='golden_h2_v9_verified',annotation_profile='phrase-functions/v9',approved_analysis_sha256=sha(ap));atomic_json(rp,reg)
 receipt.update(status='golden_h2_v9_live_readback_passed',revision=final['data']['document']['revision_id'],all_heading_anchors_preserved=True,other_chapters_preserved=True,source_text_preserved=True);atomic_json(receiptp,receipt);atomic_json(job/'docx-after-reformat.json',final);print(json.dumps(receipt),flush=True)
if __name__=='__main__':main()
