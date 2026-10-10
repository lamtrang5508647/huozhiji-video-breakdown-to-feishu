"""Read-only acceptance of uploaded clips and actual product/platform gallery membership."""
import argparse,hashlib,json,pathlib,sys
from sync_video_base import Lark,atomic_json,decode_records,id_list,cell_text
from source_identity import canonical_schema

def mapped_schema(raw, table):
 return canonical_schema(raw,table["fields"],table["id"]) if table.get("fields") else raw

def field_ref(schema,name):
 return schema.get(name,{}).get("id",name)

def planned_segment_links(plan, state, material_keys):
    segments = {item['key']: item for item in plan['segments']}
    assert len(segments) == len(plan['segments']), 'Duplicate segment plan key'
    journal = state['records']['segments']
    assert set(journal) == set(segments), 'Journaled segment keys do not match plan'
    links = {key: set() for key in material_keys}
    record_ids = set()
    for key, segment in segments.items():
        entry = journal[key]
        record_id = entry['record_id'] if isinstance(entry, dict) else entry
        assert isinstance(record_id, str) and record_id.startswith('rec'), f'Invalid segment record ID: {key}'
        assert record_id not in record_ids, 'Duplicate segment record ID in journal'
        record_ids.add(record_id)
        shot_keys = segment.get('source_shot_keys')
        assert isinstance(shot_keys, list) and shot_keys and all(isinstance(shot, str) for shot in shot_keys), f'Segment {key} must contain source_shot_keys'
        assert len(set(shot_keys)) == len(shot_keys), f'Segment {key} repeats a source shot key'
        assert set(shot_keys) <= material_keys, f'Segment {key} references material keys absent from this plan'
        for shot_key in shot_keys:
            links[shot_key].add(record_id)
    assert all(links.values()), 'Material has no planned semantic segment'
    return links, record_ids

def main():
 p=argparse.ArgumentParser(description=__doc__)
 for n in ['project','workspace','plan','state','output']:p.add_argument('--'+n,required=True,type=pathlib.Path)
 for n in ['view-id','product-code','platform']:p.add_argument('--'+n,required=True)
 p.add_argument('--archive-zone',help='Explicitly accept retained clips hidden by the 选材分区 disjoint filter; verify their attachments and links separately')
 p.add_argument('--ignore-proxy-env',action='store_true');args=p.parse_args()
 profile=json.loads((args.project/'config/project.json').read_text(encoding='utf-8-sig'));plan=json.loads(args.plan.read_text(encoding='utf-8-sig'));state=json.loads(args.state.read_text(encoding='utf-8-sig'))
 assert plan['base_id']==profile['base_id'];assert state.get('pending') is None,'Upload pending; no completion claim'
 expected={x['key']:x for x in plan['materials']};assert len(expected)==len(plan['materials']) and expected
 assert set(state['records']['materials'])==set(expected),'Not all planned material rows are journaled'
 expected_segment_links,planned_segments=planned_segment_links(plan,state,set(expected))
 assert state['main_fields_verified'],'Parent record is not verified'
 a=Lark(profile['base_id'],profile['identity'],args.workspace.resolve(),args.ignore_proxy_env)
 table=profile['tables']['materials']['id'];main=profile['tables']['videos']['id'];schema=mapped_schema(a.fields(table),profile['tables']['materials']);main_schema=mapped_schema(a.fields(main),profile['tables']['videos'])
 view=a._call('+view-get',table,['--view-id',args.view_id])[0]['view'];assert view['type']=='gallery'
 cover=a._call('+view-get-card',table,['--view-id',args.view_id])[0]['card'];assert cover['cover_field']==schema['素材附件']['id'],'Gallery must preview video attachments'
 filter_config=a._call('+view-get-filter',table,['--view-id',args.view_id])[0]['filter'];assert filter_config.get('conditions'),'Refuse unscoped gallery acceptance'
 if args.archive_zone:
  assert '选材分区' in schema and filter_config.get('logic')=='and'
  assert any(c[0] in ['选材分区',schema['选材分区']['id'],schema['选材分区'].get('name')] and c[1]=='disjoint' and c[2]==[args.archive_zone] for c in filter_config['conditions']),'Archive exception must match an explicit live gallery filter'
 rows=[];offset=0;arg,tmp=a.write_json_file(filter_config)
 try:
  while True:
   argv=['--filter-json',arg,'--limit','200','--offset',str(offset)]
   for n in ['素材ID','产品编码','所属产品','来源平台','素材附件','原视频关联','所属脚本段']:argv+=['--field-id',field_ref(schema,n)]
   if args.archive_zone:argv+=['--field-id',field_ref(schema,'选材分区')]
   data,envelope=a._call('+record-list',table,argv);page=decode_records(data,schema);rows+=page
   more=data.get('has_more',envelope.get('has_more'));assert isinstance(more,bool),'Pagination status missing'
   if not more:break
   assert page;offset+=len(page)
 finally:tmp.unlink(missing_ok=True)
 gallery_count=len(rows);archive_record_ids=set()
 if args.archive_zone:
  archive_filter={'logic':'and','conditions':[[field_ref(schema,'产品编码'),'==',args.product_code],[field_ref(schema,'来源平台'),'==',args.platform],[field_ref(schema,'选材分区'),'intersects',[args.archive_zone]]]}
  archive_arg,archive_tmp=a.write_json_file(archive_filter);offset=0
  try:
   while True:
    argv=['--filter-json',archive_arg,'--limit','200','--offset',str(offset)]
    for n in ['素材ID','产品编码','所属产品','来源平台','素材附件','原视频关联','所属脚本段','选材分区']:argv+=['--field-id',field_ref(schema,n)]
    data,envelope=a._call('+record-list',table,argv);page=decode_records(data,schema)
    for row in page:
     assert cell_text(row['fields'].get('选材分区'))==args.archive_zone
     if cell_text(row['fields'].get('素材ID')) in expected:
      assert row['record_id'] not in archive_record_ids
      archive_record_ids.add(row['record_id']);rows.append(row)
    more=data.get('has_more',envelope.get('has_more'));assert isinstance(more,bool)
    if not more:break
    assert page;offset+=len(page)
  finally:archive_tmp.unlink(missing_ok=True)
 found={};archived={}
 for row in rows:
  f=row['fields'];assert cell_text(f.get('产品编码'))==args.product_code and cell_text(f.get('来源平台'))==args.platform,'Gallery exposes another product/platform'
  key=cell_text(f.get('素材ID'))
  if key not in expected:continue
  assert key not in found and key not in archived,'Duplicate material business key in gallery/archive'
  if args.archive_zone:
   assert (cell_text(f.get('选材分区'))==args.archive_zone)==(row['record_id'] in archive_record_ids),'Archived clip leaked into gallery or nonarchive clip hidden'
  assert plan['source_record_id'] in id_list(f.get('原视频关联'),'source link')
  actual_segments=set(id_list(f.get('所属脚本段'),'semantic link'))
  assert actual_segments==expected_segment_links[key],('Material semantic segment links do not match plan',key,sorted(expected_segment_links[key]),sorted(actual_segments))
  attachments=f.get('素材附件');assert isinstance(attachments,list) and attachments,'Actual video missing'
  wanted={pathlib.Path(x['path']).name for x in expected[key]['attachments'] if x['field']=='素材附件'}
  names={x.get('name',x.get('file_name','')) for x in attachments};assert wanted<=names,('Wrong gallery attachment',key,wanted,names)
  entry=state['records']['materials'][key];rid=entry if isinstance(entry,str) else entry['record_id'];assert row['record_id']==rid
  (archived if rid in archive_record_ids else found)[key]=rid
 assert set(found)|set(archived)==set(expected),f'{len(expected)-len(found)-len(archived)} planned materials are missing from gallery and verified archive'
 parent=a.get(main,plan['source_record_id'],['画面素材','脚本段','所属产品'],main_schema)['fields']
 assert set(found.values())|set(archived.values())<=set(id_list(parent.get('画面素材'),'parent material links'))
 assert plan['product_record_id'] in id_list(parent.get('所属产品'),'parent product')
 assert planned_segments<=set(id_list(parent.get('脚本段'),'parent semantic links'))
 result={'status':'live_gallery_membership_passed','video_id':plan['video_id'],'source_sha256':plan['source_sha256'],'plan_sha256':hashlib.sha256(args.plan.read_bytes()).hexdigest(),'view_id':args.view_id,'product_code':args.product_code,'platform':args.platform,'materials_visible':len(found),'materials_archived_verified':len(archived),'archive_zone':args.archive_zone,'semantic_segments_linked':len(planned_segments),'cover_field':cover['cover_field'],'all_gallery_rows':gallery_count,'ui_playback_verified':False,'attachment_remote_bytes_sha256_verified':False}
 atomic_json(args.output,result);print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':
 try:main()
 except (AssertionError,KeyError,ValueError) as e:print(json.dumps({'status':'FAIL','error':str(e)},ensure_ascii=False));sys.exit(1)
