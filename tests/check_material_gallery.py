"""Regression: reject uploaded records that are inaccessible or incomplete in the gallery."""
import contextlib,copy,importlib.util,io,json,pathlib,sys,tempfile,unittest
from unittest.mock import patch
project=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(project/'skill/analyze-folder-videos-to-feishu/scripts'))
import verify_material_gallery as module

class GalleryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name);(self.root/'config').mkdir()
  self.profile={'base_id':'base','identity':'bot','tables':{'materials':{'id':'mat'},'videos':{'id':'main'}}}
  self.plan={'base_id':'base','video_id':'BVsample','source_record_id':'recsource','product_record_id':'recproduct','source_sha256':'a'*64,'materials':[{'key':'BVsample:shot1','attachments':[{'field':'素材附件','path':'clip.mp4'}]}],'segments':[{'key':'seg1'}]}
  self.state={'pending':None,'main_fields_verified':True,'records':{'materials':{'BVsample:shot1':{'record_id':'recmaterial'}},'segments':{'seg1':{'record_id':'recsegment'}}}}
  self.row={'record_id':'recmaterial','fields':{'素材ID':'BVsample:shot1','产品编码':'P0001','所属产品':'示例产品','来源平台':'B站','素材附件':[{'name':'clip.mp4'}],'原视频关联':[{'id':'recsource'}],'所属脚本段':[{'id':'recsegment'}]}}
  self.parent={'画面素材':[{'id':'recmaterial'}],'脚本段':[{'id':'recsegment'}],'所属产品':[{'id':'recproduct'}]}
  self.cover='clipfield';self.rows=[self.row]
 def tearDown(self):self.tmp.cleanup()
 def run_case(self):
  outer=self
  class Fake:
   def __init__(self,*a,**kw):pass
   def fields(self,table):return {'素材附件':{'id':'clipfield'}}
   def _call(self,command,table,args):
    if command=='+view-get':return {'view':{'type':'gallery'}},{}
    if command=='+view-get-card':return {'card':{'cover_field':outer.cover}},{}
    if command=='+view-get-filter':return {'filter':{'conditions':[['产品编码','==','P0001']]}},{}
    if command=='+record-list':return {'records':copy.deepcopy(outer.rows),'has_more':False},{}
    raise AssertionError(command)
   def write_json_file(self,value):
    p=outer.root/'filter.json';p.write_text(json.dumps(value));return '@filter.json',p
   def get(self,*args):return {'fields':copy.deepcopy(outer.parent)}
  for path,obj in [('config/project.json',self.profile),('plan.json',self.plan),('state.json',self.state)]:
   (self.root/path).write_text(json.dumps(obj,ensure_ascii=False),encoding='utf-8')
  argv=['verify_material_gallery.py','--project',str(self.root),'--workspace',str(self.root),'--plan',str(self.root/'plan.json'),'--state',str(self.root/'state.json'),'--output',str(self.root/'out.json'),'--view-id','view','--product-code','P0001','--platform','B站']
  with patch.object(module,'Lark',Fake),patch.object(sys,'argv',argv),contextlib.redirect_stdout(io.StringIO()):module.main()
  return json.loads((self.root/'out.json').read_text(encoding='utf-8'))
 def test_complete(self):self.assertEqual(self.run_case()['materials_visible'],1)
 def test_missing_filtered_record(self):
  self.rows=[]
  with self.assertRaisesRegex(AssertionError,'missing from gallery and verified archive'):self.run_case()
 def test_keyframe_cover(self):
  self.cover='keyframefield'
  with self.assertRaisesRegex(AssertionError,'preview video'):self.run_case()
 def test_missing_video(self):
  self.row['fields']['素材附件']=[]
  with self.assertRaisesRegex(AssertionError,'video missing'):self.run_case()
 def test_missing_parent_link(self):
  self.parent['画面素材']=[]
  with self.assertRaises(AssertionError):self.run_case()
 def test_wrong_platform(self):
  self.row['fields']['来源平台']='抖音'
  with self.assertRaisesRegex(AssertionError,'another product/platform'):self.run_case()
 def test_duplicate_key(self):
  self.rows.append(copy.deepcopy(self.row))
  with self.assertRaisesRegex(AssertionError,'Duplicate'):self.run_case()
 def test_missing_semantic_link(self):
  self.row['fields']['所属脚本段']=[]
  with self.assertRaisesRegex(AssertionError,'semantic segment'):self.run_case()
if __name__=='__main__':unittest.main()
