"""Publish one approved fixed-format chapter; default is live read-only preflight.

Requires an existing product/platform Docx registry. Does not create documents,
edit another chapter, or infer content approval. Interrupted writes fail closed.
"""
import argparse,copy,datetime,hashlib,json,pathlib,subprocess,sys,xml.etree.ElementTree as ET
from check_docx_registry import check,canonical
from render_platform_chapter import validate_layout, LAYOUT_PROFILES
from sync_video_base import Lark,atomic_json
from phrase_annotation import PROFILE,validate_supervisor_review
from publication_review import validate_publication_review
from project_paths import registry_path

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def nodes(s):return list(ET.fromstring('<root>'+s+'</root>'))
def main():
 p=argparse.ArgumentParser(description=__doc__)
 for n in ['project','workspace','job','layout']:p.add_argument('--'+n,required=True,type=pathlib.Path)
 for n in ['group-key','materials-url','segments-url']:p.add_argument('--'+n,required=True)
 p.add_argument('--layout-profile', choices=LAYOUT_PROFILES); p.add_argument('--apply',action='store_true');p.add_argument('--approved-analysis-sha256');p.add_argument('--approved-manifest-sha256');p.add_argument('--semantic-review',type=pathlib.Path);p.add_argument('--ignore-proxy-env',action='store_true');args=p.parse_args()
 project=args.project.resolve();workspace=args.workspace.resolve();job=args.job.resolve();payload=args.layout.resolve()
 assert payload.is_relative_to(workspace),'Layout must be inside the CLI workspace for @file'
 profile=read(project/'config/project.json');rp=registry_path(project,profile);registry=read(rp)
 assert profile['base_id']==registry['base_id'];group=registry['groups'][args.group_key];doc=group['doc_token']
 analysis=read(job/'analysis.json');manifest=read(job/'script_manifest.json');content=read(job/'sections-content.json');vid=manifest['video_id']
 xml=payload.read_text(encoding='utf-8');layout=validate_layout(xml,analysis,manifest,content,args.materials_url,args.segments_url,layout_profile=args.layout_profile)
 api=Lark(profile['base_id'],profile['identity'],workspace,args.ignore_proxy_env)
 def call(argv):
  r=subprocess.run([api.executable,'docs',*argv,'--as',profile['identity'],'--format','json'],cwd=workspace,env=api._env(),capture_output=True,text=True,encoding='utf-8',timeout=50)
  d=json.loads(r.stdout or r.stderr);assert r.returncode==0 and d.get('ok'),str(d)[:1200];return d
 before=call(['+fetch','--doc',doc,'--detail','full','--doc-format','xml']);check(registry,before)
 blocks=nodes(before['data']['document']['content']);expected=nodes(xml)
 if vid in group['videos']:
  record=group['videos'][vid];start=next(i for i,n in enumerate(blocks) if n.get('id')==record['chapter_root_block_id']);end=next((i for i in range(start+1,len(blocks)) if blocks[i].tag=='h1'),len(blocks))
  validate_layout(''.join(ET.tostring(n,encoding='unicode') for n in blocks[start:end]),analysis,manifest,content,args.materials_url,args.segments_url,layout_profile=args.layout_profile)
  print(json.dumps({'status':'already_published_layout_verified','video_id':vid,'mutations':0}));return
 assert not any(vid in ''.join(n.itertext()) for n in blocks),'Video already appears outside registry; reconcile before appending'
 pending=job/'publication-pending.json';assert not pending.exists(),'Uncertain prior write: inspect saved pending state and live content, never append again blindly'
 if not args.apply:
  print(json.dumps({'status':'live_readonly_preflight_passed','video_id':vid,'layout':layout,'analysis_sha256':sha(job/'analysis.json'),'manifest_sha256':sha(job/'script_manifest.json'),'mutations':0},ensure_ascii=False));return
 assert args.approved_analysis_sha256==sha(job/'analysis.json') and args.approved_manifest_sha256==sha(job/'script_manifest.json'),'Exact reviewed analysis AND manifest hashes required'
 assert analysis.get('annotation_profile')==PROFILE,'New chapter publication requires explicit v9 phrase annotations'
 assert args.semantic_review and args.semantic_review.is_file(),'Separate supervising text review required'
 validate_publication_review(read(args.semantic_review),analysis,args.approved_analysis_sha256,args.approved_manifest_sha256,job/'context-review.json')
 atomic_json(job/'publication-before.json',before);atomic_json(pending,{'video_id':vid,'payload_sha256':sha(payload),'base_revision':before['data']['document']['revision_id'],'old_block_count':len(blocks)})
 write=call(['+update','--doc',doc,'--command','block_insert_after','--block-id',blocks[-1].get('id'),'--revision-id',str(before['data']['document']['revision_id']),'--content','@'+str(payload.relative_to(workspace))]);atomic_json(job/'publication-write.json',write)
 after=call(['+fetch','--doc',doc,'--detail','full','--doc-format','xml']);check(registry,after);new=nodes(after['data']['document']['content'])[len(blocks):]
 receipt=validate_layout(''.join(ET.tostring(n,encoding='unicode') for n in new),analysis,manifest,content,args.materials_url,args.segments_url,layout_profile=args.layout_profile)
 h2=[n for n in new if n.tag=='h2' and ''.join(n.itertext()).startswith('段')];assert len(h2)==len(manifest['segments'])
 source_url=manifest['source'].get('url') or next(x['url'] for x in analysis['links'] if x['label']=='原视频')
 group['videos'][vid]={'source_title':manifest['title'],'source_url':source_url,'chapter_root_block_id':new[0].get('id'),'segment_block_ids':{f'S{i+1:02d}':n.get('id') for i,n in enumerate(h2)},'content_sha256':hashlib.sha256(json.dumps([canonical(n) for n in new],ensure_ascii=False,separators=(',',':')).encode()).hexdigest(),'source_duration_sec':manifest['duration_sec'],'source_video_sha256':manifest['source']['sha256'],'approved_analysis_sha256':args.approved_analysis_sha256,'layout_status':'golden_h2_v9_verified','annotation_profile':PROFILE}
 registry['updated_at']=datetime.datetime.now(datetime.timezone.utc).isoformat();check(registry,after);atomic_json(rp,registry)
 from urllib.parse import urlsplit
 base=urlsplit(profile['base_url']);url=base.scheme+'://'+base.netloc+'/docx/'+doc
 links={'video_id':vid,'source_sha256':manifest['source']['sha256'],'main_url':url+'#'+new[0].get('id'),'segments':{s['id']:url+'#'+h2[i].get('id') for i,s in enumerate(manifest['segments'])}}
 atomic_json(job/'doc-links.json',links);atomic_json(job/'publication-after.json',after);receipt.update(status='live_layout_readback_verified',source_sha256=manifest['source']['sha256'],other_chapters_preserved=len(group['videos'])-1,main_url=links['main_url']);atomic_json(job/'publication-receipt.json',receipt);pending.rename(job/'publication-applied.json');print(json.dumps(receipt,ensure_ascii=False))
if __name__=='__main__':main()
